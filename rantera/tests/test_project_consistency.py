import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from rantera.route import Route

ROOT = Path(__file__).resolve().parent.parent
GODOT = ROOT / "godot"


class ProjectTests(unittest.TestCase):
    def test_route_identical_between_python_and_godot(self):
        a = json.loads((ROOT / "rantera/data/route.json").read_text(encoding="utf-8"))
        b = json.loads((GODOT / "data/route.json").read_text(encoding="utf-8"))
        self.assertEqual(a, b)

    def test_route_geometry(self):
        r = Route.load()
        self.assertGreater(r.length, 3000)
        la, lo, hd = r.position_at(0)
        self.assertAlmostEqual(la, r.lat0, 6)
        # bouclage continu
        a, b = r.position_at(r.length - 0.01), r.position_at(r.length + 0.01)
        self.assertLess(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1e-5)
        self.assertTrue(0 <= hd < 360)

    def test_godot_project_files_and_references(self):
        proj = (GODOT / "project.godot").read_text(encoding="utf-8")
        self.assertIn('run/main_scene="res://scenes/Main.tscn"', proj)
        for f in GODOT.rglob("*"):
            if f.suffix in (".gd", ".tscn", ".godot") or f.name == "project.godot":
                for ref in re.findall(r'res://[\w/.\-]+', f.read_text(encoding="utf-8")):
                    self.assertTrue((GODOT / ref[6:]).exists(), f"{f.name} référence {ref} introuvable")

    def test_gdscript_static_sanity(self):
        for f in (GODOT / "scripts").glob("*.gd"):
            t = f.read_text(encoding="utf-8")
            for a, b in ("()", "[]", "{}"):
                self.assertEqual(t.count(a), t.count(b), f"{f.name}: {a}{b} déséquilibrés")
            for n, line in enumerate(t.splitlines(), 1):
                self.assertIsNone(re.match(r"^\t* +\S", line), f"{f.name}:{n} indentation mixte")
            self.assertNotIn("class_name", t)  # preload uniquement : pas d'import d'éditeur requis

    def test_godot_payload_matches_api_schema(self):
        """Les clés émises par vehicle_sim.gd / telemetry_client.gd doivent être celles qu'attend l'API."""
        sim = (GODOT / "scripts/vehicle_sim.gd").read_text(encoding="utf-8")
        for k in ("t", "speed", "ax", "ay", "az", "roll", "yaw_rate", "heading", "lat", "lon"):
            self.assertIn(f'"{k}"', sim, k)
        cli = (GODOT / "scripts/telemetry_client.gd").read_text(encoding="utf-8")
        for k in ("vehicle_id", "source", "samples", "batch_id", "scenario_label", "X-API-Key", "/api/telemetry"):
            self.assertIn(k, cli, k)

    def test_dashboard_assets(self):
        js = (ROOT / "rantera/static/app.js").read_text(encoding="utf-8")
        html = (ROOT / "rantera/static/index.html").read_text(encoding="utf-8")
        dynamic = {"d-close", "d-status", "d-note", "d-save", "d-valid", "d-track"}
        for i in set(re.findall(r'\$\$?\("#([\w-]+)', js)):
            self.assertTrue(f'id="{i}"' in html or i in dynamic, f"id #{i} absent du HTML")
        if shutil.which("node"):
            r = subprocess.run(["node", "--check", str(ROOT / "rantera/static/app.js")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_requirements_importable(self):
        for mod in ("flask", "numpy", "sklearn", "joblib", "requests"):
            __import__(mod)


if __name__ == "__main__":
    unittest.main()
