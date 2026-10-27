# Examen des candidatures avec Hermes Agent

Hermes Agent est le moteur d’exécution. Le modèle reste configurable : par défaut,
le même fournisseur Groq que l’analyse existante, avec `qwen/qwen3.8-27b`.
Installer Hermes ne signifie donc pas utiliser un modèle de la famille Hermes.

## Installation locale

Depuis la racine du projet, application arrêtée :

```bash
git pull origin main
uv sync --frozen
uv run alembic upgrade head
uv run python -m scripts.install_hermes
```

L’installateur utilise Git et uv. Il télécharge la révision officielle
`11a75355a4c5540154597c7ea23cbc2a933539f5` du dépôt
<https://github.com/NousResearch/hermes-agent>, puis installe son environnement
Python 3.14 avec son propre `uv.lock`. Le Python 3.13 et les dépendances de
l’application restent séparés. Le répertoire `.runtime/` est privé, ignoré par Git.

Ajouter dans le `.env` local existant :

```dotenv
HERMES_ENABLED=true
```

Si `GROQ_API_KEY` est déjà configurée, aucune copie de clé n’est nécessaire pour
le fournisseur par défaut. Ne jamais mettre de clé dans le frontend ou dans Git.
Sous Windows, ajouter aussi `HERMES_PYTHON` avec le chemin affiché par l’installateur
vers `.runtime/hermes-venv/Scripts/python.exe`. Sur Linux/macOS, le chemin par défaut
est `.runtime/hermes-venv/bin/python`.

```bash
uv run python -m scripts.smoke_test_hermes
uv run uvicorn app.main:app --reload
```

Le smoke test fait fonctionner le vrai moteur et ses deux outils avec un faux
serveur de modèle sur `127.0.0.1`. Il ne consomme pas de quota LLM et n’utilise
aucune donnée de votre base. Il vérifie le transport, pas la qualité d’un modèle réel.

## Parcours de l’admin

1. Accepter une demande contractor ou initier une candidature : étape **Examen en cours**.
2. Ouvrir **Ouvrir le dossier de candidature**, puis la section **Examen avec Hermes**.
3. Joindre les PDF utiles dans la catégorie Consultation, si nécessaire, puis cliquer
   sur **Lancer l’examen avec Hermes**.
4. Consulter l’état, le rapport, les exigences, les correspondances avec le contractor,
   les citations, les pièces manquantes et les questions proposées.
5. Rédiger la conclusion du contrôle humain et valider ou refuser le rapport.
6. Revenir à la fiche de l’offre pour décider **Bid/No-Bid**, puis valider explicitement
   l’étape d’examen avant de préparer le dossier.

Le bouton **Analyser la pertinence** de la fiche conserve le screening existant.
L’examen agentique se lance depuis **Examen avec Hermes** dans le dossier.

Valider le rapport ne décide pas Bid, ne termine pas une étape et n’envoie rien.
Cette action humaine rafraîchit les critères objectifs locaux et rend la décision
Bid/No-Bid disponible. Les contrôles d’éligibilité existants restent applicables.
Refuser le rapport ne refuse pas la candidature : un nouvel examen peut être lancé.
Les contractors conservent uniquement leurs informations, demandes et jalons publics.
Les rapports, traces et conclusions de contrôle sont réservés à l’admin.

## Sources et limites de lecture

L’examen utilise un instantané propre à une candidature : avis SAM et description
déjà enregistrés, profil, Company Statement, inventaire des justificatifs et dernières
versions actives des PDF de consultation joints. Le contenu des W-9, assurances et
autres justificatifs d’onboarding n’est pas transmis ; leur inventaire et leur état
de contrôle permettent de repérer des pièces à vérifier.

Hermes dispose de deux outils de lecture, `search_sources` et `read_source`, et
d’un outil `submit_report` qui remet le rapport au contrôle humain sans modifier
la candidature. Il lit les sources
par portions et construit son rapport structuré. Les exigences et conclusions
d’éligibilité doivent citer un extrait présent dans une source réellement consultée.
Les extraits invalides sont renvoyés à Hermes pour correction dans la limite de
la tâche. L’application vérifie à nouveau ces extraits avant d’accepter le résultat.
La lecture de l’avis et des sources de contexte de l’entreprise est obligatoire ;
les autres sources non consultées sont signalées. Cette vérification
confirme la provenance du texte, pas la justesse de son interprétation.

