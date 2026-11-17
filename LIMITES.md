# Limites connues

1. **Aucune donnée réelle.** Le générateur (`scenarios.py`) produit des chocs en demi-sinusoïde avec bruit. Les étiquettes de
   gravité du modèle viennent d'un score latent du simulateur, pas de dossiers médicaux. L'exactitude annoncée mesure la
   capacité à retrouver la logique du simulateur ; elle ne prouve rien sur des accidents réels.
2. **Chocs légers manqués.** Sur 1200 accidents simulés, 31 (2,6 %) ne sont pas détectés : tous ont un ΔV de 8 à 11,4 km/h
   (pic < 3 g). Seuil réglable (`DetectionConfig.impact_g`) au prix de plus de fausses alertes sur données réelles.
3. **Gravité hybride = max(règles, IA).** Choix délibéré : sous-estimer est plus grave que sur-estimer. Conséquence : l'exactitude
   du mode hybride (~77 %) est inférieure à celle de l'IA seule (~84 %) mais il sous-estime moins (≈4 % vs ≈9 %).
4. **Fausses alertes réelles inconnues.** 0 % sur les scénarios simulés « sans accident » (croisière, nid-de-poule, freinage) ;
   capteurs réels (vibrations, montage, dérive, bouchons de circulation) non représentés.
5. **Capteurs 50 Hz** supposés. Un téléphone ou un boîtier à fréquence plus basse sous-estimera les pics.
6. **Partie Godot non exécutée** pendant le développement (voir README).
7. **Tableau de bord** : JavaScript vérifié syntaxiquement (`node --check`) et assets servis par l'API (testés), mais **pas rendu
   dans un navigateur** pendant le développement.
8. **Sécurité** : une clé API partagée, pas de comptes utilisateurs ni de chiffrement TLS (à placer derrière un reverse proxy
   HTTPS). SQLite convient à un prototype, pas à une charge de production.
9. **Alertes** : aucune intégration SMS/e-mail/secours. Livraison « au moins une fois » vers un webhook.
10. **Vie privée** : positions stockées en clair ; `purge` fourni mais aucune purge automatique planifiée.
11. Les statistiques mélangent simulé et réel sauf filtre `source`.
