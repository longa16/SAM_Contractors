# GovContractAI — SAM_Contractors

Plateforme d'accompagnement d'entreprises candidates aux marchés publics américains.
Le propriétaire supervise plusieurs entreprises ; chaque entreprise dispose de son espace
privé et son équipe valide ses candidatures. Les sous-traitants sont distincts des
entreprises clientes. Voir [les décisions](docs/DECISIONS.md) et [la roadmap](docs/ROADMAP_AGENT_CODE.md).

## Installation et fondations (Phase 0.5)

Python 3.13 et uv sont requis. Depuis la racine du dépôt :

```bash
uv sync --frozen
uv run alembic upgrade head
# Si le JSON historique est disponible localement (la source n'est pas modifiée) :
uv run python -m scripts.import_json_to_db data/opportunities_db.json
```

L'import est idempotent ; un second passage ne crée pas de doublons et ne réinitialise
pas les états métier. Les 901 offres locales ont été importées et conservées. Les JSON
et les bases sont ignorés ; une nouvelle installation sans données commence vide.

Configurer les variables de `.env.example` dans l'environnement ou un fichier `.env`
non versionné. Ne jamais copier de clé dans les commandes, les logs ou Git.
La clé SAM est nécessaire uniquement aux appels réels, pas aux tests et migrations.
Dans cet environnement cloud, utiliser `UV_CACHE_DIR=/workspace/.cache/uv` si le cache
utilisateur est en lecture seule.

```bash
uv run python sync.py       # Un passage ; code de sortie 1 sur échec, aucun faux succès
uv run python scheduler.py  # Processus séparé : 00h00, 08h00, 16h00 UTC + rattrapage
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`GET /healthz` vérifie le processus ; `GET /readyz` vérifie l'accès à une base migrée.
Le worker ne démarre pas dans le serveur web : éviter les tâches dupliquées entre workers.
L'API d'administration et l'authentification sont introduites avec la Phase 1 ; aucune
route de déclenchement de synchronisation non authentifiée n'est exposée ici.

## Garanties d'ingestion

- Dates UTC aware ; dates seules à 23h59m59 heure de l'Est ; heure ambiguë à l'automne
  interprétée comme la seconde occurrence.
- Fenêtre depuis le dernier succès avec un jour de recouvrement ; découpage ≤ 365 jours.
- Pagination jusqu'à épuisement ; plafond de sécurité configurable de 50 pages/fenêtre.
  Atteindre le plafond est un échec explicite, jamais une synchronisation complète.
- Trois essais exponentiels sur panne réseau, HTTP 429 ou 5xx ; pas de retry sur 401/403.
- Offres et curseur transactionnels ; aucun avancement si une page échoue.
- État métier séparé des données SAM ; audit des imports, mises à jour et expirations.
- Verrou SQL interprocessus avec bail et protection contre les workers périmés.
- Conservation des opportunités expirées ; pas de suppression physique automatique.
- Aucun téléchargement de description pendant l'ingestion ; aucune action email/soumission.

## Vérification

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict app/
uv run pytest -q
uv run alembic check
```

La suite sans réseau vérifie notamment deux synchronisations successives, les dates/DST,
la conservation des décisions, la panne en page 2, 4 200 et 5 200 résultats, les verrous
concurrents, l'import répété de 901 offres, la migration réversible et les sondes HTTP.
Les appels réels sont exclus par défaut ; une API simulée ne prouve pas l'accès réel SAM.

## État et limites

Voir [VALIDATION.md](docs/VALIDATION.md) pour les preuves et les étapes restantes.
Les modules de screening, RFP, sous-traitants, pricing et post-award suivent les phases
de la roadmap ; ils ne sont pas présentés comme déjà opérationnels.
