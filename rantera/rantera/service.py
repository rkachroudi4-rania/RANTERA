"""Pipeline métier : télémétrie -> détection -> classification -> incident -> alerte."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from .config import Config
from .db import Database
from .detection import (DetectionConfig, VehicleDetector, locate, max_severity, rules_severity)
from .ml import SeverityModel
from .outbox import Outbox
from .schema import iso_utc

log = logging.getLogger("rantera.service")
STATUSES = ("nouveau", "en_cours", "traite", "faux_positif")


def _sample_time_iso(t: float) -> str:
    now = time.time()
    return iso_utc(t) if 946_684_800 < t < now + 86_400 else iso_utc(now)


class Service:
    def __init__(self, cfg: Config, db: Database, model: SeverityModel | None, outbox: Outbox):
        self.cfg, self.db, self.model, self.outbox = cfg, db, model, outbox
        self.det_cfg = DetectionConfig()
        self.detectors: dict[str, VehicleDetector] = {}
        self._meta: dict[str, dict] = {}  # source / scenario_label par véhicule
        self._last_pos_t: dict[str, float] = {}
        self.lock = threading.RLock()

    # -- ingestion ------------------------------------------------------------------
    def ingest(self, vehicle_id: str, samples: list[dict], source: str = "real",
               batch_id: str | None = None, scenario_label: str | None = None) -> dict:
        if batch_id:
            with self.db.conn() as c:
                row = c.execute("SELECT result FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if row:
                res = json.loads(row["result"])
                res["duplicate"] = True
                return res
        with self.lock:
            det = self.detectors.setdefault(vehicle_id, VehicleDetector(self.det_cfg))
            self._meta[vehicle_id] = {"source": source, "scenario_label": scenario_label}
            events, positions = [], []
            for s in sorted(samples, key=lambda x: x["t"]):
                events.extend(det.feed(s))
                if s["lat"] is not None and s["t"] - self._last_pos_t.get(vehicle_id, -1e18) >= 1.0:
                    self._last_pos_t[vehicle_id] = s["t"]
                    positions.append((vehicle_id, s["t"], s["lat"], s["lon"], s["speed"]))
            last = max(samples, key=lambda x: x["t"])
            incidents = []
            with self.db.conn() as c:
                if positions:
                    c.executemany("INSERT INTO positions(vehicle_id,ts,lat,lon,speed) VALUES(?,?,?,?,?)", positions)
                gps = [s for s in samples if s["lat"] is not None]
                lastgps = max(gps, key=lambda x: x["t"]) if gps else None
                c.execute(
                    "INSERT INTO vehicles(vehicle_id,last_seen,last_lat,last_lon,last_speed,last_heading,source) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(vehicle_id) DO UPDATE SET last_seen=excluded.last_seen, "
                    "last_lat=COALESCE(excluded.last_lat,last_lat), last_lon=COALESCE(excluded.last_lon,last_lon), "
                    "last_speed=excluded.last_speed, last_heading=COALESCE(excluded.last_heading,last_heading), source=excluded.source",
                    (vehicle_id, time.time(), lastgps["lat"] if lastgps else None, lastgps["lon"] if lastgps else None,
                     last["speed"], last.get("heading"), source))
                for ev in events:
                    inc = self._record_event(c, vehicle_id, ev)
                    if inc:
                        incidents.append(inc)
                result = {"accepted": len(samples), "duplicate": False, "incidents": incidents,
                          "vehicle": {"vehicle_id": vehicle_id, "speed": last["speed"]}}
                if batch_id:
                    c.execute("INSERT OR IGNORE INTO batches(batch_id,vehicle_id,received_at,result) VALUES(?,?,?,?)",
                              (batch_id, vehicle_id, iso_utc(time.time()), json.dumps(result, ensure_ascii=False)))
        return result

    def finalize_stale(self, now: float | None = None) -> list[dict]:
        out = []
        with self.lock:
            for vid, det in self.detectors.items():
                evs = det.finalize_stale(now)
                if evs:
                    with self.db.conn() as c:
                        for ev in evs:
                            inc = self._record_event(c, vid, ev)
                            if inc:
                                out.append(inc)
        return out

    # -- incidents -------------------------------------------------------------------
    def _record_event(self, conn, vehicle_id: str, ev: dict) -> dict | None:
        meta = self._meta.get(vehicle_id, {"source": "real", "scenario_label": None})
        lat, lon, psrc = locate(ev)
        if lat is None:  # dernier recours : dernière position connue du véhicule
            row = conn.execute("SELECT last_lat,last_lon FROM vehicles WHERE vehicle_id=?", (vehicle_id,)).fetchone()
            if row and row["last_lat"] is not None:
                lat, lon, psrc = row["last_lat"], row["last_lon"], "last_known"
        feats = ev["features"]
        sev_rules = sev_ml = None
        conf = None
        engine = "rules"
        if ev["kind"] == "accident":
            sev_rules = rules_severity(feats, ev["type"])
            severity = sev_rules
            if self.cfg.engine in ("hybrid", "ml") and self.model is not None and self.model.loaded:
                sev_ml, conf, _ = self.model.predict(feats)
                severity = sev_ml if self.cfg.engine == "ml" else max_severity(sev_rules, sev_ml)
                engine = self.cfg.engine
        else:
            severity = "faible"
        incident_id = "INC-" + uuid.uuid4().hex[:10].upper()
        now_iso = iso_utc(time.time())
        gt = meta["scenario_label"]
        cur = conn.execute(
            "INSERT OR IGNORE INTO incidents(incident_id,vehicle_id,kind,type,severity,severity_rules,severity_ml,ml_confidence,"
            "engine,occurred_at,trigger_t,lat,lon,position_source,source,validated,status,features,ground_truth,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'nouveau',?,?,?,?)",
            (incident_id, vehicle_id, ev["kind"], ev["type"], severity, sev_rules, sev_ml, conf, engine,
             _sample_time_iso(ev["t"]), round(ev["t"], 3), lat, lon, psrc, meta["source"],
             json.dumps(feats), gt, now_iso, now_iso))
        if cur.rowcount == 0:
            return None  # déjà enregistré (rejeu de télémétrie)
        conn.execute("INSERT INTO status_history(incident_id,old_status,new_status,note,changed_at) VALUES(?,?,?,?,?)",
                     (incident_id, None, "nouveau", "Créé automatiquement", now_iso))
        alert_id = None
        if ev["kind"] == "accident":
            alert_id = "ALR-" + uuid.uuid4().hex[:10].upper()
            payload = {
                "alert_id": alert_id, "incident_id": incident_id, "vehicle_id": vehicle_id,
                "timestamp": _sample_time_iso(ev["t"]),
                "position": {"lat": lat, "lon": lon, "source": psrc},
                "type": ev["type"], "severity": severity,
                "source": meta["source"], "simulated": meta["source"] == "simulated", "validated": False,
                "engine": engine, "confidence": conf,
            }
            self.outbox.enqueue(conn, alert_id, incident_id, payload)
        log.info("Incident %s : %s / %s (%s)", incident_id, ev["type"], severity, meta["source"])
        return {"incident_id": incident_id, "type": ev["type"], "kind": ev["kind"], "severity": severity,
                "lat": lat, "lon": lon, "position_source": psrc, "alert_id": alert_id,
                "occurred_at": _sample_time_iso(ev["t"]), "source": meta["source"]}

    # -- requêtes ---------------------------------------------------------------------
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["features"] = json.loads(d["features"]) if d.get("features") else {}
        d["validated"] = bool(d["validated"])
        return d

    def list_incidents(self, f: dict, limit: int = 100, offset: int = 0, order: str = "desc") -> dict:
        where, args = self._where(f)
        order = "ASC" if order == "asc" else "DESC"
        with self.db.conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM incidents {where}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM incidents {where} ORDER BY occurred_at {order}, id {order} LIMIT ? OFFSET ?",
                             args + [limit, offset]).fetchall()
        return {"total": total, "items": [self._row(r) for r in rows]}

    @staticmethod
    def _where(f: dict) -> tuple[str, list]:
        cl, args = [], []
        for key in ("type", "severity", "status", "source", "kind", "vehicle_id"):
            if f.get(key):
                cl.append(f"{key}=?")
                args.append(f[key])
        if f.get("date_from"):
            cl.append("occurred_at>=?")
            args.append(f["date_from"] if len(f["date_from"]) > 10 else f["date_from"] + "T00:00:00Z")
        if f.get("date_to"):
            cl.append("occurred_at<=?")
            args.append(f["date_to"] if len(f["date_to"]) > 10 else f["date_to"] + "T23:59:59Z")
        if f.get("has_position") == "1":
            cl.append("lat IS NOT NULL")
        return ("WHERE " + " AND ".join(cl)) if cl else "", args

    def get_incident(self, incident_id: str) -> dict | None:
        with self.db.conn() as c:
            r = c.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
            if not r:
                return None
            d = self._row(r)
            d["history"] = [dict(x) for x in c.execute(
                "SELECT old_status,new_status,note,changed_at FROM status_history WHERE incident_id=? ORDER BY id", (incident_id,))]
            d["alerts"] = [self._alert_row(x) for x in c.execute("SELECT * FROM alerts WHERE incident_id=?", (incident_id,))]
            t = d["trigger_t"]
            d["track"] = [dict(x) for x in c.execute(
                "SELECT ts,lat,lon,speed FROM positions WHERE vehicle_id=? AND ts BETWEEN ? AND ? ORDER BY ts",
                (d["vehicle_id"], t - 60, t + 30))]
        return d

    def update_incident(self, incident_id: str, status: str | None, note: str | None, validated: bool | None) -> dict | None:
        if status is not None and status not in STATUSES:
            raise ValueError(f"statut invalide (valeurs : {', '.join(STATUSES)})")
        now = iso_utc(time.time())
        with self.db.conn() as c:
            r = c.execute("SELECT status, notes FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
            if not r:
                return None
            if status is not None and status != r["status"]:
                c.execute("INSERT INTO status_history(incident_id,old_status,new_status,note,changed_at) VALUES(?,?,?,?,?)",
                          (incident_id, r["status"], status, note, now))
                c.execute("UPDATE incidents SET status=?,updated_at=? WHERE incident_id=?", (status, now, incident_id))
            if note:
                notes = (r["notes"] + "\n" if r["notes"] else "") + f"[{now}] {note}"
                c.execute("UPDATE incidents SET notes=?,updated_at=? WHERE incident_id=?", (notes, now, incident_id))
            if validated is not None:
                c.execute("UPDATE incidents SET validated=?,updated_at=? WHERE incident_id=?", (int(validated), now, incident_id))
        return self.get_incident(incident_id)

    @staticmethod
    def _alert_row(r) -> dict:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        return d

    def list_alerts(self, status: str | None = None, limit: int = 100) -> list[dict]:
        with self.db.conn() as c:
            q = "SELECT * FROM alerts" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC LIMIT ?"
            rows = c.execute(q, ([status] if status else []) + [limit]).fetchall()
        return [self._alert_row(r) for r in rows]

    def list_vehicles(self) -> list[dict]:
        with self.db.conn() as c:
            rows = c.execute("SELECT * FROM vehicles ORDER BY last_seen DESC").fetchall()
        now = time.time()
        out = []
        for r in rows:
            d = dict(r)
            d["age_s"] = round(now - d["last_seen"], 1)
            d["active"] = d["age_s"] < 15
            out.append(d)
        return out

    # -- statistiques --------------------------------------------------------------------
    def stats(self, f: dict) -> dict:
        where, args = self._where(f)
        with self.db.conn() as c:
            def grp(expr, extra=""):
                w = where + (" AND " if where else "WHERE ") + extra if extra else where
                return {r[0]: r[1] for r in c.execute(
                    f"SELECT {expr} k, COUNT(*) n FROM incidents {w} GROUP BY k ORDER BY k", args)}
            total = c.execute(f"SELECT COUNT(*) FROM incidents {where}", args).fetchone()[0]
            by_sev = grp("severity", "kind='accident'")
            by_type = grp("type")
            by_status = grp("status")
            by_source = grp("source")
            by_day = grp("substr(occurred_at,1,10)")
            by_hour = grp("substr(occurred_at,12,2)")
            hot_where = where + (" AND " if where else "WHERE ") + "kind='accident' AND lat IS NOT NULL"
            cells = c.execute(
                f"SELECT ROUND(lat,2) la, ROUND(lon,2) lo, COUNT(*) n, AVG(lat) clat, AVG(lon) clon, "
                f"SUM(CASE severity WHEN 'elevee' THEN 3 WHEN 'moyenne' THEN 2 ELSE 1 END) score "
                f"FROM incidents {hot_where} GROUP BY la, lo ORDER BY score DESC, n DESC LIMIT 10", args).fetchall()
            open_n = c.execute(f"SELECT COUNT(*) FROM incidents {where}" + (" AND " if where else " WHERE ") +
                               "status IN ('nouveau','en_cours')", args).fetchone()[0]
            real_validated = c.execute("SELECT COUNT(*) FROM incidents WHERE source='real' AND validated=1").fetchone()[0]
        sev = {s: by_sev.get(s, 0) for s in ("faible", "moyenne", "elevee")}
        return {
            "total": total, "open": open_n, "accidents": sum(sev.values()), "by_severity": sev, "by_type": by_type,
            "by_status": by_status, "by_source": by_source, "by_day": by_day,
            "by_hour": {f"{h:02d}": by_hour.get(f"{h:02d}", 0) for h in range(24)},
            "hotspots": [{"lat": r["clat"], "lon": r["clon"], "count": r["n"], "score": r["score"]} for r in cells],
            "real_validated_detections": real_validated,
            "outbox": self.outbox.counts(),
            "note": "Les statistiques mêlent accidents simulés et réels : filtrer par source pour les distinguer.",
        }

    def export_rows(self, f: dict) -> list[dict]:
        return self.list_incidents(f, limit=100000, order="asc")["items"]

    def purge(self, older_than_days: float) -> dict:
        cutoff = time.time() - older_than_days * 86400
        with self.db.conn() as c:
            pos = c.execute("DELETE FROM positions WHERE ts<?", (cutoff,)).rowcount
            inc = c.execute("UPDATE incidents SET lat=NULL, lon=NULL, position_source='unavailable' WHERE trigger_t<? AND lat IS NOT NULL",
                            (cutoff,)).rowcount
        return {"positions_deleted": pos, "incidents_anonymized": inc}
