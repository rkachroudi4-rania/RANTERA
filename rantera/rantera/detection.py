"""Détection d'événements anormaux, classification du type et gravité (règles)."""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

G = 9.81
SEVERITIES = ("faible", "moyenne", "elevee")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
TYPE_CODES = {"collision_frontale": 0, "collision_arriere": 1, "collision_laterale": 2, "tonneau": 3}
FEATURE_NAMES = [
    "pre_speed", "delta_v", "peak_g", "peak_ax", "peak_ay", "peak_az",
    "impact_ms", "roll_max", "post_speed", "yaw_rate_peak", "impact_angle", "jerk_max", "type_code",
]


@dataclass
class DetectionConfig:
    impact_g: float = 3.0          # déclenchement : accélération horizontale (g)
    roll_trigger_deg: float = 45.0  # déclenchement : angle de roulis
    rollover_deg: float = 60.0     # tonneau confirmé
    pre_s: float = 2.0
    post_s: float = 3.0
    keep_s: float = 3.0
    min_delta_v: float = 6.0       # km/h : sous ce seuil, choc ignoré (bosse, nid-de-poule)
    cooldown_s: float = 10.0
    strong_g: float = 1.5          # fenêtre d'intégration du choc
    brake_g: float = 0.55
    brake_min_s: float = 0.6
    brake_min_drop_kmh: float = 25.0
    stale_s: float = 8.0           # secondes réelles avant finalisation d'un incident sans nouvelle télémétrie


def _dts(win: list[dict]) -> list[float]:
    dts = []
    for i, s in enumerate(win):
        if i + 1 < len(win):
            dt = win[i + 1]["t"] - s["t"]
        else:
            dt = dts[-1] if dts else 0.02
        dts.append(min(max(dt, 0.001), 0.1))
    return dts


def extract_features(pre: list[dict], post: list[dict], cfg: DetectionConfig) -> dict:
    t0 = post[0]["t"]
    recent = [x for x in pre if x["t"] >= t0 - 1.0] or pre or post[:1]
    pre_speed = max(x["speed"] for x in recent)
    win = [x for x in pre if x["t"] >= t0 - 0.2] + post
    dts = _dts(win)
    mags = [math.hypot(x["ax"], x["ay"]) for x in win]
    peak_g = max(mags)
    dvx = dvy = 0.0
    impact = 0.0
    for x, dt, m in zip(win, dts, mags):
        if m >= cfg.strong_g:
            dvx += x["ax"] * G * dt
            dvy += x["ay"] * G * dt
            impact += dt
    delta_v = math.hypot(dvx, dvy) * 3.6
    angle = math.degrees(math.atan2(dvy, dvx)) if (dvx or dvy) else 0.0
    last = [x for x in post if x["t"] >= post[-1]["t"] - 1.0]
    jerk = 0.0
    for i in range(1, len(win)):
        dt = max(win[i]["t"] - win[i - 1]["t"], 0.001)
        jerk = max(jerk, abs(mags[i] - mags[i - 1]) / dt)
    return {
        "pre_speed": round(pre_speed, 2),
        "delta_v": round(delta_v, 2),
        "peak_g": round(peak_g, 2),
        "peak_ax": round(max((x["ax"] for x in win), key=abs), 2),
        "peak_ay": round(max((x["ay"] for x in win), key=abs), 2),
        "peak_az": round(max((x["az"] - 1.0 for x in win), key=abs), 2),
        "impact_ms": round(impact * 1000, 1),
        "roll_max": round(max(abs(x["roll"]) for x in post), 1),
        "post_speed": round(sum(x["speed"] for x in last) / len(last), 2),
        "yaw_rate_peak": round(max(abs(x["yaw_rate"]) for x in post), 1),
        "impact_angle": round(angle, 1),
        "jerk_max": round(jerk, 1),
    }


def classify_type(f: dict, cfg: DetectionConfig) -> str:
    if f["roll_max"] >= cfg.rollover_deg:
        return "tonneau"
    a = abs(f["impact_angle"])
    if a >= 135:
        return "collision_frontale"
    if a <= 45:
        return "collision_arriere"
    return "collision_laterale"


def rules_severity(f: dict, incident_type: str) -> str:
    dv, peak = f["delta_v"], f["peak_g"]
    lateral = incident_type == "collision_laterale"
    rollover = incident_type == "tonneau"
    high_dv, mid_dv = (30.0, 15.0) if lateral else (40.0, 20.0)
    if dv >= high_dv or peak >= 25 or (rollover and (f["pre_speed"] >= 70 or dv >= 30)):
        return "elevee"
    if dv >= mid_dv or peak >= 12 or rollover:
        return "moyenne"
    return "faible"


