import random
import time
import unittest

from rantera.detection import (DetectionConfig, VehicleDetector, classify_type, locate, max_severity,
                               rules_severity)
from rantera.scenarios import CRASH_SCENARIOS, TYPE_OF, generate_motion


def run(samples, det=None):
    det = det or VehicleDetector()
    out = []
    for s in samples:
        out += det.feed(s)
    return out, det


class DetectionTests(unittest.TestCase):
    def test_each_crash_type_is_detected_and_classified(self):
        for sc in CRASH_SCENARIOS:
            ok = 0
            for seed in range(25):
                rng = random.Random(seed)
                samples, truth = generate_motion(sc, rng, pre_s=4, post_s=5, dv=30.0, v0=70.0 if sc != "rear" else 30.0)
                evs, _ = run(samples)
                acc = [e for e in evs if e["kind"] == "accident"]
                self.assertLessEqual(len(acc), 1, f"{sc}: incident dupliqué")
                if acc and acc[0]["type"] == TYPE_OF[sc]:
                    ok += 1
            self.assertEqual(ok, 25, f"{sc}: {ok}/25 correctement détectés (ΔV=30 km/h)")

    def test_no_accident_on_cruise_pothole(self):
        for sc in ("cruise", "pothole"):
            for seed in range(30):
                samples, _ = generate_motion(sc, random.Random(seed), pre_s=4, post_s=5)
                evs, _ = run(samples)
                self.assertEqual(evs, [], f"{sc} seed {seed} a produit {evs[:1]}")

    def test_hard_braking_is_anomaly_not_accident(self):
        for seed in range(30):
            samples, _ = generate_motion("hard_brake", random.Random(seed), pre_s=4, post_s=5)
            evs, _ = run(samples)
            self.assertEqual([e["kind"] for e in evs], ["anomalie"])
            self.assertEqual(evs[0]["type"], "freinage_brutal")

    def test_weak_impact_below_dv_threshold_ignored(self):
        samples, _ = generate_motion("cruise", random.Random(1), pre_s=4, post_s=5)
        samples[250]["ay"] = 5.0  # pic isolé d'un échantillon : ΔV ≈ 3,5 km/h
        evs, _ = run(samples)
        self.assertEqual(evs, [])

    def test_duplicates_and_out_of_order_samples_ignored(self):
        samples, _ = generate_motion("frontal", random.Random(2), pre_s=4, post_s=5, dv=30.0, v0=70.0)
        det = VehicleDetector()
        evs, det = run(samples, det)
        self.assertEqual(len([e for e in evs if e["kind"] == "accident"]), 1)
        again, _ = run(samples, det)  # rejeu complet : rien de nouveau
        self.assertEqual(again, [])

    def test_stale_pending_incident_is_finalized_when_telemetry_stops(self):
        samples, _ = generate_motion("frontal", random.Random(3), pre_s=4, post_s=5, dv=40.0, v0=80.0)
        cut = int(4.5 * 50)  # télémétrie coupée 0,5 s après l'impact
        evs, det = run(samples[:cut])
        self.assertEqual(evs, [])
        self.assertIsNotNone(det.pending)
        self.assertEqual(det.finalize_stale(time.time() + 1), [])  # trop tôt
        late = det.finalize_stale(time.time() + 60)
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["type"], "collision_frontale")

    def test_cooldown_prevents_double_count_of_same_crash(self):
        samples, _ = generate_motion("rollover", random.Random(4), pre_s=4, post_s=10, dv=40.0, v0=90.0)
        evs, _ = run(samples)
        self.assertEqual(len([e for e in evs if e["kind"] == "accident"]), 1)

    def test_severity_rules_boundaries(self):
        base = {"pre_speed": 60, "roll_max": 0, "peak_g": 5, "delta_v": 10}
        self.assertEqual(rules_severity(base, "collision_frontale"), "faible")
        self.assertEqual(rules_severity({**base, "delta_v": 25}, "collision_frontale"), "moyenne")
        self.assertEqual(rules_severity({**base, "delta_v": 45}, "collision_frontale"), "elevee")
        self.assertEqual(rules_severity({**base, "delta_v": 32}, "collision_laterale"), "elevee")  # seuils latéraux plus bas
        self.assertEqual(rules_severity({**base, "delta_v": 32}, "collision_frontale"), "moyenne")
        self.assertEqual(rules_severity({**base, "peak_g": 30}, "collision_arriere"), "elevee")
        self.assertEqual(rules_severity({**base, "roll_max": 120}, "tonneau"), "moyenne")
        self.assertEqual(rules_severity({**base, "roll_max": 120, "pre_speed": 90}, "tonneau"), "elevee")

    def test_max_severity(self):
        self.assertEqual(max_severity("faible", "moyenne"), "moyenne")
        self.assertEqual(max_severity("elevee", "faible"), "elevee")

    def test_type_classification_by_impulse_direction(self):
        cfg = DetectionConfig()
        mk = lambda ang, roll=0: {"impact_angle": ang, "roll_max": roll}
        self.assertEqual(classify_type(mk(170), cfg), "collision_frontale")
        self.assertEqual(classify_type(mk(-170), cfg), "collision_frontale")
        self.assertEqual(classify_type(mk(5), cfg), "collision_arriere")
        self.assertEqual(classify_type(mk(90), cfg), "collision_laterale")
        self.assertEqual(classify_type(mk(-90), cfg), "collision_laterale")
        self.assertEqual(classify_type(mk(170, 100), cfg), "tonneau")

    def test_locate_gps_last_known_unavailable(self):
        w = lambda t, lat: {"t": t, "lat": lat, "lon": 10.0 if lat else None}
        self.assertEqual(locate({"t": 10, "window": [w(9.9, 1.0), w(10.0, 2.0)]}), (2.0, 10.0, "gps"))
        self.assertEqual(locate({"t": 10, "window": [w(5.0, 1.0), w(9.0, None), w(10.5, None)]}), (1.0, 10.0, "last_known"))
        self.assertEqual(locate({"t": 10, "window": [w(9.0, None), w(11.0, 3.0)]}), (3.0, 10.0, "last_known"))
        self.assertEqual(locate({"t": 10, "window": [w(9.0, None)]}), (None, None, "unavailable"))


if __name__ == "__main__":
    unittest.main()
