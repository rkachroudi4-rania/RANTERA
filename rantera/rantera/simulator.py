"""Simulateur de véhicule Python (headless).

Parle exactement le même protocole que la simulation Godot : il sert de client de
référence, de banc de test d'intégration et de solution de repli sans Godot.
Mise en tampon locale : si l'API est injoignable, les lots sont conservés (fichier
de spool JSONL) et renvoyés dans l'ordre à la reconnexion, avec un batch_id stable
(l'API est idempotente : pas de doublon d'incident).
"""
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections import deque
from pathlib import Path

import requests

from .route import Route
from .scenarios import ALL_SCENARIOS, attach_gps, generate_motion

log = logging.getLogger("rantera.sim")


class TelemetryClient:
    def __init__(self, api_url: str, api_key: str, spool_path: str | None = None, timeout: float = 5.0,
                 max_queue: int = 5000):
        self.url = api_url.rstrip("/") + "/api/telemetry"
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self.timeout = timeout
        self.queue: deque = deque()
        self.max_queue = max_queue
        self.dropped = 0
        self.online = True
        self.incidents: list[dict] = []
        self.spool = Path(spool_path) if spool_path else None
        if self.spool and self.spool.exists():
            for line in self.spool.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.queue.append(json.loads(line))

    def _persist(self) -> None:
        if self.spool:
            self.spool.parent.mkdir(parents=True, exist_ok=True)
            self.spool.write_text("".join(json.dumps(b) + "\n" for b in self.queue), encoding="utf-8")

    def send(self, vehicle_id: str, samples: list[dict], source: str = "simulated", label: str | None = None) -> bool:
        """Met le lot en file puis tente de vider la file. Retourne True si la file est vide."""
        self.queue.append({"vehicle_id": vehicle_id, "source": source, "samples": samples,
                           "batch_id": uuid.uuid4().hex, "scenario_label": label})
        while len(self.queue) > self.max_queue:
            self.queue.popleft()
            self.dropped += 1
        return self.flush()

    def flush(self) -> bool:
        while self.queue:
            batch = self.queue[0]
            try:
                r = requests.post(self.url, json=batch, headers=self.headers, timeout=self.timeout)
            except requests.RequestException as exc:
                if self.online:  # journaliser uniquement le changement d'état
                    log.warning("API injoignable (%s) : mise en tampon des lots", type(exc).__name__)
                self.online = False
                self._persist()
                return False
            if not self.online:
                log.info("Connexion rétablie : envoi des %d lot(s) en attente", len(self.queue))
                self.online = True
            if r.status_code == 200:
                self.incidents.extend(r.json().get("incidents", []))
                self.queue.popleft()
            elif r.status_code in (401, 403):
                # clé API erronée : ne JAMAIS jeter la télémétrie (elle peut contenir un accident)
                if self.online or not getattr(self, "_auth_warned", False):
                    log.error("Clé API refusée (HTTP %s) : lots conservés en attente", r.status_code)
                    self._auth_warned = True
                self._persist()
                return False
            elif 400 <= r.status_code < 500 and r.status_code != 429:
                log.error("Lot rejeté définitivement (%s) : %s", r.status_code, r.text[:200])
                self.queue.popleft()  # un lot invalide ne doit pas bloquer la file
            else:
                self._persist()
                return False
        self._persist()
        return True


def run(api_url: str, api_key: str, scenario: str = "frontal", vehicle_id: str = "VEH-001", duration: float = 30.0,
        event_at: float = 10.0, realtime: bool = False, hz: int = 50, batch_s: float = 1.0, seed: int | None = None,
        spool_path: str | None = None, gps_loss: bool = False, source: str = "simulated") -> TelemetryClient:
    if scenario not in ALL_SCENARIOS:
        raise ValueError(f"scénario inconnu : {scenario}")
    rng = random.Random(seed)
    route = Route.load()
    t0 = time.time()
    samples, truth = generate_motion(scenario, rng, hz=hz, pre_s=event_at, post_s=max(1.0, duration - event_at), t0=t0)
    samples = attach_gps(samples, route, gps_loss_after=max(0.0, event_at - 3.0) if gps_loss else None)
    client = TelemetryClient(api_url, api_key, spool_path)
    per = max(1, int(batch_s * hz))
    log.info("Simulation '%s' : %d échantillons, événement à t+%.1fs", scenario, len(samples), event_at)
    for i in range(0, len(samples), per):
        chunk = samples[i:i + per]
        client.send(vehicle_id, chunk, source, scenario)
        if realtime:
            time.sleep(batch_s)
    client.flush()
    return client
