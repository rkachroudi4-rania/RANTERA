"""Test bout-en-bout : vrai processus `python -m rantera serve`, vrai client simulateur, vrai webhook."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import requests

from rantera import simulator
from tests.helpers import Receiver, ensure_model

ROOT = Path(__file__).resolve().parent.parent
KEY = "e2e-key"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class E2ETests(unittest.TestCase):
    def start(self, tmp, port, webhook=""):
        env = dict(os.environ, RANTERA_DB=str(Path(tmp) / "e2e.db"), RANTERA_MODEL=ensure_model(),
                   RANTERA_API_KEY=KEY, RANTERA_PORT=str(port), RANTERA_WEBHOOK_URL=webhook, PYTHONPATH=str(ROOT))
        self.proc = subprocess.Popen([sys.executable, "-m", "rantera", "serve"], cwd=ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                if requests.get(f"http://127.0.0.1:{port}/api/health", timeout=1).ok:
                    return
            except requests.RequestException:
                time.sleep(0.25)
        self.fail("le serveur n'a pas démarré")

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)

    def test_full_chain_with_restart(self):
        rx = Receiver()
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        H = {"X-API-Key": KEY}
        with tempfile.TemporaryDirectory() as tmp:
            self.start(tmp, port, rx.url)
            try:
                self.assertIn("RANTERA", requests.get(base + "/").text)
                client = simulator.run(base, KEY, scenario="rollover", vehicle_id="E2E-1", duration=20, event_at=8, seed=2)
                self.assertEqual(len(client.queue), 0)
                self.assertEqual([i["type"] for i in client.incidents], ["tonneau"])
                client = simulator.run(base, KEY, scenario="pothole", vehicle_id="E2E-2", duration=15, event_at=6, seed=3)
                self.assertEqual(client.incidents, [])
                # le thread d'arrière-plan livre l'alerte à la supervision externe
                for _ in range(40):
                    if rx.received:
                        break
                    time.sleep(0.2)
                self.assertEqual(len(rx.received), 1)
                alert = json.loads(rx.received[0][1])
                self.assertEqual(alert["type"], "tonneau")
                self.assertIsNotNone(alert["position"]["lat"])
                inc = requests.get(base + "/api/incidents", headers=H).json()
                self.assertEqual(inc["total"], 1)
                self.assertEqual(requests.get(base + "/api/incidents").status_code, 401)
                iid = inc["items"][0]["incident_id"]
                requests.patch(f"{base}/api/incidents/{iid}", json={"status": "traite"}, headers=H).raise_for_status()
            finally:
                self.stop()
            # redémarrage : l'historique est conservé
            self.start(tmp, port, rx.url)
            try:
                inc = requests.get(base + "/api/incidents", headers=H).json()
                self.assertEqual(inc["total"], 1)
                self.assertEqual(inc["items"][0]["status"], "traite")
                self.assertEqual(requests.get(base + "/api/alerts", headers=H).json()["counts"]["sent"], 1)
                self.assertEqual(len(rx.received), 1)  # pas de renvoi après redémarrage
            finally:
                self.stop()
                rx.close()

    def test_refuses_default_key_on_network_interface(self):
        env = dict(os.environ, RANTERA_API_KEY="rantera-dev-key", RANTERA_HOST="0.0.0.0", PYTHONPATH=str(ROOT))
        r = subprocess.run([sys.executable, "-m", "rantera", "serve", "--no-train"], cwd=ROOT, env=env,
                           capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
