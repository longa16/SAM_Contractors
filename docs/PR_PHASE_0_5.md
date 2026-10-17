# fix: stabilize SAM ingestion and add audited SQL foundations

La base JSON était sauvegardée en liste puis relue comme un dictionnaire ; une nouvelle
synchronisation échouait et une panne de pagination pouvait avancer le curseur.
L'ingestion utilise désormais SQLAlchemy, une migration Alembic et un curseur transactionnel,
avec états métier séparés, dates UTC, pagination contrôlée et verrou interprocessus.
Les points d'entrée historiques restent disponibles et les 901 offres sont préservées.

Validation locale : 54 tests, Ruff, format, mypy strict et Alembic check passent.
L'accès réel SAM et la CI distante ne sont pas validés ; GitHub API est refusée par le proxy.
