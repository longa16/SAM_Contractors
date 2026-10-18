# GovContractAI — SAM_Contractors

Application d'accompagnement des entreprises candidates aux marchés publics américains.
Vous supervisez la plateforme ; chaque entreprise dispose d'un espace privé et son équipe
valide ses candidatures. Les **entreprises clientes** sont distinctes des futurs
**sous-traitants**. Les données SAM sont communes ; les profils, scores, décisions et
commentaires sont privés à chaque entreprise.

## Fonctionnalités développées

- Ingestion SAM transactionnelle et paginée, reprise sans perte du curseur, cron UTC,
  verrou interprocessus, dates normalisées et audit.
- Base SQLite avec migrations Alembic ; les 901 offres historiques sont conservées.
- Connexion JWT, mots de passe Argon2, rôles et isolation des espaces entreprises.
- Administration : création d'entreprises et d'accès clients ; supervision des espaces.
- Profil de candidature propre à chaque entreprise : NAICS, États, certifications,
  taille de contrat, cautionnement et délais ; aucun profil réel inventé.
- Catalogue filtrable et paginé, fiche, commentaires et décision Bid/No-Bid avec audit.
- Screening local déterministe puis analyse Groq : cache, historique, réparation JSON,
  preuves vérifiées, quotas partagés, budget et signalement des analyses reportées.
- Interface française responsive, sans CDN ; décisions et commentaires sans rechargement.

**État** : Phase 0.5 validée localement ; Phase 1 implémentée et validée hors réseau,
avec récupération SAM réelle vérifiée ; la recette IA réelle et la calibration métier restent ouvertes. Les phases RFP,
matching de sous-traitants, outreach, prix, propositions, conformité et post-award ne
sont pas encore implémentées. La roadmap interdit de commencer une phase avant que les
critères de la précédente soient validés. Voir [le suivi détaillé](docs/VALIDATION.md).

## Installer

Python 3.13 et uv sont requis. À la racine du dépôt :

```bash
uv sync --frozen --python 3.13
uv run alembic upgrade head
# Seulement si le fichier historique est présent localement :
uv run python -m scripts.import_json_to_db data/opportunities_db.json
```

L'import est idempotent et conserve les états métier. Il préserve aussi le curseur
historique de `last_sync.json` lorsqu'il est présent, afin de rattraper les journées
manquées. Un import n'est jamais présenté comme une synchronisation API réussie.
Les fichiers d'origine restent sur disque. Les données et bases sont ignorées par Git ;
une nouvelle installation sans import démarre vide.

Configurer les variables de [.env.example](.env.example) dans l'environnement ou un
fichier `.env` privé. `JWT_SECRET` doit être une valeur aléatoire privée d'au moins
32 caractères. Il est utilisé localement pour signer les sessions, sans destination
réseau. Aucun compte ni mot de passe par défaut n'est créé.

