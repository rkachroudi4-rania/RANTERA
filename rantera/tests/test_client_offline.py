import json
import tempfile
import threading
import unittest
from pathlib import Path

from werkzeug.serving import make_server

from rantera.simulator import TelemetryClient
from tests.helpers import KEY, H, crash_batch, make_app


class Live:
    def __init__(self, app, port=0):
        self.srv = make_server("127.0.0.1", port, app, threaded=True)
        self.port = self.srv.server_port
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def stop(self):
        self.srv.shutdown()


class OfflineClientTests(unittest.TestCase):
    def test_batches_buffered_while_offline_and_flushed_in_order_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, ext = make_app(tmp)
            spool = Path(tmp) / "spool.jsonl"
            # 1) API injoignable (port fermé) : tout est conservé, sur disque aussi
            probe = Live(app)
            port = probe.port
            probe.stop()
            c = TelemetryClient(f"http://127.0.0.1:{port}", KEY, str(spool), timeout=1.0)
            b, _ = crash_batch("frontal", seed=40, dv=45.0, v0=80.0)
            samples = b["samples"]
            for i in range(0, len(samples), 50):
                self.assertFalse(c.send("V-OFF", samples[i:i + 50], "simulated", "frontal"))
            n = len(c.queue)
            self.assertEqual(n, 9)
            self.assertFalse(c.online)
            self.assertEqual(len(spool.read_text().splitlines()), n)
            # 2) redémarrage du client : la file est rechargée depuis le disque
            c2 = TelemetryClient(f"http://127.0.0.1:{port}", KEY, str(spool), timeout=1.0)
            self.assertEqual(len(c2.queue), n)
            # 3) l'API revient : vidage dans l'ordre, un seul incident
            live = Live(app, port)
            try:
                self.assertTrue(c2.flush())
                self.assertEqual(len(c2.queue), 0)
                self.assertEqual(spool.read_text(), "")
                self.assertEqual(len(c2.incidents), 1)
                self.assertEqual(c2.incidents[0]["type"], "collision_frontale")
                # 4) réponse perdue : le client renvoie le MÊME lot -> aucun doublon
                lost = c2.queue.copy()
                first = json.loads(json.dumps({"vehicle_id": "V-OFF", "source": "simulated", "samples": samples[:50], "batch_id": "same"}))
                cl = app.test_client()
                cl.post("/api/telemetry", json=first, headers=H())
                r = cl.post("/api/telemetry", json=first, headers=H()).get_json()
                self.assertTrue(r["duplicate"])
                self.assertEqual(cl.get("/api/incidents", headers=H()).get_json()["total"], 1)
            finally:
                live.stop()

    def test_wrong_key_keeps_batches_and_invalid_batch_does_not_block_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _ = make_app(tmp)
            live = Live(app)
            try:
                url = f"http://127.0.0.1:{live.port}"
                good = {"t": 1.7e9, "speed": 50.0, "ax": 0.0, "ay": 0.0}
                # clé erronée (401) : la télémétrie est CONSERVÉE, puis envoyée une fois la clé corrigée
                c = TelemetryClient(url, "wrong", None, timeout=2.0)
                self.assertFalse(c.send("V9", [good]))
                self.assertEqual(len(c.queue), 1)
                c.headers["X-API-Key"] = KEY
                self.assertTrue(c.flush())
                self.assertEqual(len(c.queue), 0)
                # lot réellement invalide (400) : écarté, ne bloque pas les suivants
                ok = TelemetryClient(url, KEY, None, timeout=2.0)
                ok.queue.append({"vehicle_id": "bad id", "samples": [good], "batch_id": "x"})
                self.assertTrue(ok.send("V9", [dict(good, t=1.7e9 + 5)]))
                self.assertEqual(len(ok.queue), 0)
            finally:
                live.stop()

    def test_queue_cap_counts_dropped(self):
        c = TelemetryClient("http://127.0.0.1:1", KEY, None, timeout=0.2, max_queue=3)
        for _ in range(5):
            c.send("V", [{"t": 1.0, "speed": 1, "ax": 0, "ay": 0}])
        self.assertEqual((len(c.queue), c.dropped), (3, 2))


if __name__ == "__main__":
    unittest.main()