La lecture est limitée à 100 pages PDF et 240 000 caractères au total, avec au plus
60 000 caractères par source. Un PDF scanné ou non extractible est signalé ; aucun
OCR n’est simulé. Les pièces externes SAM ne sont pas téléchargées automatiquement.
L’admin doit joindre les documents nécessaires et tenir compte des limites affichées.

Un changement d’avis, de profil, de présentation, d’inventaire ou de PDF de consultation
rend le rapport antérieur périmé pour sa validation. Le rapport et son instantané
restent conservés ; une relance crée un nouvel examen. Un ancien rapport ne se met
pas silencieusement à jour.

## Exécution et exploitation

Les demandes sont persistées en base avant traitement. Le worker démarré avec
l’application consomme la file, une demande à la fois par processus serveur. La
contrainte d’unicité interdit deux examens actifs de la même candidature ; un double
clic retrouve la demande déjà enregistrée. Les états sont En attente, Examen en cours,
Rapport disponible, Examen échoué et Examen annulé.

L’admin peut annuler une demande et relancer après échec. Les résultats tardifs d’une
exécution annulée ne remplacent pas son état. Un travail interrompu par un arrêt brutal
est marqué en échec à expiration de son bail ; il n’est pas facturé une seconde fois
par une relance automatique. Les demandes encore en attente restent dans la file.

Chaque exécution dispose d’un processus et d’un répertoire temporaire avec un home
Hermes neuf, sans mémoire partagée entre entreprises, outils de terminal, navigation,
messagerie ou écriture dans l’application. Seuls la clé du fournisseur et les réglages
système nécessaires au réseau sont transmis au processus. Les sources et rapports
sont conservés dans la base privée ; les fichiers temporaires sont supprimés à la fin.
Cette séparation applicative ne remplace pas un conteneur ou une isolation système
pour un déploiement nécessitant cette garantie.

Les refus d’accès du fournisseur, modèles indisponibles et quotas atteints sont
affichés avec des messages spécifiques. Les erreurs publiques sont des messages fixes ;
les clés et réponses brutes du
fournisseur ne sont pas journalisées. La tâche est bornée par son délai et son nombre
d’itérations. Le plafond quotidien compte toutes les demandes, y compris celles en
échec ou annulées ; il est distinct du budget de l’ancien screening.

Réglages facultatifs :

```dotenv
HERMES_BASE_URL=https://api.groq.com/openai/v1
HERMES_MODEL=qwen/qwen3.8-27b
HERMES_TIMEOUT_SECONDS=240
HERMES_MAX_ITERATIONS=12
HERMES_MAX_TOKENS=2000
HERMES_DAILY_RUN_LIMIT=30
```

Pour un autre fournisseur compatible Chat Completions, renseigner également
`HERMES_API_KEY`. La clé Groq n’est réutilisée que pour l’URL Groq par défaut.
Un compte avec un petit quota de tokens par minute peut ralentir ou bloquer un
examen même si sa clé est valide. Le moteur respecte son délai et ne valide pas un
rapport incomplet ; réessayer après le renouvellement du quota ou adapter le modèle
et la capacité du compte.
Redémarrer l’application après tout changement de configuration. Le mode de
démonstration isolée désactive Hermes pour ne pas lancer d’appels réels.

## API

Préfixe admin :
`/api/companies/{company_id}/applications/{notice_id}/agent-examinations`.

- `GET /?page=1` : historique paginé, état du moteur, sources et rapports.
- `POST /` : mettre un examen en file, réponse 202.
- `POST /{id}/cancel` : annuler une demande en attente ou en cours.
- `POST /{id}/decision` : contrôle humain `{decision: accepted|rejected, note: ...}`.
- `GET /{id}/report` : télécharger le rapport et ses références en JSON.

Une identité contractor reçoit 403 sur ces routes. Un identifiant d’examen provenant
d’une autre candidature reçoit 404. Aucun de ces endpoints n’envoie de candidature.

Ce premier périmètre couvre l’examen. La rédaction des offres, la production de PDF
de candidature et la transmission à l’acheteur restent des étapes ultérieures.
