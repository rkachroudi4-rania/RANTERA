# RANTERA — plateforme de sécurité routière (prototype expérimental)

Détection d'accidents à partir de télémétrie véhicule, classification du type, estimation de la gravité
(règles + IA), géolocalisation, alertes avec reprise, tableau de bord de supervision, historique et statistiques.

> **RANTERA est un prototype.** Tout ce qui a été mesuré l'a été sur des **scénarios simulés**.
> La détection sur accidents réels, la précision de la gravité et l'envoi effectif des alertes
> doivent être validés avant tout usage opérationnel. Voir [docs/LIMITES.md](docs/LIMITES.md).

## Architecture

```
 Godot (godot/)  ──┐                       ┌─> SQLite (incidents, positions, alertes, historique)
 Simulateur Python ├─ HTTP/JSON ─> API Flask ─┤─> Détection (règles) + IA (RandomForest) -> gravité
 (rantera/simulator.py)  POST /api/telemetry  └─> File d'alertes ─> webhook de supervision (optionnel)
                                      │
                              Tableau de bord web  http://127.0.0.1:8000/
```

| Exigence du cahier des charges | Où |
|---|---|
| Simulation véhicule + accidents (Godot) | `godot/` (Godot 4.2+) et `rantera/simulator.py` (équivalent headless) |
| Détection / classification / gravité | `rantera/detection.py`, `rantera/ml.py`, `rantera/scenarios.py` |
| Communication Godot ↔ Python | API REST JSON `POST /api/telemetry` (lots d'1 s, idempotents) |
| Géolocalisation | GPS par échantillon ; repli « dernière position connue » ; sinon « indisponible » |
| Alertes + file en cas de perte de connexion | `rantera/outbox.py` (côté serveur), tampon dans les clients Godot et Python |
| Tableau de bord, historique, filtres, carte | `rantera/static/` |
| Stockage / historique / statistiques / export | `rantera/db.py`, `rantera/service.py`, `/api/stats`, `/api/export/*.csv` |
| Distinction simulé / réel | champ `source` (`simulated`/`real`), badge dans l'UI, `validated` réservé aux incidents réels |

## Installation et lancement

Prérequis : Python ≥ 3.10. (Godot 4.2+ uniquement pour la simulation graphique.)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python -m rantera serve
```

Au premier démarrage, le modèle IA est entraîné automatiquement (~15 s) dans `data/models/`.
Ouvrez ensuite <http://127.0.0.1:8000/>, saisissez la clé API **`rantera-dev-key`** (valeur par défaut, usage local
uniquement), puis cliquez sur **Simuler un accident**.

Simulateur sans Godot, dans un autre terminal :

```bash
python -m rantera simulate --scenario frontal --duration 25          # frontal|lateral|rear|rollover|hard_brake|pothole|cruise
python -m rantera simulate --scenario lateral --gps-loss --realtime  # perte GPS avant l'impact, en temps réel
```

### Simulation Godot

1. Installer Godot 4.2+ ; ouvrir le dossier `godot/` (ou `godot --path godot`).
2. Vérifier `godot/config.json` (`api_url`, `api_key`, `vehicle_id`).
3. Lancer l'API (`python -m rantera serve`) puis la scène. Touches : ↑/↓ vitesse, **1** frontal, **2** latéral,
   **3** arrière, **4** tonneau, **5** freinage brutal, **6** nid-de-poule, **G** perte GPS, **R** réinitialiser.
4. Vérification sans interface : `RANTERA_AUTORUN=frontal godot --headless --path godot`
   (code de sortie 0 si l'incident a été reçu de l'API).

> ⚠️ La partie Godot n'a **pas pu être exécutée** lors du développement (Godot absent de l'environnement).
> Elle est vérifiée statiquement uniquement (voir « Tests »). Attendez-vous à devoir corriger d'éventuelles erreurs
> de syntaxe au premier lancement ; le contrat réseau est, lui, testé côté serveur.

## Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `RANTERA_API_KEY` | `rantera-dev-key` | Clé exigée (`X-API-Key`) sur toute l'API sauf `/api/health`. **À changer** ; le serveur refuse de démarrer avec la clé par défaut si `RANTERA_HOST` n'est pas local. |
| `RANTERA_HOST` / `RANTERA_PORT` | `127.0.0.1` / `8000` | Interface d'écoute |
| `RANTERA_DB` | `data/rantera.db` | Base SQLite |
| `RANTERA_MODEL` | `data/models/severity_rf.joblib` | Modèle IA |
| `RANTERA_ENGINE` | `hybrid` | `rules`, `ml` ou `hybrid` (gravité = max règles/IA : privilégie la sur-estimation) |
| `RANTERA_WEBHOOK_URL` | vide | Supervision externe : reçoit chaque alerte en `POST` JSON |
| `RANTERA_WEBHOOK_SECRET` | vide | Signe le corps (`X-Rantera-Signature: sha256=<HMAC>`) |

### Activer des services externes
- **Supervision externe** : définir `RANTERA_WEBHOOK_URL` (et le secret). Format du message : `docs/API.md`. Sans webhook,
  les alertes sont marquées « envoyées (dashboard) » : le tableau de bord intégré est la supervision.
- **Fond de carte** : le tableau de bord charge Leaflet + OpenStreetMap depuis Internet. Hors ligne, il bascule sur une
  carte canvas simplifiée. Aucun compte ni clé requis.
- **SMS, e-mail, services d'urgence réels** : *non implémentés*. Il faut un fournisseur (identifiants requis) ; le webhook
  signé est le point d'extension prévu.
- **Données réelles** : envoyer la télémétrie avec `"source": "real"` au même endpoint (voir `docs/API.md`).

## Tests

```bash
python -m unittest discover -s tests -v
```
Voir `docs/RAPPORT_EVALUATION.md` (régénérable : `python -m rantera train && python -m rantera evaluate`).

## Commandes utiles
`python -m rantera train|evaluate|simulate|purge|serve` — `purge --days N` supprime les positions de plus de N jours et
anonymise la position des incidents correspondants (protection des données de localisation).
