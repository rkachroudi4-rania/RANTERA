"""API REST Flask + service du tableau de bord."""
from __future__ import annotations

import csv
import hmac
import io
import logging
import random
import threading
import time
import uuid

from flask import Flask, Response, jsonify, request, send_from_directory
from pathlib import Path

from . import __version__
from .config import DEFAULT_API_KEY, Config
from .db import Database
from .detection import FEATURE_NAMES
from .ml import SeverityModel
from .outbox import Outbox
from .route import Route
from .scenarios import ALL_SCENARIOS, attach_gps, generate_motion
from .schema import ValidationError, validate_batch
from .service import STATUSES, Service

log = logging.getLogger("rantera.api")
STATIC_DIR = Path(__file__).resolve().parent / "static"
FILTER_KEYS = ("type", "severity", "status", "source", "kind", "vehicle_id", "date_from", "date_to", "has_position")


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or Config.load()
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
    db = Database(cfg.db_path)
    model = SeverityModel()
    if cfg.engine != "rules":
        if not model.load(cfg.model_path):
            log.warning("Modèle IA indisponible (%s) : repli sur les règles. Lancez `python -m rantera train`.", cfg.model_path)
    outbox = Outbox(cfg, db)
    svc = Service(cfg, db, model, outbox)
    route = Route.load()
    app.extensions["rantera"] = {"cfg": cfg, "db": db, "svc": svc, "outbox": outbox, "model": model}

    if cfg.background:
        outbox.start()

        def finalizer():
            while True:
                time.sleep(1.0)
                try:
                    svc.finalize_stale()
                except Exception:
                    log.exception("Erreur de finalisation des incidents")
        threading.Thread(target=finalizer, name="rantera-finalizer", daemon=True).start()

    # -- sécurité ------------------------------------------------------------------
    @app.before_request
    def check_key():
        if not request.path.startswith("/api/") or request.path == "/api/health":
            return None
        key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(key.encode(), cfg.api_key.encode()):
            return jsonify(error="unauthorized", detail="Clé API absente ou invalide (en-tête X-API-Key)"), 401
        return None

    @app.errorhandler(ValidationError)
    def _val(e):
        return jsonify(error="validation_error", detail=str(e)), 400

    @app.errorhandler(404)
    def _404(e):
        return jsonify(error="not_found", detail="Ressource introuvable"), 404

    @app.errorhandler(405)
    def _405(e):
        return jsonify(error="method_not_allowed"), 405

    @app.errorhandler(413)
    def _413(e):
        return jsonify(error="payload_too_large"), 413

    @app.errorhandler(Exception)
    def _500(e):
        log.exception("Erreur interne")
        return jsonify(error="internal_error", detail="Erreur interne du serveur"), 500

    def filters() -> dict:
        return {k: request.args.get(k) for k in FILTER_KEYS if request.args.get(k)}

    def int_arg(name, default, lo, hi):
        try:
            return max(lo, min(hi, int(request.args.get(name, default))))
        except ValueError:
            raise ValidationError(f"{name} doit être un entier")

    # -- pages ---------------------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok" if db.ping() else "degraded", version=__version__, database=db.ping(),
                       model={"loaded": model.loaded, "engine": cfg.engine},
                       alerts=outbox.counts(), webhook_configured=bool(cfg.webhook_url),
                       default_api_key=cfg.api_key == DEFAULT_API_KEY)

    # -- télémétrie ------------------------------------------------------------------
    @app.post("/api/telemetry")
    def telemetry():
        payload = request.get_json(silent=True)
        b = validate_batch(payload, cfg.max_batch_samples)
        return jsonify(svc.ingest(b["vehicle_id"], b["samples"], b["source"], b["batch_id"], b["scenario_label"]))

    @app.get("/api/vehicles")
    def vehicles():
        return jsonify(items=svc.list_vehicles())

    @app.post("/api/simulate")
    def simulate():
        body = request.get_json(silent=True) or {}
        scenario = body.get("scenario", "frontal")
        if scenario not in ALL_SCENARIOS:
            raise ValidationError(f"scénario inconnu. Valeurs : {', '.join(ALL_SCENARIOS)}")
        vid = body.get("vehicle_id") or "SIM-" + uuid.uuid4().hex[:6].upper()
        rng = random.Random()
        kwargs = {}
        for k in ("v0", "dv"):
            if body.get(k) is not None:
                if not isinstance(body[k], (int, float)) or not 0 < body[k] <= 250:
                    raise ValidationError(f"{k} invalide")
                kwargs[k] = float(body[k])
        pre_s, post_s = 4.0, 5.0
        t0 = time.time() - (pre_s + post_s)
        samples, truth = generate_motion(scenario, rng, pre_s=pre_s, post_s=post_s, t0=t0, **kwargs)
        gps_loss = pre_s - 3.0 if body.get("gps_loss") else None
        samples = attach_gps(samples, route, start_s=rng.uniform(0, route.length), gps_loss_after=gps_loss)
        res = svc.ingest(vid, samples, "simulated", None, scenario)
        with svc.lock:
            svc.detectors.pop(vid, None)  # véhicule de test éphémère
        res["truth"] = truth
        if not res["incidents"]:
            res["message"] = "Aucun incident détecté pour ce scénario (normal pour 'cruise' et 'pothole')."
        return jsonify(res)

    # -- incidents ---------------------------------------------------------------------
    @app.get("/api/incidents")
    def incidents():
        return jsonify(svc.list_incidents(filters(), int_arg("limit", 100, 1, 1000), int_arg("offset", 0, 0, 10**9),
                                          request.args.get("order", "desc")))

    @app.get("/api/incidents/<incident_id>")
    def incident(incident_id):
        d = svc.get_incident(incident_id)
        return (jsonify(d), 200) if d else (jsonify(error="not_found"), 404)

    @app.patch("/api/incidents/<incident_id>")
    def patch_incident(incident_id):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValidationError("corps JSON attendu")
        note = body.get("note")
        if note is not None and (not isinstance(note, str) or len(note) > 1000):
            raise ValidationError("note invalide (max 1000 caractères)")
        validated = body.get("validated")
        if validated is not None and not isinstance(validated, bool):
            raise ValidationError("validated doit être booléen")
        try:
            d = svc.update_incident(incident_id, body.get("status"), note, validated)
        except ValueError as exc:
            raise ValidationError(str(exc))
        return (jsonify(d), 200) if d else (jsonify(error="not_found"), 404)

    # -- alertes ---------------------------------------------------------------------
    @app.get("/api/alerts")
    def alerts():
        return jsonify(items=svc.list_alerts(request.args.get("status"), int_arg("limit", 100, 1, 1000)),
                       counts=outbox.counts())

    @app.post("/api/alerts/<alert_id>/retry")
    def alert_retry(alert_id):
        if not outbox.retry_now(alert_id):
            return jsonify(error="not_found", detail="Alerte introuvable ou déjà envoyée"), 404
        return jsonify({**outbox.process_due(), "alert_id": alert_id})

    # -- analyse -----------------------------------------------------------------------
    @app.get("/api/stats")
    def stats():
        return jsonify(svc.stats(filters()))

    @app.get("/api/model")
    def model_info():
        return jsonify(loaded=model.loaded, engine=cfg.engine, metrics=model.metrics)

    def _csv(rows, cols, name):
        def safe(v):
            s = "" if v is None else str(v)
            return "'" + s if s[:1] in ("=", "+", "-", "@") else s
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([safe(r.get(c)) for c in cols])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}"})

    @app.get("/api/export/incidents.csv")
    def export_incidents():
        rows = svc.export_rows(filters())
        for r in rows:
            r.update({f"f_{k}": v for k, v in r["features"].items()})
        cols = ["incident_id", "vehicle_id", "kind", "type", "severity", "severity_rules", "severity_ml", "ml_confidence",
                "occurred_at", "lat", "lon", "position_source", "source", "validated", "status", "ground_truth"]
        return _csv(rows, cols, "incidents.csv")

    @app.get("/api/export/features.csv")
    def export_features():
        """Jeu de données prêt pour des analyses prédictives : caractéristiques + gravité estimée."""
        rows = svc.export_rows(dict(filters(), kind="accident"))
        for r in rows:
            r.update(r["features"])
        cols = FEATURE_NAMES + ["severity", "type", "source", "validated"]
        return _csv(rows, cols, "features.csv")

    @app.post("/api/maintenance/purge")
    def purge():
        body = request.get_json(silent=True) or {}
        days = body.get("older_than_days")
        if not isinstance(days, (int, float)) or days < 0:
            raise ValidationError("older_than_days doit être un nombre >= 0")
        return jsonify(svc.purge(float(days)))

    app.extensions["rantera"]["statuses"] = STATUSES
    return app
