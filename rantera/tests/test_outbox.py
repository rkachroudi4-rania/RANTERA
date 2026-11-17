import hashlib
import hmac
import json
import tempfile
import time
import unittest

from tests.helpers import H, Receiver, crash_batch, make_app


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rx = None

    def tearDown(self):
        if self.rx:
            self.rx.close()
        self.tmp.cleanup()

    def _crash(self, app, seed=1, vehicle="V1", t0=1_700_000_000.0):
        b, _ = crash_batch("frontal", seed=seed, dv=45.0, v0=80.0, vehicle=vehicle, t0=t0)
        return app.test_client().post("/api/telemetry", json=b, headers=H()).get_json()["incidents"][0]

    def test_webhook_delivery_with_valid_signature(self):
        self.rx = Receiver()
        app, ext = make_app(self.tmp.name, webhook_url=self.rx.url, webhook_secret="s3cret")
        inc = self._crash(app)
        res = ext["outbox"].process_due()
        self.assertEqual(res["sent"], 1)
        self.assertEqual(len(self.rx.received), 1)
        headers, body = self.rx.received[0]
        payload = json.loads(body)
        self.assertEqual(payload["incident_id"], inc["incident_id"])
        self.assertEqual(payload["severity"], "elevee")
        expect = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Rantera-Signature"], expect)
        ext["outbox"].process_due()  # pas de double livraison
        self.assertEqual(len(self.rx.received), 1)

    def test_alert_survives_outage_then_delivered_exactly_once(self):
        self.rx = Receiver()
        url = self.rx.url
        self.rx.close()  # panne de la supervision
        app, ext = make_app(self.tmp.name, webhook_url=url, retry_base_s=2.0)
        inc = self._crash(app)
        now = time.time()
        r = ext["outbox"].process_due(now)
        self.assertEqual((r["sent"], r["failed_attempts"]), (0, 1))
        a = app.test_client().get("/api/alerts", headers=H()).get_json()["items"][0]
        self.assertEqual((a["status"], a["attempts"]), ("pending", 1))
        self.assertTrue(a["last_error"])
        # pas de nouvelle tentative avant l'échéance (backoff)
        self.assertEqual(ext["outbox"].process_due(now + 0.5), {"sent": 0, "failed_attempts": 0})
        # la supervision revient sur le même port
        from http.server import HTTPServer
        import threading
        from tests import helpers
        port = int(url.split(":")[2].split("/")[0])
        self.rx = helpers.Receiver.__new__(helpers.Receiver)
        helpers.Receiver.__init__(self.rx)  # port aléatoire : on repointe la config vers lui
        ext["cfg"].webhook_url = self.rx.url
        r = ext["outbox"].process_due(now + 10)
        self.assertEqual(r["sent"], 1)
        self.assertEqual(len(self.rx.received), 1)
        self.assertEqual(json.loads(self.rx.received[0][1])["incident_id"], inc["incident_id"])
        self.assertEqual(ext["outbox"].counts(), {"pending": 0, "sent": 1, "failed": 0})

    def test_http_error_keeps_alert_pending_and_backoff_grows(self):
        self.rx = Receiver(status=500)
        app, ext = make_app(self.tmp.name, webhook_url=self.rx.url, retry_base_s=2.0, retry_max_s=5.0)
        self._crash(app)
        t = time.time()
        for i in range(4):
            t += 100
            ext["outbox"].process_due(t)
        a = app.test_client().get("/api/alerts", headers=H()).get_json()["items"][0]
        self.assertEqual((a["status"], a["attempts"]), ("pending", 4))
        self.assertIn("500", a["last_error"])
        self.assertLessEqual(a["next_attempt_at"] - t, 5.0 + 0.01)  # plafonné

    def test_alert_marked_failed_after_max_attempts_and_manual_retry(self):
        self.rx = Receiver(status=503)
        app, ext = make_app(self.tmp.name, webhook_url=self.rx.url, max_attempts=2)
        self._crash(app)
        t = time.time()
        for _ in range(3):
            t += 1000
            ext["outbox"].process_due(t)
        c = app.test_client()
        a = c.get("/api/alerts", headers=H()).get_json()["items"][0]
        self.assertEqual(a["status"], "failed")  # conservée, visible, jamais supprimée
        self.rx.status = 200
        r = c.post(f"/api/alerts/{a['alert_id']}/retry", headers=H())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c.get("/api/alerts", headers=H()).get_json()["counts"]["sent"], 1)
        self.assertEqual(c.post("/api/alerts/ALR-NOPE/retry", headers=H()).status_code, 404)
        self.assertEqual(c.post(f"/api/alerts/{a['alert_id']}/retry", headers=H()).status_code, 404)  # déjà envoyée

    def test_dashboard_channel_when_no_webhook(self):
        app, ext = make_app(self.tmp.name)
        self._crash(app)
        ext["outbox"].process_due()
        a = app.test_client().get("/api/alerts", headers=H()).get_json()["items"][0]
        self.assertEqual((a["status"], a["channel"]), ("sent", "dashboard"))

    def test_background_thread_delivers(self):
        self.rx = Receiver()
        app, ext = make_app(self.tmp.name, webhook_url=self.rx.url)
        self._crash(app)
        ext["outbox"].start()
        try:
            for _ in range(40):
                if self.rx.received:
                    break
                time.sleep(0.1)
        finally:
            ext["outbox"].stop()
        self.assertEqual(len(self.rx.received), 1)


if __name__ == "__main__":
    unittest.main()
