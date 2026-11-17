import os
import tempfile
import unittest

from rantera.detection import FEATURE_NAMES, SEVERITIES
from rantera.ml import SeverityModel
from tests.helpers import ensure_model


class MLTests(unittest.TestCase):
    def test_trained_model_loads_predicts_and_beats_chance(self):
        m = SeverityModel()
        self.assertTrue(m.load(ensure_model()))
        self.assertGreater(m.metrics["accuracy"], 0.6)  # 3 classes : hasard = 0,33
        feats = dict(zip(FEATURE_NAMES, [70, 45, 14, -14, 1, 0.5, 150, 0, 0, 5, 175, 900, 0]))
        label, conf, probs = m.predict(feats)
        self.assertIn(label, SEVERITIES)
        self.assertTrue(0 < conf <= 1)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=2)
        self.assertEqual(label, "elevee")
        light = dict(zip(FEATURE_NAMES, [30, 9, 3.2, -3.2, 0.2, 0.1, 140, 0, 20, 3, 178, 200, 0]))
        self.assertEqual(m.predict(light)[0], "faible")

    def test_missing_or_corrupt_model_degrades_gracefully(self):
        m = SeverityModel()
        self.assertFalse(m.load("/nonexistent/model.joblib"))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.joblib")
            with open(p, "wb") as fh:
                fh.write(b"not a model")
            self.assertFalse(m.load(p))
        self.assertFalse(m.loaded)


if __name__ == "__main__":
    unittest.main()
