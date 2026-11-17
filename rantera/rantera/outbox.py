"""File d'alertes : livraison vers la supervision avec reprise automatique (aucune alerte perdue)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time

import requests

from .config import Config
from .db import Database
from .schema import iso_utc

log = logging.getLogger("rantera.outbox")


class Outbox:
    def __init__(self, cfg: Config, db: Database):
        self.cfg, self.db = cfg, db
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- écriture -----------------------------------------------------------------
    def enqueue(self, conn, alert_id: str, incident_id: str, payload: dict) -> None:
        conn.execute(
            "INSERT INTO alerts(alert_id,incident_id,payload,status,attempts,next_attempt_at,created_at)"
            " VALUES(?,?,?,?,0,?,?)",
            (alert_id, incident_id, json.dumps(payload, ensure_ascii=False), "pending", 0.0, iso_utc(time.time())),
        )

    # -- livraison ----------------------------------------------------------------
    def _deliver(self, payload: dict) -> tuple[bool, str | None, str]:
        if not self.cfg.webhook_url:
            return True, None, "dashboard"  # le tableau de bord intégré consulte la base
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-Rantera-Alert-Id": payload["alert_id"]}
        if self.cfg.webhook_secret:
            sig = hmac.new(self.cfg.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Rantera-Signature"] = "sha256=" + sig
        try:
            r = requests.post(self.cfg.webhook_url, data=body, headers=headers, timeout=self.cfg.webhook_timeout_s)
            if 200 <= r.status_code < 300:
                return True, None, "webhook"
            return False, f"HTTP {r.status_code}", "webhook"
        except requests.RequestException as exc:
            return False, f"{type(exc).__name__}: {exc}"[:300], "webhook"

    def process_due(self, now: float | None = None) -> dict:
        """Tente la livraison des alertes échues. Retourne {'sent': n, 'failed_attempts': m}."""
        now = time.time() if now is None else now
        sent = failed = 0
        with self._lock:
            with self.db.conn() as c:
                rows = c.execute(
                    "SELECT alert_id,payload,attempts FROM alerts WHERE status='pending' AND next_attempt_at<=? "
                    "ORDER BY created_at LIMIT 50", (now,)).fetchall()
            for row in rows:
                ok, err, channel = self._deliver(json.loads(row["payload"]))
                attempts = row["attempts"] + 1
                with self.db.conn() as c:
                    if ok:
                        c.execute("UPDATE alerts SET status='sent',channel=?,attempts=?,last_error=NULL,sent_at=? WHERE alert_id=?",
                                  (channel, attempts, iso_utc(time.time()), row["alert_id"]))
                        sent += 1
                    else:
                        delay = min(self.cfg.retry_base_s * (2 ** (attempts - 1)), self.cfg.retry_max_s)
                        status = "failed" if attempts >= self.cfg.max_attempts else "pending"
                        c.execute("UPDATE alerts SET status=?,channel=?,attempts=?,last_error=?,next_attempt_at=? WHERE alert_id=?",
                                  (status, channel, attempts, err, now + delay, row["alert_id"]))
                        failed += 1
                        log.warning("Alerte %s non livrée (%s), nouvelle tentative dans %.0fs", row["alert_id"], err, delay)
        return {"sent": sent, "failed_attempts": failed}

    def retry_now(self, alert_id: str) -> bool:
        with self.db.conn() as c:
            cur = c.execute("UPDATE alerts SET status='pending', next_attempt_at=0, attempts=CASE WHEN status='failed' THEN 0 ELSE attempts END "
                            "WHERE alert_id=? AND status IN ('pending','failed')", (alert_id,))
            return cur.rowcount > 0

    def counts(self) -> dict:
        with self.db.conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM alerts GROUP BY status").fetchall()
        out = {"pending": 0, "sent": 0, "failed": 0}
        out.update({r["status"]: r["n"] for r in rows})
        return out

    # -- thread -------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(1.0):
                try:
                    self.process_due()
                except Exception:  # ne jamais tuer le thread
                    log.exception("Erreur dans la boucle d'envoi des alertes")

        self._thread = threading.Thread(target=loop, name="rantera-outbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
