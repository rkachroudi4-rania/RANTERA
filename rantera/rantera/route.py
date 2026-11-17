"""Itinéraire de simulation (polyligne GPS) partagé entre Python et Godot."""
from __future__ import annotations

import json
import math
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
EARTH_M_PER_DEG = 111_320.0


class Route:
    def __init__(self, waypoints: list[tuple[float, float]], loop: bool = True):
        if len(waypoints) < 2:
            raise ValueError("Un itinéraire nécessite au moins 2 points")
        pts = list(waypoints)
        if loop and pts[0] != pts[-1]:
            pts.append(pts[0])
        self.points = pts
        self.loop = loop
        self.lat0, self.lon0 = pts[0]
        self._kx = EARTH_M_PER_DEG * math.cos(math.radians(self.lat0))
        self.xy = [self._to_xy(lat, lon) for lat, lon in pts]
        self.cum = [0.0]
        for (x1, y1), (x2, y2) in zip(self.xy, self.xy[1:]):
            self.cum.append(self.cum[-1] + math.hypot(x2 - x1, y2 - y1))
        self.length = self.cum[-1]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Route":
        path = Path(path) if path else DATA_DIR / "route.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        wps = [(w["lat"], w["lon"]) for w in data["waypoints"]]
        return cls(wps, bool(data.get("loop", True)))

    def _to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self._kx, (lat - self.lat0) * EARTH_M_PER_DEG)

    def position_at(self, s: float) -> tuple[float, float, float]:
        """(lat, lon, cap en degrés depuis le nord) à l'abscisse curviligne s (m)."""
        if self.loop:
            s %= self.length
        else:
            s = max(0.0, min(s, self.length))
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        seg = self.cum[lo + 1] - self.cum[lo]
        f = 0.0 if seg == 0 else (s - self.cum[lo]) / seg
        (x1, y1), (x2, y2) = self.xy[lo], self.xy[lo + 1]
        x, y = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
        heading = (math.degrees(math.atan2(x2 - x1, y2 - y1)) + 360) % 360
        return (self.lat0 + y / EARTH_M_PER_DEG, self.lon0 + x / self._kx, heading)
