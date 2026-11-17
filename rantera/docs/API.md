# API RANTERA

Toutes les routes `/api/*` (sauf `/api/health`) exigent l'en-tête `X-API-Key`. Erreurs : `{"error": "...", "detail": "..."}`.

## POST /api/telemetry
```json
{"vehicle_id": "VEH-001", "source": "real|simulated", "batch_id": "unique-par-lot (idempotence)",
 "scenario_label": "facultatif", "samples": [
  {"t": 1760000000.02, "speed": 63.4, "ax": -0.02, "ay": 0.01, "az": 1.0, "roll": 0.2,
   "yaw_rate": 0.4, "heading": 92.0, "lat": 36.8065, "lon": 10.1830}]}
```
`t` secondes Unix · `speed` km/h · `ax/ay/az` en g (ax>0 vers l'avant, ay>0 vers la droite, az≈1 au repos) · `roll` degrés ·
`yaw_rate` °/s · `lat/lon` facultatifs mais fournis ensemble. Fréquence recommandée : 50 Hz, lots d'1 s (≤ 1000 échantillons).
Réponse : `{"accepted": n, "duplicate": bool, "incidents": [...]}`. Renvoyer le même `batch_id` ne recrée rien.

## Incidents
`GET /api/incidents?type&severity&status&source&kind&vehicle_id&date_from&date_to&has_position&limit&offset&order`
`GET /api/incidents/<id>` (détail + historique + alertes + trajectoire) · `PATCH /api/incidents/<id>` `{status, note, validated}`
Valeurs : `type` ∈ collision_frontale|collision_laterale|collision_arriere|tonneau|freinage_brutal ·
`severity` ∈ faible|moyenne|elevee · `status` ∈ nouveau|en_cours|traite|faux_positif · `kind` ∈ accident|anomalie.

## Autres
`GET /api/vehicles` · `GET /api/alerts[?status=]` · `POST /api/alerts/<id>/retry` · `GET /api/stats[?filtres]` ·
`GET /api/model` · `GET /api/export/incidents.csv` · `GET /api/export/features.csv` · `POST /api/simulate`
`{"scenario","v0","dv","gps_loss"}` · `POST /api/maintenance/purge` `{"older_than_days"}` · `GET /api/health`.

## Message d'alerte (webhook)
```json
{"alert_id": "ALR-…", "incident_id": "INC-…", "vehicle_id": "VEH-001", "timestamp": "2026-10-28T12:00:00Z",
 "position": {"lat": 36.8, "lon": 10.18, "source": "gps|last_known|unavailable"},
 "type": "collision_frontale", "severity": "elevee", "source": "simulated", "simulated": true,
 "validated": false, "engine": "hybrid", "confidence": 0.94}
```
En-têtes : `X-Rantera-Alert-Id`, `X-Rantera-Signature: sha256=<HMAC-SHA256 du corps>` si un secret est défini.
Le récepteur doit répondre 2xx ; sinon l'alerte reste en file (délais 2 s, 4 s, 8 s… plafonnés à 300 s, 50 tentatives puis
statut « failed » conservé et relançable). La livraison est *au moins une fois* : dédupliquer sur `alert_id`.
