"""Configuration centralisée (variables d'environnement + surcharges)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API_KEY = "rantera-dev-key"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    db_path: str = field(default_factory=lambda: _env("RANTERA_DB", str(PROJECT_ROOT / "data" / "rantera.db")))
    model_path: str = field(default_factory=lambda: _env("RANTERA_MODEL", str(PROJECT_ROOT / "data" / "models" / "severity_rf.joblib")))
    api_key: str = field(default_factory=lambda: _env("RANTERA_API_KEY", DEFAULT_API_KEY))
    host: str = field(default_factory=lambda: _env("RANTERA_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("RANTERA_PORT", "8000")))
    # "hybrid" (max règles/IA), "rules" ou "ml"
    engine: str = field(default_factory=lambda: _env("RANTERA_ENGINE", "hybrid"))
    # Interface de supervision externe (optionnelle). Vide = le tableau de bord intégré est la supervision.
    webhook_url: str = field(default_factory=lambda: _env("RANTERA_WEBHOOK_URL", ""))
    webhook_secret: str = field(default_factory=lambda: _env("RANTERA_WEBHOOK_SECRET", ""))
    webhook_timeout_s: float = 4.0
    retry_base_s: float = 2.0
    retry_max_s: float = 300.0
    max_attempts: int = 50
    background: bool = True  # threads d'arrière-plan (outbox, finalisation)
    max_batch_samples: int = 1000

    @classmethod
    def load(cls, **overrides) -> "Config":
        cfg = cls()
        valid = {f.name for f in fields(cls)}
        for key, value in overrides.items():
            if key not in valid:
                raise TypeError(f"Option de configuration inconnue : {key}")
            setattr(cfg, key, value)
        return cfg
