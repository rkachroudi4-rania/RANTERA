"""Accès SQLite (stdlib). Une connexion par opération : sûr entre threads grâce au mode WAL."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles(
  vehicle_id TEXT PRIMARY KEY, last_seen REAL, last_lat REAL, last_lon REAL,
  last_speed REAL, last_heading REAL, source TEXT);
CREATE TABLE IF NOT EXISTS positions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL, ts REAL NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, speed REAL);
CREATE INDEX IF NOT EXISTS idx_pos_vehicle_ts ON positions(vehicle_id, ts);
CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT UNIQUE NOT NULL,
  vehicle_id TEXT NOT NULL,
  kind TEXT NOT NULL,                 -- accident | anomalie
  type TEXT NOT NULL,
  severity TEXT NOT NULL,             -- faible | moyenne | elevee
  severity_rules TEXT, severity_ml TEXT, ml_confidence REAL, engine TEXT,
  occurred_at TEXT NOT NULL,          -- ISO 8601 UTC
  trigger_t REAL NOT NULL,
  lat REAL, lon REAL, position_source TEXT NOT NULL,
  source TEXT NOT NULL,               -- real | simulated
  validated INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'nouveau',
  features TEXT NOT NULL,
  ground_truth TEXT,
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(vehicle_id, trigger_t));
CREATE INDEX IF NOT EXISTS idx_inc_time ON incidents(occurred_at);
CREATE TABLE IF NOT EXISTS status_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL,
  old_status TEXT, new_status TEXT NOT NULL, note TEXT, changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts(
  alert_id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL,               -- pending | sent | failed
  channel TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL,
  last_error TEXT, created_at TEXT NOT NULL, sent_at TEXT);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alerts(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS batches(
  batch_id TEXT PRIMARY KEY, vehicle_id TEXT, received_at TEXT, result TEXT);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def ping(self) -> bool:
        try:
            with self.conn() as c:
                c.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False
