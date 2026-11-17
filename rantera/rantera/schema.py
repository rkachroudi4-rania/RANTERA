"""Validation des charges utiles de télémétrie."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


class ValidationError(ValueError):
    pass


def _num(d: dict, key: str, lo: float, hi: float, default: Any = ...) -> Any:
    if key not in d or d[key] is None:
        if default is ...:
            raise ValidationError(f"champ obligatoire manquant : {key}")
        return default
    v = d[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValidationError(f"{key} doit être numérique")
    v = float(v)
    if math.isnan(v) or math.isinf(v) or v < lo or v > hi:
        raise ValidationError(f"{key} hors limites [{lo}, {hi}]")
    return v


def validate_sample(s: Any) -> dict:
    if not isinstance(s, dict):
        raise ValidationError("un échantillon doit être un objet JSON")
    out = {
        "t": _num(s, "t", 0, 4_102_444_800),
        "speed": _num(s, "speed", 0, 500),
        "ax": _num(s, "ax", -200, 200),
        "ay": _num(s, "ay", -200, 200),
        "az": _num(s, "az", -200, 200, 1.0),
        "roll": _num(s, "roll", -720, 720, 0.0),
        "yaw_rate": _num(s, "yaw_rate", -5000, 5000, 0.0),
        "heading": _num(s, "heading", 0, 360, None),
    }
    lat = _num(s, "lat", -90, 90, None)
    lon = _num(s, "lon", -180, 180, None)
    if (lat is None) != (lon is None):
        raise ValidationError("lat et lon doivent être fournis ensemble")
    out["lat"], out["lon"] = lat, lon
    return out


def validate_batch(payload: Any, max_samples: int = 1000) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("le corps doit être un objet JSON")
    vid = payload.get("vehicle_id")
    if not isinstance(vid, str) or not (1 <= len(vid) <= 64) or not vid.replace("-", "").replace("_", "").isalnum():
        raise ValidationError("vehicle_id invalide (1-64 caractères alphanumériques, - ou _)")
    source = payload.get("source", "real")
    if source not in ("real", "simulated"):
        raise ValidationError("source doit valoir 'real' ou 'simulated'")
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValidationError("samples doit être une liste non vide")
    if len(samples) > max_samples:
        raise ValidationError(f"trop d'échantillons (max {max_samples})")
    batch_id = payload.get("batch_id")
    if batch_id is not None and (not isinstance(batch_id, str) or not 1 <= len(batch_id) <= 128):
        raise ValidationError("batch_id invalide")
    label = payload.get("scenario_label")
    if label is not None and (not isinstance(label, str) or len(label) > 64):
        raise ValidationError("scenario_label invalide")
    return {
        "vehicle_id": vid,
        "source": source,
        "samples": [validate_sample(s) for s in samples],
        "batch_id": batch_id,
        "scenario_label": label,
    }


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