```bash
uv run python -m scripts.create_admin --email votre-adresse@example.com
# Le mot de passe est saisi masqué, jamais dans un argument de commande.
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Depuis l'interface : se connecter, ouvrir **Administration**, créer l'espace de
l'entreprise, sélectionner cet espace, compléter son **Profil entreprise**, puis
créer l'accès de son équipe. Le décideur (`business_developer`) analyse les offres,
ajoute des commentaires et valide Bid/No-Bid. Un responsable opérations peut lire
et commenter, mais ne peut pas engager une candidature.

Le profil YAML dans `config/` est un **exemple**, jamais chargé comme une identité réelle.
Les profils réels résident en base et sont validés par Pydantic. Une modification du
profil marque ses anciens scores à réévaluer et bloque la décision jusqu'au nouveau tri.

## SAM et analyse IA

Configurer `SAM_API_KEY` et `GROQ_API_KEY` par un canal sécurisé et autoriser les destinations
`api.sam.gov` et `api.groq.com`. Sans clés, l'import, le catalogue, les profils, les
commentaires et les tests restent utilisables ; les analyses externes sont reportées
explicitement. Les badges « analyse indisponible » ou « description à récupérer » ne
signifient pas qu'une analyse approfondie a réussi.

```bash
uv run python sync.py       # Une synchronisation ; code 1 sur échec
uv run python scheduler.py  # Désactivé par défaut ; uniquement avec SCHEDULED_SYNC_ENABLED=true
```

Le déclenchement manuel lance le screening des entreprises après chaque synchronisation réussie,
par score décroissant. Une analyse individuelle est disponible dans chaque fiche.
Le serveur web ne démarre pas de scheduler ; éviter les workers dupliqués.
L'API admin permet également de demander une actualisation du catalogue.

La pagination utilise des **indices de page** et non des offsets de lignes, contrairement
à la roadmap initiale : voir [la documentation GSA vérifiée](docs/SAM_API_CONTRACT.md).
Une fenêtre peut couvrir au plus 365 jours ; atteindre le plafond de pages est un
échec explicite sans avancement du curseur. Les offres expirées sont conservées.
Les dossiers déjà soumis ou attribués ne sont pas expirés par leur ancienne deadline.
Les clés, URL authentifiées et descriptions complètes ne sont jamais écrites dans les logs.

Les plafonds d'analyse sont conservateurs : `SCREENING_STAGE2_DAILY_CAP` est partagé
entre entreprises (clé SAM commune), et le profil peut définir un plafond individuel
plus bas. Une réservation de tokens en base couvre l'entrée maximale, la réparation et
les retries avant chaque analyse ; même un échec consomme sa réservation. Le budget
par défaut peut donc arrêter les analyses avant le plafond d'offres. L'opérateur doit
l'ajuster selon son budget réel, sans le confondre avec un quota SAM vérifié.
Le disjoncteur Groq est local au processus ; les limites et réservations sont en base.


## Premier lancement et actualisations quotidiennes

Après `uv run alembic upgrade head`, le serveur déclenche automatiquement, avec la clé SAM,
un **chargement initial des 365 jours calendaires incluant aujourd'hui**, une fois par base.
Il commence par les dates les plus récentes, puis remonte dans le passé par périodes de
7 jours, pour rendre les premières offres visibles rapidement. L'API est paginée : un
chargement initial correspond à plusieurs requêtes, pas à un unique appel HTTP.
La recherche initiale utilise `rdlfrom` ; seules les offres actives des types ciblés dont
l'échéance normalisée n'est pas passée sont ajoutées. Les offres sans échéance ne sont pas
considérées expirées localement, mais leur présence dépend du filtre de délai côté SAM.

La progression est conservée dans `sam_bootstrap`. Si une période échoue (quota, réseau,
arrêt du serveur), les périodes terminées restent en base ; le prochain démarrage ou le
bouton de reprise reprend la période non terminée. Après la réussite complète, le marqueur
`completed_at` empêche définitivement de rejouer l'initialisation sur cette base, même après
un redémarrage. Le JSON historique ou une ancienne sync courte ne valent pas un chargement
annuel : une base existante reçoit elle aussi cette initialisation une fois après migration.
Le mode démo et les tests désactivent les appels automatiques externes.

Ensuite, **aucun appel quotidien n'est planifié par défaut** : l'administrateur clique sur
« Récupérer les nouvelles offres et les mises à jour » lorsqu'il le souhaite. La recherche
part du dernier succès avec un jour de recouvrement ; les offres nouvelles sont insérées
et les avis déjà présents sont mis à jour sans créer de doublons ni écraser les décisions.
Les anciennes offres encore actives sont également revérifiées par `noticeid`, car la
recherche par date de publication seule ne garantit pas de découvrir leurs modifications.
Chaque avis connu est vérifié au plus une fois par jour (UTC). Une réponse vide ne provoque
aucune suppression : elle est auditée comme avis non retrouvé.

**Quotas** : SAM ne fournit pas de filtre « modifié depuis » dans le contrat consulté.
Les revérifications par identifiant consomment donc des requêtes supplémentaires, parfois
nombreuses. En cas de quota atteint, les offres déjà reçues sont conservées, les avis déjà
revérifiés sont marqués, et le curseur global ne progresse pas tant que le passage n'est
pas complet. Une nouvelle demande continue les vérifications manquantes du jour ; le jour
suivant les avis les plus anciens sont prioritaires. La complétude quotidienne dépend du
quota associé à la clé. Aucun résultat complet n'est annoncé après une récupération partielle.
Il faut laisser le serveur ouvert pendant le traitement. Ctrl+C interrompt proprement
entre les requêtes ; la requête en cours peut attendre son délai réseau avant l'arrêt.

Les dossiers déjà présents qui deviennent expirés sont conservés comme historique et
marqués expirés ; les nouveaux avis déjà expirés ne sont pas ajoutés. Aucune décision
humaine ni discussion n'est supprimée. La progression s'actualise dans le catalogue.
`AUTO_INITIAL_SYNC=false` permet de différer le chargement initial au clic manuel.
`SCHEDULED_SYNC_ENABLED=false` est le défaut ; n'activez ce réglage que si vous souhaitez
explicitement rétablir l'ancien cron.

## API

Toutes les routes `/api` hors connexion exigent `Authorization: Bearer <session>`.
Un client est rattaché à son entreprise par son compte. L'administrateur accède par défaut
au **Catalogue SAM — toutes les offres**, sans entreprise active et même avant d'en créer une.
La recherche, les filtres SAM, les fiches et l'actualisation du catalogue sont disponibles
sans entreprise. Pour accéder aux scores, décisions, discussions et profils privés,
il sélectionne une entreprise dans « Espace actif » (`company_id` dans l'API).
Les filtres de score/recommandation/statut de candidature nécessitent cette sélection. Modifier ce paramètre ne donne aucun accès à
une autre entreprise pour un client. Le journal d'audit global est réservé à l'administrateur.

- `POST /api/auth/login`, `GET /api/auth/me`.
- `GET/POST /api/companies`, `GET/PUT /api/companies/{id}/profile`, `POST /api/users`.
- `GET /api/opportunities`, `GET /api/opportunities/{id}`.
- `POST /api/opportunities/{id}/screening?force=false`.
- `POST /api/opportunities/{id}/decision` : `{decision: "bid"|"no_bid", reason}`.
- `GET/POST /api/opportunities/{id}/comments`.
- `GET /api/sync/status`, `POST /api/sync/run` (admin), `GET /api/audit` (admin).
- `GET /healthz` : processus ; `GET /readyz` : base accessible à la dernière migration.

La liste accepte `page`, `page_size` (≤100), `status`, `recommendation`, `min_score`,
`naics`, `state`, `set_aside`, `deadline_before` (avec fuseau), `q`, `sort=score|deadline`.
Les erreurs métier retournent `{error: {code, message}}` ; les erreurs de formulaire
ne renvoient jamais les valeurs reçues, notamment les mots de passe.

## Vérifier

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict app/
uv run pytest -q
uv run alembic check
npm --prefix frontend ci
npm --prefix frontend run format:check
# Installer Chromium si nécessaire : cd frontend && npx playwright install chromium
CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:e2e
```