def max_severity(a: str, b: str) -> str:
    return a if SEVERITY_RANK[a] >= SEVERITY_RANK[b] else b


class VehicleDetector:
    """Détecteur à état, un par véhicule, alimenté échantillon par échantillon."""

    def __init__(self, cfg: DetectionConfig | None = None):
        self.cfg = cfg or DetectionConfig()
        self.buf: deque = deque()
        self.pending: dict | None = None
        self.last_t = -math.inf
        self.cooldown_until = -math.inf
        self.brake: dict | None = None
        self.brake_block_until = -math.inf

    # -- API --------------------------------------------------------------------
    def feed(self, s: dict) -> list[dict]:
        cfg, t = self.cfg, s["t"]
        events: list[dict] = []
        if t <= self.last_t:
            return events  # doublon / échantillon hors ordre
        self.last_t = t
        self.buf.append(s)
        while self.buf and self.buf[0]["t"] < t - cfg.keep_s:
            self.buf.popleft()

        if self.pending is not None:
            self.pending["post"].append(s)
            if t - self.pending["t0"] >= cfg.post_s:
                ev = self._finalize()
                if ev:
                    events.append(ev)
            return events
        if t < self.cooldown_until:
            self.brake = None
            return events

        if math.hypot(s["ax"], s["ay"]) >= cfg.impact_g or abs(s["roll"]) >= cfg.roll_trigger_deg:
            pre = [x for x in self.buf if x["t"] < t]
            self.pending = {"t0": t, "pre": pre, "post": [s], "received": time.time()}
            self.brake = None
            return events

        # freinage brutal (anomalie sans choc)
        if s["ax"] <= -cfg.brake_g:
            if self.brake is None:
                self.brake = {"t0": t, "v0": s["speed"], "peak": -s["ax"], "emitted": False}
            else:
                self.brake["peak"] = max(self.brake["peak"], -s["ax"])
                drop = self.brake["v0"] - s["speed"]
                if (not self.brake["emitted"] and t >= self.brake_block_until
                        and t - self.brake["t0"] >= cfg.brake_min_s and drop >= cfg.brake_min_drop_kmh):
                    self.brake["emitted"] = True
                    self.brake_block_until = t + 5.0
                    events.append({
                        "kind": "anomalie", "type": "freinage_brutal", "t": self.brake["t0"],
                        "features": {"pre_speed": round(self.brake["v0"], 2), "speed_drop": round(drop, 2),
                                     "peak_decel_g": round(self.brake["peak"], 2),
                                     "duration_s": round(t - self.brake["t0"], 2)},
                        "window": list(self.buf), "trigger": s,
                    })
        else:
            self.brake = None
        return events

    def finalize_stale(self, now: float | None = None) -> list[dict]:
        """Finalise un incident en attente si la télémétrie s'est arrêtée (véhicule détruit, perte réseau)."""
        now = time.time() if now is None else now
        if self.pending is not None and now - self.pending["received"] >= self.cfg.stale_s:
            ev = self._finalize()
            return [ev] if ev else []
        return []

    # -- interne ----------------------------------------------------------------
    def _finalize(self) -> dict | None:
        p, cfg = self.pending, self.cfg
        self.pending = None
        feats = extract_features(p["pre"], p["post"], cfg)
        if feats["roll_max"] < cfg.rollover_deg and feats["delta_v"] < cfg.min_delta_v:
            return None  # choc trop faible : ignoré
        itype = classify_type(feats, cfg)
        feats["type_code"] = TYPE_CODES[itype]
        self.cooldown_until = p["post"][-1]["t"] + cfg.cooldown_s
        return {
            "kind": "accident", "type": itype, "t": p["t0"], "features": feats,
            "window": p["pre"] + p["post"], "trigger": p["post"][0],
        }


def locate(event: dict) -> tuple[float | None, float | None, str]:
    """Position GPS de l'événement : (lat, lon, source) avec source in gps | last_known | unavailable."""
    t0 = event["t"]
    win = event["window"]
    before = [x for x in win if x["t"] <= t0 and x.get("lat") is not None]
    if before:
        x = before[-1]
        return x["lat"], x["lon"], ("gps" if t0 - x["t"] <= 2.0 else "last_known")
    after = [x for x in win if x["t"] > t0 and x.get("lat") is not None]
    if after:
        return after[0]["lat"], after[0]["lon"], "last_known"
    return None, None, "unavailable"
