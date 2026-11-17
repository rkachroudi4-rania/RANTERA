"""Générateur de scénarios de conduite/accident synthétiques.

Les séries produites (vitesse, accélérations, roulis, lacet) servent à :
  - l'entraînement et l'évaluation du module IA,
  - le simulateur Python headless et l'endpoint /api/simulate,
  - les tests automatisés.

Ce sont des modèles simplifiés (impulsion demi-sinusoïdale), PAS des données
réelles : voir docs/LIMITES.md.
"""
from __future__ import annotations

import math
import random

G = 9.81
CRASH_SCENARIOS = ("frontal", "lateral", "rear", "rollover")
NEGATIVE_SCENARIOS = ("cruise", "pothole", "hard_brake")
ALL_SCENARIOS = CRASH_SCENARIOS + NEGATIVE_SCENARIOS
TYPE_OF = {
    "frontal": "collision_frontale",
    "lateral": "collision_laterale",
    "rear": "collision_arriere",
    "rollover": "tonneau",
    "hard_brake": "freinage_brutal",
}
SEVERITY_THRESHOLDS = (22.0, 45.0)  # seuils du score latent « réel » (faible/moyenne/élevée)


def severity_from_latent(score: float) -> str:
    if score < SEVERITY_THRESHOLDS[0]:
        return "faible"
    if score < SEVERITY_THRESHOLDS[1]:
        return "moyenne"
    return "elevee"


