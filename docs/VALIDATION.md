# Validation et suivi

## Phase 0.5 — validée localement

- 54 tests passent ; couverture `app/sam` et `app/services` : 99,25 %.
- Ruff, format et mypy strict passent ; Alembic ne détecte pas de dérive.
- P05-AC1 à AC8 : tests dans `tests/integration/test_sync.py`, `test_import.py`,
  `test_migrations_health.py`, `tests/unit/test_normalize.py` et pipeline CI déclaré.
- Import réel du fichier local : deux passages, 901 opportunités, données source intactes.
- La CI distante n'a pas été exécutée : seul l'équivalent local est vérifié.
- API SAM et documentation officielle non accessibles dans cette instance (proxy 403).
- API GitHub refuse aussi la lecture des métadonnées du dépôt ; aucune PR distante créée.
  La branche et les changements locaux restent consultables.

## Adaptation métier

L'instruction utilisateur remplace D10 : espaces privés pour les entreprises clientes,
avec supervision de la plateforme. Les décisions appartiennent à l'entreprise candidate.
La Phase 1 adapte les contraintes de schéma et d'autorisation à ce modèle.

## Conditions avant de déclarer la plateforme complète

Les phases 1 à 8 ne doivent pas être cochées avant leurs vérifications respectives.
Les mesures LLM réelles, l'évaluation sur 30 avis étiquetés, trois RFP représentatifs,
les modèles commerciaux approuvés et les intégrations de production ne peuvent être
remplacés par de faux résultats ou de fausses données métier.
