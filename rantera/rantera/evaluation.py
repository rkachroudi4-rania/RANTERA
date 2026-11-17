"""Évaluation reproductible du détecteur et de l'estimation de gravité sur scénarios simulés."""
from __future__ import annotations

import random
from collections import Counter

from .detection import DetectionConfig, VehicleDetector, max_severity, rules_severity, SEVERITIES
from .scenarios import ALL_SCENARIOS, CRASH_SCENARIOS, TYPE_OF, generate_motion


def _cm(pairs, labels):
    m = {a: {b: 0 for b in labels} for a in labels}
    for a, b in pairs:
        if a in m and b in m[a]:
            m[a][b] += 1
    return m


def evaluate(model, n_per_scenario: int = 150, seed: int = 7) -> dict:
    rng = random.Random(seed)
    res = {"seed": seed, "n_per_scenario": n_per_scenario, "scenarios": {}}
    type_pairs, sev_pairs = [], {"rules": [], "ml": [], "hybrid": []}
    for sc in ALL_SCENARIOS:
        detected = false_acc = false_anom = type_ok = 0
        for _ in range(n_per_scenario):
            samples, truth = generate_motion(sc, rng, pre_s=4.0, post_s=5.0)
            det = VehicleDetector(DetectionConfig())
            evs = [e for s in samples for e in det.feed(s)]
            acc = [e for e in evs if e["kind"] == "accident"]
            anom = [e for e in evs if e["kind"] == "anomalie"]
            if sc in CRASH_SCENARIOS:
                if acc:
                    detected += 1
                    e = acc[0]
                    type_pairs.append((truth["type"], e["type"]))
                    type_ok += e["type"] == truth["type"]
                    f = e["features"]
                    r = rules_severity(f, e["type"])
                    sev_pairs["rules"].append((truth["severity"], r))
                    if model is not None and model.loaded:
                        m, _, _ = model.predict(f)
                        sev_pairs["ml"].append((truth["severity"], m))
                        sev_pairs["hybrid"].append((truth["severity"], max_severity(r, m)))
            else:
                false_acc += bool(acc)
                if sc != "hard_brake":
                    false_anom += bool(anom)
                else:
                    detected += bool(anom)
        res["scenarios"][sc] = {
            "n": n_per_scenario, "detected": detected,
            "false_accident_alerts": false_acc, "false_anomaly_events": false_anom, "type_correct": type_ok,
        }
    crash_total = n_per_scenario * len(CRASH_SCENARIOS)
    crash_det = sum(res["scenarios"][s]["detected"] for s in CRASH_SCENARIOS)
    neg = [s for s in ALL_SCENARIOS if s not in CRASH_SCENARIOS]
    neg_total = n_per_scenario * len(neg)
    res["summary"] = {
        "accident_recall": round(crash_det / crash_total, 4),
        "false_accident_rate": round(sum(res["scenarios"][s]["false_accident_alerts"] for s in neg) / neg_total, 4),
        "type_accuracy_when_detected": round(sum(a == b for a, b in type_pairs) / max(1, len(type_pairs)), 4),
        "hard_brake_recall": round(res["scenarios"]["hard_brake"]["detected"] / n_per_scenario, 4),
    }
    res["type_confusion"] = _cm(type_pairs, sorted(set(TYPE_OF[s] for s in CRASH_SCENARIOS)))
    res["severity"] = {}
    for k, pairs in sev_pairs.items():
        if not pairs:
            continue
        acc = sum(a == b for a, b in pairs) / len(pairs)
        under = sum(SEVERITIES.index(b) < SEVERITIES.index(a) for a, b in pairs) / len(pairs)
        over = sum(SEVERITIES.index(b) > SEVERITIES.index(a) for a, b in pairs) / len(pairs)
        res["severity"][k] = {"accuracy": round(acc, 4), "under_estimation": round(under, 4),
                              "over_estimation": round(over, 4), "n": len(pairs),
                              "confusion": _cm(pairs, list(SEVERITIES))}
    # rappel par niveau de gravité réel : où le détecteur échoue-t-il ?
    return res


def to_markdown(r: dict, train_metrics: dict | None = None) -> str:
    L = ["# Rapport d'évaluation RANTERA", "",
         "> Évaluation sur **scénarios simulés** générés par `rantera/scenarios.py` (graine "
         f"{r['seed']}). Ces chiffres ne valent **pas** validation sur accidents réels.", "",
         "## Détection", "", "| Scénario | N | Détectés | Fausses alertes accident | Type correct |", "|---|---|---|---|---|"]
    for sc, v in r["scenarios"].items():
        L.append(f"| {sc} | {v['n']} | {v['detected']} | {v['false_accident_alerts']} | {v['type_correct'] or '-'} |")
    s = r["summary"]
    L += ["", f"- Rappel accidents : **{s['accident_recall']:.1%}**",
          f"- Taux de fausses alertes accident (scénarios sans accident) : **{s['false_accident_rate']:.1%}**",
          f"- Exactitude du type quand détecté : **{s['type_accuracy_when_detected']:.1%}**",
          f"- Rappel freinage brutal : **{s['hard_brake_recall']:.1%}**", "", "## Gravité", "",
          "| Moteur | Exactitude | Sous-estimation | Sur-estimation | N |", "|---|---|---|---|---|"]
    for k, v in r["severity"].items():
        L.append(f"| {k} | {v['accuracy']:.1%} | {v['under_estimation']:.1%} | {v['over_estimation']:.1%} | {v['n']} |")
    if train_metrics:
        L += ["", "## Entraînement du modèle", "", f"- Exactitude test : {train_metrics['accuracy']:.1%} (F1 macro {train_metrics['f1_macro']:.3f})"]
    return "\n".join(L) + "\n"
