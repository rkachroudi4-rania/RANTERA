"""Ligne de commande : serve | train | evaluate | simulate | purge."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import DEFAULT_API_KEY, PROJECT_ROOT, Config


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def cmd_train(args) -> int:
    from .ml import train
    cfg = Config.load()
    m = train(n=args.n, seed=args.seed, model_path=cfg.model_path)
    print(f"Modèle enregistré : {cfg.model_path}")
    print(f"Exactitude (test simulé) : {m['accuracy']:.1%}  F1 macro : {m['f1_macro']:.3f}  "
          f"(entraînement {m['n_train']}, test {m['n_test']})")
    return 0


def cmd_evaluate(args) -> int:
    from .evaluation import evaluate, to_markdown
    from .ml import SeverityModel
    cfg = Config.load()
    model = SeverityModel()
    if not model.load(cfg.model_path):
        print("Modèle absent : exécutez d'abord `python -m rantera train`", file=sys.stderr)
        return 1
    r = evaluate(model, n_per_scenario=args.n, seed=args.seed)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_markdown(r, model.metrics), encoding="utf-8")
    Path(out).with_suffix(".json").write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(r["summary"], indent=2))
    print(f"Rapport écrit : {out}")
    return 0


def cmd_serve(args) -> int:
    _setup_logging(args.verbose)
    overrides = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    cfg = Config.load(**overrides)
    log = logging.getLogger("rantera")
    if cfg.api_key == DEFAULT_API_KEY:
        if cfg.host not in ("127.0.0.1", "localhost", "::1"):
            log.error("Refus de démarrer : clé API par défaut avec écoute réseau (%s). Définissez RANTERA_API_KEY.", cfg.host)
            return 2
        log.warning("Clé API par défaut ('%s') utilisée : acceptable en local uniquement. Définissez RANTERA_API_KEY.", DEFAULT_API_KEY)
    if cfg.engine != "rules" and not Path(cfg.model_path).exists() and not args.no_train:
        log.info("Aucun modèle IA trouvé : entraînement initial sur scénarios simulés (~15 s)...")
        from .ml import train
        m = train(n=3000, seed=42, model_path=cfg.model_path)
        log.info("Modèle entraîné (exactitude test simulé : %.1f%%)", 100 * m["accuracy"])
    from .api import create_app
    app = create_app(cfg)
    log.info("RANTERA %s - tableau de bord : http://%s:%d/", __import__("rantera").__version__, cfg.host, cfg.port)
    try:
        from waitress import serve  # type: ignore
        serve(app, host=cfg.host, port=cfg.port, threads=8)
    except ImportError:
        app.run(host=cfg.host, port=cfg.port, threaded=True, debug=False)
    return 0


def cmd_simulate(args) -> int:
    _setup_logging(args.verbose)
    from . import simulator
    key = args.api_key or Config.load().api_key
    client = simulator.run(args.api_url, key, scenario=args.scenario, vehicle_id=args.vehicle, duration=args.duration,
                           event_at=args.event_at, realtime=args.realtime, seed=args.seed, spool_path=args.spool,
                           gps_loss=args.gps_loss)
    print(f"Lots en attente : {len(client.queue)}")
    for inc in client.incidents:
        print(f"  -> {inc['incident_id']} {inc['type']} gravité={inc['severity']} "
              f"pos=({inc['lat']}, {inc['lon']}) [{inc['position_source']}]")
    if not client.incidents:
        print("  (aucun incident détecté)")
    return 0 if not client.queue else 3


def cmd_purge(args) -> int:
    from .db import Database
    from .outbox import Outbox
    from .service import Service
    cfg = Config.load()
    svc = Service(cfg, Database(cfg.db_path), None, Outbox(cfg, Database(cfg.db_path)))
    print(json.dumps(svc.purge(args.days)))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rantera", description="Plateforme de sécurité routière RANTERA")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="Démarrer l'API + tableau de bord")
    s.add_argument("--host"); s.add_argument("--port", type=int)
    s.add_argument("--no-train", action="store_true", help="Ne pas entraîner le modèle s'il est absent")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_serve)
    t = sub.add_parser("train", help="Entraîner le modèle de gravité sur scénarios simulés")
    t.add_argument("--n", type=int, default=3000); t.add_argument("--seed", type=int, default=42)
    t.set_defaults(fn=cmd_train)
    e = sub.add_parser("evaluate", help="Évaluer détection + gravité (rapport Markdown)")
    e.add_argument("--n", type=int, default=150); e.add_argument("--seed", type=int, default=7)
    e.add_argument("--output", default=str(PROJECT_ROOT / "docs" / "RAPPORT_EVALUATION.md"))
    e.set_defaults(fn=cmd_evaluate)
    m = sub.add_parser("simulate", help="Simulateur de véhicule headless (client de l'API)")
    m.add_argument("--api-url", default="http://127.0.0.1:8000"); m.add_argument("--api-key")
    m.add_argument("--scenario", default="frontal"); m.add_argument("--vehicle", default="VEH-001")
    m.add_argument("--duration", type=float, default=25.0); m.add_argument("--event-at", type=float, default=10.0)
    m.add_argument("--realtime", action="store_true"); m.add_argument("--seed", type=int)
    m.add_argument("--spool", help="Fichier de tampon hors-ligne (JSONL)"); m.add_argument("--gps-loss", action="store_true")
    m.add_argument("-v", "--verbose", action="store_true")
    m.set_defaults(fn=cmd_simulate)
    g = sub.add_parser("purge", help="Effacer les positions anciennes (protection des données de localisation)")
    g.add_argument("--days", type=float, required=True)
    g.set_defaults(fn=cmd_purge)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
