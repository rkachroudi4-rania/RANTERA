import csv
import io
import tempfile
import unittest

from tests.helpers import H, crash_batch, make_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app, self.ext = make_app(self.tmp.name)
        self.c = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, payload):
        return self.c.post("/api/telemetry", json=payload, headers=H())

    # -- sécurité & validation ----------------------------------------------------
    def test_auth_required_except_health_and_ui(self):
        self.assertEqual(self.c.get("/api/health").status_code, 200)
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/static/app.js").status_code, 200)
        self.assertEqual(self.c.get("/api/incidents").status_code, 401)
        self.assertEqual(self.c.get("/api/incidents", headers={"X-API-Key": "wrong"}).status_code, 401)
        self.assertEqual(self.c.post("/api/telemetry", json={}).status_code, 401)
        self.assertEqual(self.c.get("/api/incidents", headers=H()).status_code, 200)

    def test_validation_errors_are_400_with_message(self):
        good = {"t": 1.0, "speed": 10, "ax": 0, "ay": 0}
        cases = [
            (None, "objet JSON"),
            ({"samples": [good]}, "vehicle_id"),
            ({"vehicle_id": "bad id!", "samples": [good]}, "vehicle_id"),
            ({"vehicle_id": "v", "samples": []}, "samples"),
            ({"vehicle_id": "v", "samples": [{"t": 1}]}, "obligatoire"),
            ({"vehicle_id": "v", "samples": [{**good, "speed": "fast"}]}, "numérique"),
            ({"vehicle_id": "v", "samples": [{**good, "lat": 12.0}]}, "ensemble"),
            ({"vehicle_id": "v", "samples": [{**good, "lat": 99.0, "lon": 1.0}]}, "limites"),
            ({"vehicle_id": "v", "samples": [{**good, "ax": float("nan")}]}, None),
            ({"vehicle_id": "v", "source": "x", "samples": [good]}, "source"),
        ]
        for payload, frag in cases:
            r = self.post(payload)
            self.assertEqual(r.status_code, 400, payload)
            if frag:
                self.assertIn(frag, r.get_json()["detail"])
        self.assertEqual(self.c.post("/api/telemetry", data="pas du json", headers=H()).status_code, 400)

    def test_unknown_routes_and_ids(self):
        self.assertEqual(self.c.get("/api/incidents/INC-NOPE", headers=H()).status_code, 404)
        self.assertEqual(self.c.patch("/api/incidents/INC-NOPE", json={"status": "traite"}, headers=H()).status_code, 404)
        self.assertEqual(self.c.get("/api/nope", headers=H()).status_code, 404)
        self.assertEqual(self.c.get("/api/incidents?limit=abc", headers=H()).status_code, 400)

    # -- chaîne complète ----------------------------------------------------------
    def test_simulated_accident_triggers_alert_and_is_stored(self):
        b, truth = crash_batch("frontal", seed=11, dv=45.0, v0=80.0)
        r = self.post(b).get_json()
        self.assertEqual(len(r["incidents"]), 1)
        inc = r["incidents"][0]
        self.assertEqual(inc["type"], "collision_frontale")
        self.assertEqual(inc["severity"], "elevee")
        self.assertEqual(inc["position_source"], "gps")
        self.assertIsNotNone(inc["alert_id"])
        d = self.c.get(f"/api/incidents/{inc['incident_id']}", headers=H()).get_json()
        for k in ("incident_id", "vehicle_id", "occurred_at", "lat", "lon", "type", "severity", "status"):
            self.assertIsNotNone(d[k], k)
        self.assertEqual(d["source"], "simulated")
        self.assertFalse(d["validated"])
        self.assertEqual(d["status"], "nouveau")
        self.assertTrue(d["occurred_at"].startswith("2023-11-14"))
        self.assertGreater(len(d["track"]), 3)
        al = d["alerts"][0]["payload"]
        self.assertEqual({"alert_id", "incident_id", "vehicle_id", "timestamp", "position", "type", "severity"} - set(al), set())
        self.assertTrue(al["simulated"])
        self.assertEqual(d["alerts"][0]["status"], "pending")  # pas encore traité (threads désactivés)
        self.ext["outbox"].process_due()
        self.assertEqual(self.c.get("/api/alerts", headers=H()).get_json()["counts"]["sent"], 1)

    def test_batch_replay_is_idempotent(self):
        b, _ = crash_batch("rear", seed=5, dv=30.0, v0=30.0)
        b["batch_id"] = "batch-1"
        first = self.post(b).get_json()
        second = self.post(b).get_json()
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["incidents"], second["incidents"])
        self.assertEqual(self.c.get("/api/incidents", headers=H()).get_json()["total"], 1)

    def test_same_samples_under_new_batch_id_do_not_duplicate_incident(self):
        b, _ = crash_batch("frontal", seed=6, dv=40.0, v0=70.0)
        self.post(dict(b, batch_id="a"))
        self.ext["svc"].detectors.clear()  # simule un redémarrage serveur : détecteur sans mémoire
        self.post(dict(b, batch_id="b"))
        self.assertEqual(self.c.get("/api/incidents", headers=H()).get_json()["total"], 1)
        self.assertEqual(len(self.c.get("/api/alerts", headers=H()).get_json()["items"]), 1)

    def test_incident_split_across_batches_is_detected(self):
        b, _ = crash_batch("lateral", seed=7, dv=35.0, v0=60.0)
        found = []
        for i in range(0, len(b["samples"]), 50):  # lots d'1 s comme les vrais clients
            r = self.post(dict(b, samples=b["samples"][i:i + 50])).get_json()
            found += r["incidents"]
        self.assertEqual([x["type"] for x in found], ["collision_laterale"])

    def test_negative_scenarios_create_no_accident(self):
        for sc in ("cruise", "pothole"):
            b, _ = crash_batch(sc, seed=3, vehicle="N-" + sc)
            self.assertEqual(self.post(b).get_json()["incidents"], [])
        b, _ = crash_batch("hard_brake", seed=3, vehicle="HB")
        inc = self.post(b).get_json()["incidents"]
        self.assertEqual(inc[0]["kind"], "anomalie")
        self.assertIsNone(inc[0]["alert_id"])  # pas d'alerte de secours pour un simple freinage
        self.assertEqual(self.c.get("/api/alerts", headers=H()).get_json()["items"], [])

    # -- géolocalisation ------------------------------------------------------------
    def test_position_sources(self):
        b, _ = crash_batch("frontal", seed=8, dv=40.0, v0=70.0, vehicle="G1", gps_loss=1.0)  # GPS perdu 3 s avant l'impact
        inc = self.post(b).get_json()["incidents"][0]
        self.assertEqual(inc["position_source"], "last_known")
        self.assertIsNotNone(inc["lat"])
        b, _ = crash_batch("frontal", seed=9, dv=40.0, v0=70.0, vehicle="G2", gps=False)
        inc = self.post(b).get_json()["incidents"][0]
        self.assertEqual(inc["position_source"], "unavailable")
        self.assertIsNone(inc["lat"])
        self.assertEqual(self.c.get("/api/incidents?has_position=1", headers=H()).get_json()["total"], 1)

    # -- historique, filtres, suivi ---------------------------------------------------
    def _seed(self):
        plan = [("frontal", 1_700_000_000.0, 50.0, "V1"), ("rear", 1_700_100_000.0, 12.0, "V2"),
                ("lateral", 1_700_200_000.0, 50.0, "V3"), ("rollover", 1_700_300_000.0, 45.0, "V4")]
        for i, (sc, t0, dv, v) in enumerate(plan):
            b, _ = crash_batch(sc, seed=20 + i, t0=t0, vehicle=v, dv=dv, v0=90.0 if sc != "rear" else 30.0)
            self.post(b)

    def test_filters(self):
        self._seed()
        g = lambda q: self.c.get("/api/incidents?" + q, headers=H()).get_json()
        self.assertEqual(g("")["total"], 4)
        self.assertEqual(g("type=tonneau")["total"], 1)
        self.assertEqual(g("vehicle_id=V2")["items"][0]["type"], "collision_arriere")
        self.assertEqual(g("source=real")["total"], 0)
        self.assertEqual(g("source=simulated")["total"], 4)
        self.assertEqual(g("date_from=2023-11-15&date_to=2023-11-16")["total"], 1)  # 1_700_100_000 = 16 nov. 2023
        self.assertEqual(g("date_to=2023-11-14")["total"], 1)  # borne de fin inclusive
        sev = g("severity=elevee")
        self.assertTrue(all(i["severity"] == "elevee" for i in sev["items"]))
        self.assertGreaterEqual(sev["total"], 2)
        self.assertEqual(len(g("limit=2")["items"]), 2)
        self.assertEqual(g("limit=2&offset=3")["items"].__len__(), 1)
        asc = [i["occurred_at"] for i in g("order=asc")["items"]]
        self.assertEqual(asc, sorted(asc))

    def test_status_workflow_history_and_validation(self):
        b, _ = crash_batch("frontal", seed=30, dv=40.0, v0=70.0)
        iid = self.post(b).get_json()["incidents"][0]["incident_id"]
        url = f"/api/incidents/{iid}"
        r = self.c.patch(url, json={"status": "en_cours", "note": "Équipe envoyée"}, headers=H())
        self.assertEqual(r.get_json()["status"], "en_cours")
        self.c.patch(url, json={"status": "traite"}, headers=H())
        d = self.c.get(url, headers=H()).get_json()
        self.assertEqual([h["new_status"] for h in d["history"]], ["nouveau", "en_cours", "traite"])
        self.assertIn("Équipe envoyée", d["notes"])
        self.assertEqual(self.c.patch(url, json={"status": "inconnu"}, headers=H()).status_code, 400)
        self.assertEqual(self.c.patch(url, json={"validated": "oui"}, headers=H()).status_code, 400)
        self.assertEqual(self.c.patch(url, json={"note": "x" * 2000}, headers=H()).status_code, 400)
        self.assertTrue(self.c.patch(url, json={"validated": True}, headers=H()).get_json()["validated"])

    # -- statistiques & exports ---------------------------------------------------------
    def test_stats_and_hotspots(self):
        self._seed()
        s = self.c.get("/api/stats", headers=H()).get_json()
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["accidents"], 4)
        self.assertEqual(sum(s["by_type"].values()), 4)
        self.assertEqual(sum(s["by_severity"].values()), 4)
        self.assertEqual(len(s["by_day"]), 4)
        self.assertEqual(len(s["by_hour"]), 24)
        self.assertEqual(s["by_source"], {"simulated": 4})
        self.assertGreaterEqual(len(s["hotspots"]), 1)
        self.assertEqual(s["open"], 4)
        self.assertEqual(s["real_validated_detections"], 0)
        s2 = self.c.get("/api/stats?type=tonneau", headers=H()).get_json()
        self.assertEqual(s2["total"], 1)

    def test_csv_exports(self):
        self._seed()
        r = self.c.get("/api/export/incidents.csv", headers=H())
        self.assertEqual(r.mimetype, "text/csv")
        rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
        self.assertEqual(len(rows), 4)
        self.assertIn("severity", rows[0])
        f = list(csv.DictReader(io.StringIO(self.c.get("/api/export/features.csv", headers=H()).get_data(as_text=True))))
        self.assertEqual(len(f), 4)
        self.assertIn("delta_v", f[0])
        self.assertGreater(float(f[0]["delta_v"]), 5)

    def test_csv_injection_is_neutralized(self):
        # vehicle_id est borné à [A-Za-z0-9_-] par la validation ; scenario_label est du texte libre exporté
        bad, _ = crash_batch("frontal", seed=31, dv=40.0, v0=70.0, vehicle="=CMD")
        self.assertEqual(self.post(bad).status_code, 400)
        b, _ = crash_batch("frontal", seed=31, dv=40.0, v0=70.0)
        b["scenario_label"] = "=HYPERLINK(1)"
        self.assertEqual(len(self.post(b).get_json()["incidents"]), 1)
        text = self.c.get("/api/export/incidents.csv", headers=H()).get_data(as_text=True)
        self.assertIn("'=HYPERLINK(1)", text)

    # -- simulation depuis le tableau de bord --------------------------------------------
    def test_simulate_endpoint_all_scenarios(self):
        for sc, expect in (("frontal", 1), ("lateral", 1), ("rear", 1), ("rollover", 1), ("hard_brake", 1), ("pothole", 0), ("cruise", 0)):
            hits = 0
            for _ in range(6):  # le détecteur ne garantit pas 100 % sur ΔV minimal : on exige une majorité
                r = self.c.post("/api/simulate", json={"scenario": sc}, headers=H())
                self.assertEqual(r.status_code, 200)
                hits += len(r.get_json()["incidents"])
            if expect:
                self.assertGreaterEqual(hits, 5, sc)
            else:
                self.assertEqual(hits, 0, sc)
        self.assertEqual(self.c.post("/api/simulate", json={"scenario": "zzz"}, headers=H()).status_code, 400)
        self.assertEqual(self.c.post("/api/simulate", json={"scenario": "frontal", "dv": -5}, headers=H()).status_code, 400)
        r = self.c.post("/api/simulate", json={"scenario": "frontal", "v0": 90, "dv": 50, "gps_loss": True}, headers=H()).get_json()
        self.assertEqual(r["incidents"][0]["position_source"], "last_known")
        # incidents simulés = jamais « réels »
        self.assertEqual(self.c.get("/api/incidents?source=real", headers=H()).get_json()["total"], 0)

    def test_two_quick_simulations_both_register(self):
        a = self.c.post("/api/simulate", json={"scenario": "frontal", "v0": 90, "dv": 50}, headers=H()).get_json()
        b = self.c.post("/api/simulate", json={"scenario": "frontal", "v0": 90, "dv": 50}, headers=H()).get_json()
        self.assertEqual(len(a["incidents"]) + len(b["incidents"]), 2)

    def test_vehicles_and_health(self):
        b, _ = crash_batch("cruise", seed=1, vehicle="VX", t0=1.7e9)
        self.post(b)
        v = self.c.get("/api/vehicles", headers=H()).get_json()["items"]
        self.assertEqual(v[0]["vehicle_id"], "VX")
        self.assertIsNotNone(v[0]["last_lat"])
        h = self.c.get("/api/health").get_json()
        self.assertEqual(h["status"], "ok")
        self.assertTrue(h["model"]["loaded"])

    def test_purge_removes_location_data(self):
        b, _ = crash_batch("frontal", seed=32, dv=40.0, v0=70.0)
        iid = self.post(b).get_json()["incidents"][0]["incident_id"]
        self.assertEqual(self.c.post("/api/maintenance/purge", json={"older_than_days": "x"}, headers=H()).status_code, 400)
        r = self.c.post("/api/maintenance/purge", json={"older_than_days": 30}, headers=H()).get_json()
        self.assertGreater(r["positions_deleted"], 0)
        self.assertEqual(r["incidents_anonymized"], 1)
        d = self.c.get(f"/api/incidents/{iid}", headers=H()).get_json()
        self.assertIsNone(d["lat"])
        self.assertEqual(d["position_source"], "unavailable")
        self.assertEqual(d["track"], [])

    def test_rules_only_engine_when_model_missing(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            app, ext = make_app(tmp.name, model_path="/nonexistent/m.joblib")
            self.assertFalse(ext["model"].loaded)
            b, _ = crash_batch("frontal", seed=33, dv=45.0, v0=80.0)
            inc = app.test_client().post("/api/telemetry", json=b, headers=H()).get_json()["incidents"][0]
            self.assertEqual(inc["severity"], "elevee")
            d = app.test_client().get(f"/api/incidents/{inc['incident_id']}", headers=H()).get_json()
            self.assertEqual(d["engine"], "rules")
            self.assertIsNone(d["severity_ml"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