Sur une machine utilisant le Chromium téléchargé par Playwright, omettre
`CHROMIUM_EXECUTABLE`. Node n'est nécessaire qu'aux tests navigateur : aucun build
frontend et aucune dépendance Node ne sont nécessaires au serveur en exploitation.
Dans ce cloud, définir `UV_CACHE_DIR=/workspace/.cache/uv` et
`npm --cache /workspace/.cache/npm` si les caches utilisateur sont en lecture seule.

La suite par défaut interdit les connexions réseau et exclut le test `live`. Le test
navigateur utilise une base temporaire, une entreprise fictive et une IA simulée ; il
ne crée aucun compte dans la base réelle. La CI déclarée reproduit ces contrôles,
mais son exécution distante n'a pas été vérifiée ici.

## Recette réelle et calibration à fournir

```bash
# Variables supplémentaires non secrètes : chemins vers un profil réel et un avis éligible.
LIVE_COMPANY_PROFILE=/chemin/profil.yaml LIVE_OPPORTUNITY_JSON=/chemin/avis.json \
  uv run pytest -m live -o addopts='' tests/live/test_real_screening.py
uv run python -m scripts.eval_screening /chemin/avis_etiquetes.jsonl --profile /chemin/profil.yaml
```

L'évaluation attend au moins 30 avis distincts **étiquetés par un humain**, au format
`{opportunity: {...champs SAM...}, recommendation: "RECOMMENDED"|"REVIEW"|"PASS", reviewer: "..."}`.
Elle mesure le tri local et refuse de déclarer le rappel satisfaisant sans exemple
positif. Aucun jeu de vérité métier ni résultat de rappel n'a été inventé.
La recette `live` exige les clés et des données réelles sélectionnées ; elle vérifie
un screening complet en moins de 120 secondes dans une base temporaire.

Les décisions et écarts aux documents sont consignés dans [DECISIONS.md](docs/DECISIONS.md).
La production (PostgreSQL, S3, TLS, sauvegardes, limitation de débit et disponibilité)
reste une phase séparée ; cette version ne doit pas être exposée publiquement comme
une plateforme de production achevée.

## Essayer sans clés API

Une démonstration locale isolée est disponible :

```bash
uv run python -m scripts.run_demo
```

Voir [le guide de test et les comptes fictifs](docs/TESTER_LA_DEMO.md).


En cas de synchronisation échouée, cliquer sur **Appliquer** pour rafraîchir l'état :
le catalogue affiche désormais le diagnostic de la dernière tentative (statut HTTP,
quota, accès refusé ou panne réseau). Les mêmes champs `reason`, `http_status` et
`attempts` sont journalisés avec `sync_failed`, sans URL ni clé. Les anciens échecs
qui ne contiennent que `sam_api_error` ne permettent pas de retrouver leur cause précise.
