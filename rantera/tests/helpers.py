import os
import random
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from rantera import ml
from rantera.api import create_app
from rantera.config import Config
from rantera.route import Route
from rantera.scenarios import attach_gps, generate_motion

KEY = "test-key"
_MODEL_DIR = tempfile.mkdtemp(prefix="rantera-test-model-")
MODEL_PATH = os.path.join(_MODEL_DIR, "m.joblib")
_trained = False


def ensure_model() -> str:
    global _trained
    if not _trained:
        ml.train(n=700, seed=1, model_path=MODEL_PATH)
        _trained = True
    return MODEL_PATH


def make_app(tmpdir: str, **overrides):
    opts = dict(db_path=os.path.join(tmpdir, "t.db"), model_path=ensure_model(), api_key=KEY, background=False)
    opts.update(overrides)
    cfg = Config.load(**opts)
    app = create_app(cfg)
    return app, app.extensions["rantera"]


def H():
    return {"X-API-Key": KEY}


def crash_batch(scenario="frontal", seed=1, t0=1_700_000_000.0, vehicle="V1", gps=True, gps_loss=None, **kw):
    rng = random.Random(seed)
    samples, truth = generate_motion(scenario, rng, pre_s=4.0, post_s=5.0, t0=t0, **kw)
    if gps:
        samples = attach_gps(samples, Route.load(), start_s=100.0, gps_loss_after=gps_loss)
    else:
        for s in samples:
            s["lat"] = s["lon"] = None
    return {"vehicle_id": vehicle, "source": "simulated", "samples": samples, "scenario_label": scenario}, truth


class Receiver:
    """Vrai serveur HTTP local servant de supervision externe (webhook)."""

    def __init__(self, status=200):
        self.status = status
        self.received = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                outer.received.append((dict(self.headers), body))
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/alerts"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
