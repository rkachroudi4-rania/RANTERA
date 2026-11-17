# Rapport d'évaluation RANTERA

> Évaluation sur **scénarios simulés** générés par `rantera/scenarios.py` (graine 7). Ces chiffres ne valent **pas** validation sur accidents réels.

## Détection

| Scénario | N | Détectés | Fausses alertes accident | Type correct |
|---|---|---|---|---|
| frontal | 200 | 191 | 0 | 191 |
| lateral | 200 | 194 | 0 | 194 |
| rear | 200 | 195 | 0 | 195 |
| rollover | 200 | 200 | 0 | 200 |
| cruise | 200 | 0 | 0 | - |
| pothole | 200 | 0 | 0 | - |
| hard_brake | 200 | 200 | 0 | - |

- Rappel accidents : **97.5%**
- Taux de fausses alertes accident (scénarios sans accident) : **0.0%**
- Exactitude du type quand détecté : **100.0%**
- Rappel freinage brutal : **100.0%**

## Gravité

| Moteur | Exactitude | Sous-estimation | Sur-estimation | N |
|---|---|---|---|---|
| rules | 76.8% | 4.4% | 18.9% | 780 |
| ml | 83.7% | 8.7% | 7.6% | 780 |
| hybrid | 76.9% | 4.1% | 19.0% | 780 |

## Entraînement du modèle

- Exactitude test : 82.7% (F1 macro 0.833)