def generate_motion(
    scenario: str,
    rng: random.Random,
    hz: int = 50,
    pre_s: float = 6.0,
    post_s: float = 8.0,
    v0: float | None = None,
    dv: float | None = None,
    t0: float = 0.0,
) -> tuple[list[dict], dict]:
    """Retourne (échantillons, vérité terrain). Pas de GPS : voir Route pour positionner."""
    if scenario not in ALL_SCENARIOS:
        raise ValueError(f"scénario inconnu : {scenario}")
    dt = 1.0 / hz
    n = int((pre_s + post_s) * hz)
    t_event = pre_s

    # --- paramètres du scénario -------------------------------------------------
    if v0 is None:
        v0 = {
            "frontal": rng.uniform(25, 110),
            "lateral": rng.uniform(25, 90),
            "rear": rng.uniform(0, 50),
            "rollover": rng.uniform(40, 110),
            "hard_brake": rng.uniform(75, 120),
            "pothole": rng.uniform(30, 80),
            "cruise": rng.uniform(30, 90),
        }[scenario]
    pulse_T = rng.uniform(0.10, 0.18)
    dv_true = 0.0
    if scenario in CRASH_SCENARIOS:
        if dv is None:
            if scenario == "frontal":
                dv = rng.uniform(8, max(9.0, v0 * 0.95))
            elif scenario == "lateral":
                dv = rng.uniform(8, 55)
            elif scenario == "rear":
                dv = rng.uniform(8, 50)
            else:
                dv = rng.uniform(15, 50)
        if scenario == "frontal":
            dv = min(dv, v0)
        dv_true = dv
    amp_g = (dv_true / 3.6) * math.pi / (2 * pulse_T) / G if dv_true else 0.0
    side = rng.choice((-1, 1))
    brake_g = rng.uniform(0.35, 0.8)
    roll_final = side * rng.uniform(90, 180)
    hb_g = rng.uniform(0.65, 0.95)
    hb_drop = rng.uniform(35, min(70.0, max(36.0, v0 - 5)))
    cruise_amp = rng.uniform(1.0, 3.0)  # m/s

    v = v0 / 3.6
    samples: list[dict] = []
    braking_done = False
    for k in range(n):
        tk = k * dt
        local = tk - t_event
        ax = ay = 0.0
        az = 1.0
        roll = 0.0
        yaw = 0.0
        # vitesse de croisière de base (variations douces)
        in_event = scenario != "cruise" and local >= 0
        if not in_event:
            target = v0 / 3.6 + cruise_amp * math.sin(2 * math.pi * tk / 20.0)
            ax = (target - v) / dt / G if k else 0.0
            ax = max(-0.15, min(0.15, ax))
            v = max(0.0, v + ax * G * dt)
        else:
            if scenario in CRASH_SCENARIOS:
                if 0 <= local < pulse_T:
                    a = amp_g * math.sin(math.pi * local / pulse_T)
                    if scenario == "frontal":
                        ax, ay = -a * math.cos(math.radians(rng.uniform(-20, 20))), a * rng.uniform(-0.3, 0.3)
                    elif scenario == "rear":
                        ax, ay = a, a * rng.uniform(-0.2, 0.2)
                    elif scenario == "lateral":
                        ay, ax = side * a, a * rng.uniform(-0.25, 0.25)
                        v = max(0.0, v - 0.0)  # la vitesse longitudinale varie peu
                    else:  # rollover : choc latéral initial (déclenchement du tonneau)
                        ay, ax = side * a, -a * rng.uniform(0.0, 0.3)
                    v = max(0.0, v + ax * G * dt)
                else:  # après l'impulsion : freinage/ralentissement jusqu'à l'arrêt
                    decel = brake_g if v > 0.2 else 0.0
                    if scenario == "rollover":
                        decel = max(decel, 0.7) if v > 0.2 else 0.0
                    ax = -decel
                    v = max(0.0, v - decel * G * dt)
                if scenario == "rollover":
                    prog = max(0.0, min(1.0, local / 1.2))
                    roll = roll_final * (prog * prog * (3 - 2 * prog))
                    if 0 < local < 1.4:
                        az = 1.0 + 1.5 * math.sin(2 * math.pi * local / 0.45)
                        yaw = side * 220 * math.exp(-local)
            elif scenario == "hard_brake":
                dropped = v0 / 3.6 - v
                if not braking_done and dropped * 3.6 < hb_drop and v > 0.5:
                    ax = -hb_g
                    v = max(0.0, v + ax * G * dt)
                else:
                    braking_done = True
                    ax = 0.0
            elif scenario == "pothole":
                if 0 <= local < 0.08:
                    az = 1.0 + 3.5 * math.sin(math.pi * local / 0.08)
                    ax = -0.25 * math.sin(math.pi * local / 0.08)
        # bruit capteur
        ax += rng.gauss(0, 0.03)
        ay += rng.gauss(0, 0.03)
        az += rng.gauss(0, 0.03)
        roll += rng.gauss(0, 0.3)
        yaw += rng.gauss(0, 0.5)
        samples.append({
            "t": round(t0 + tk, 4),
            "speed": round(max(0.0, v * 3.6 + rng.gauss(0, 0.4)), 2),
            "ax": round(ax, 4),
            "ay": round(ay, 4),
            "az": round(az, 4),
            "roll": round(roll, 3),
            "yaw_rate": round(yaw, 3),
        })

    # --- vérité terrain -----------------------------------------------------------
    truth = {
        "scenario": scenario,
        "kind": "accident" if scenario in CRASH_SCENARIOS else ("anomalie" if scenario == "hard_brake" else "aucun"),
        "type": TYPE_OF.get(scenario),
        "severity": None,
        "dv_kmh": round(dv_true, 2),
        "v0_kmh": round(v0, 2),
        "t_event": round(t0 + t_event, 4),
    }
    if scenario in CRASH_SCENARIOS:
        latent = dv_true
        if scenario == "lateral":
            latent *= 1.25
        if scenario == "rollover":
            latent = latent * 1.3 + 0.1 * v0
        latent *= math.exp(rng.gauss(0, 0.18))
        truth["latent"] = round(latent, 2)
        truth["severity"] = severity_from_latent(latent)
    elif scenario == "hard_brake":
        truth["severity"] = "faible"
    return samples, truth


def attach_gps(samples: list[dict], route, start_s: float = 0.0, gps_loss_after: float | None = None) -> list[dict]:
    """Ajoute lat/lon/heading en faisant avancer le véhicule le long de l'itinéraire.

    gps_loss_after : instant (s, relatif au 1er échantillon) après lequel le GPS est perdu.
    """
    s = start_s
    out = []
    prev_t = samples[0]["t"]
    first_t = prev_t
    for smp in samples:
        s += (smp["speed"] / 3.6) * max(0.0, smp["t"] - prev_t)
        prev_t = smp["t"]
        lat, lon, hdg = route.position_at(s)
        d = dict(smp)
        if gps_loss_after is not None and smp["t"] - first_t >= gps_loss_after:
            d["lat"] = d["lon"] = None
        else:
            d["lat"], d["lon"] = round(lat, 6), round(lon, 6)
        d["heading"] = round(hdg, 1)
        out.append(d)
    return out
