"""Module IA : estimation de la gravité par RandomForest entraîné sur scénarios simulés.

IMPORTANT : les étiquettes proviennent du générateur de scénarios (score latent bruité),
pas de dossiers réels d'accidents. Les métriques mesurent donc la capacité à retrouver
la logique du simulateur, pas la validité clinique. Voir docs/LIMITES.md.
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path

from .detection import FEATURE_NAMES, SEVERITIES, DetectionConfig, VehicleDetector
from .scenarios import CRASH_SCENARIOS, generate_motion

log = logging.getLogger("rantera.ml")


def build_dataset(n: int, seed: int) -> tuple[list[list[float]], list[str], dict]:
    """Génère n scénarios d'accident, les passe dans le VRAI détecteur et collecte les caractéristiques."""
    rng = random.Random(seed)
    X, y = [], []
    missed = 0
    for i in range(n):
        sc = CRASH_SCENARIOS[i % len(CRASH_SCENARIOS)]
        samples, truth = generate_motion(sc, rng, pre_s=4.0, post_s=5.0)
        det = VehicleDetector(DetectionConfig())
        ev = [e for s in samples for e in det.feed(s) if e["kind"] == "accident"]
        if not ev:
            missed += 1
            continue
        f = ev[0]["features"]
        X.append([f[k] for k in FEATURE_NAMES])
        y.append(truth["severity"])
    return X, y, {"scenarios": n, "missed_by_detector": missed}


def train(n: int = 3000, seed: int = 42, model_path: str | None = None) -> dict:
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
    from sklearn.model_selection import train_test_split

    X, y, info = build_dataset(n, seed)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=seed, stratify=y)
    clf = RandomForestClassifier(n_estimators=150, min_samples_leaf=2, class_weight="balanced",
                                 random_state=seed, n_jobs=1)
    clf.fit(Xtr, ytr)
    pred = clf.predict(Xte)
    labels = list(SEVERITIES)
    metrics = {
        "trained_on": "scénarios simulés (étiquettes issues du générateur, non réelles)",
        "n_train": len(Xtr), "n_test": len(Xte), "seed": seed, **info,
        "accuracy": round(float(accuracy_score(yte, pred)), 4),
        "f1_macro": round(float(f1_score(yte, pred, average="macro", labels=labels)), 4),
        "labels": labels,
        "confusion_matrix": confusion_matrix(yte, pred, labels=labels).tolist(),
        "feature_importance": {k: round(float(v), 4) for k, v in zip(FEATURE_NAMES, clf.feature_importances_)},
        "features": FEATURE_NAMES,
    }
    if model_path:
        p = Path(model_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": clf, "features": FEATURE_NAMES, "metrics": metrics}, p)
        p.with_suffix(".json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return metrics


class SeverityModel:
    def __init__(self):
        self.clf = None
        self.metrics: dict = {}

    @property
    def loaded(self) -> bool:
        return self.clf is not None

    def load(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        try:
            import joblib
            blob = joblib.load(p)
            if blob.get("features") != FEATURE_NAMES:
                log.warning("Modèle incompatible (caractéristiques différentes) : réentraînement nécessaire")
                return False
            self.clf, self.metrics = blob["model"], blob.get("metrics", {})
            return True
        except Exception as exc:  # version sklearn incompatible, fichier corrompu...
            log.warning("Chargement du modèle impossible (%s) : réentraînement nécessaire", exc)
            self.clf = None
            return False

    def predict(self, features: dict) -> tuple[str, float, dict]:
        vec = [[features[k] for k in FEATURE_NAMES]]
        probs = self.clf.predict_proba(vec)[0]
        classes = list(self.clf.classes_)
        best = max(range(len(classes)), key=lambda i: probs[i])
        return classes[best], float(probs[best]), {c: round(float(p), 3) for c, p in zip(classes, probs)}
