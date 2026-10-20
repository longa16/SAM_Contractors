# Validation et travail restant


## Vérification réelle SAM — 17 octobre 2026

Cette vérification remplace le constat historique de clés absentes ci-dessous :
SAM_API_KEY est maintenant accessible à l'application et l'authentification réelle a réussi.
Deux pages réelles de taille 1 ont confirmé des avis distincts aux indices 0 et 1.
Deux synchronisations complètes ont ensuite été exécutées dans une base temporaire :
la première a récupéré et stocké 864 offres ; la seconde a mis à jour 271 offres,
inséré 0 doublon et conservé 864 lignes. La fenêtre de recouvrement explique que le
second appel traite moins de journées. Une description SAM réelle a aussi été récupérée
et convertie en texte (854 caractères). Les données historiques n'ont pas été modifiées.
La combinaison `ptype=o,k` est acceptée par l'API réelle. Les 51 régressions ciblées
sync/import/normalisation et le parcours navigateur passent après clarification du
formulaire « Ajouter un utilisateur à cette entreprise ».
La recette réelle Groq, la calibration métier et la validation de production restent
séparées ; ce contrôle ne les déclare pas effectuées.

## Catalogue indépendant des entreprises — vérification actuelle

Le catalogue global est désormais l'accueil administrateur, sans entreprise active,
y compris quand aucune entreprise n'existe. Les données privées restent accessibles
uniquement dans l'espace de l'entreprise sélectionnée. **90 tests Python passent**,
**2 parcours Chromium passent**, couverture métier **95,90 %**. Le test live reste exclu.
Les parcours vérifient l'affichage global, les fiches, le changement d'espace et
l'absence de fuite de scores, décisions et commentaires dans le catalogue global.

## Diagnostic des échecs SAM

Les anciennes tentatives n'enregistraient que `sam_api_error` : leur cause précise ne
peut pas être reconstituée à partir de ce code. Désormais les nouvelles erreurs tracent
un motif fixe, le statut HTTP et le nombre de tentatives quand applicable, dans les
logs et l'audit. `/api/sync/status` et le catalogue exposent ce diagnostic sans URL,
corps de réponse fournisseur ni clé. Une panne de sync ne lance pas le screening ;
un échec du screening après une sync réussie est journalisé séparément.
Validation : 95 tests Python passent, dont 401/403/429/503 et panne réseau avec vérification
d'absence de clé dans les diagnostics. Aucun appel SAM réel n'a été nécessaire pour
ce correctif ; il n'établit pas la cause de l'erreur de l'instance locale de l'utilisateur.

## Initialisation sur 365 jours, puis synchronisations manuelles — 18 octobre 2026

La migration `4253aa59a16d` ajoute une progression initiale persistante, le type de sync
et la dernière revérification de chaque avis. Le premier serveur démarre le chargement
en arrière-plan uniquement si la base est migrée, la clé présente et le marqueur non
terminé. Une base issue des anciennes synchronisations courtes reçoit aussi ce chargement
initial une fois. Les redémarrages suivant une réussite ne le relancent pas.

Les 365 jours calendaires sont parcourus des plus récents aux plus anciens en périodes
de 7 jours. Chaque période et sa progression sont validées ensemble. Un échec ou quota
conserve les périodes terminées ; une reprise continue à la période restante. Les
fenêtres dépassant le plafond de pagination sont subdivisées, sans troncature silencieuse.
Le filtre SAM `rdlfrom` et un contrôle local évitent d'insérer les nouveaux avis expirés.

Après initialisation, chaque clic lance la recherche récente et la revérification des
anciens avis actifs par `noticeid` (y compris date de publication inchangée). Les résultats
validés et `last_checked_at` sont conservés sous quota, mais le curseur global ne progresse
qu'au succès complet. Les décisions humaines restent intactes. Le cron est désactivé
par défaut ; aucun rafraîchissement quotidien n'est lancé automatiquement après ce premier
chargement. Les appels par identifiant sont nécessaires faute de filtre de modification
documenté ; leur complétude reste tributaire du quota SAM réel.

Validation hors réseau : **105 tests Python passent**, dont 10 tests dédiés à l'initialisation,
la reprise, les anciens avis modifiés, le quota et le premier démarrage non bloquant.
**4 parcours navigateur passent**, dont la progression initiale et le passage au bouton
manuel. Migration upgrade/downgrade et contrôle de dérive vérifiés. Aucun chargement annuel
réel n'a été exécuté ici pour ne pas consommer le quota de la clé pendant le développement.

## Résultats locaux (lot initial)

- **88 tests Python passent**, **1 test live exclu**, aucun échec.
- Couverture combinée `app/sam`, `app/services`, `app/agents`, `app/llm` : **95,87 %**.
- Ruff, format, mypy strict et Alembic check passent.
- **1 parcours Chromium passe** : connexion, catalogue, fiche, analyse simulée,
  Bid sans rechargement (statut visible sur la carte), commentaire privé, profil,
  affichage mobile sans débordement horizontal et déconnexion ; aucune erreur JS.
- Serveur réel démarré sur la base locale : accueil, `/healthz`, `/readyz` → 200 ;
  catalogue sans session → 401. Processus de vérification arrêté après contrôle.
- Import de production locale répété : **901 opportunités**, sources JSON conservées,
  curseur historique préservé sans déclarer une synchronisation API réussie.
- Migration initiale et migration entreprise : upgrade/downgrade/upgrade vérifiés ;
  les utilisateurs historiques et assignations existantes sont préservés.
- CI GitHub Actions définie pour qualité, migrations, tests Python et navigateur,
  mais **non exécutée à distance** dans cette session.

## Critères de la roadmap

| Critère | État et preuve |
|---|---|
| P05-AC1–AC6 | Validés par les tests de sync : répétition, UTC/DST, expiration, préservation des décisions, rollback, cinq et six pages. |
| P05-AC7 | 901 avis importés deux fois, test sur échantillon complet sans contacts + import du fichier réel local. |
| P05-AC8 | Commandes locales vertes et pipeline déclaré ; exécution CI distante non vérifiée. |
| P1-AC1–AC3 | 50 avis réels anonymisés triés localement ; blocages set-aside et délai testés. |
| P1-AC4–AC5 | Réparation JSON, double échec, seuils, quotas entreprise/globaux et budget testés sans réseau. |
| P1-AC6 | Décisions, motif obligatoire, audit, 403 par rôle, 404 entre entreprises et 409 pour transitions invalides testés. |
| P1-AC7 | Cache d'entrée et `force` testés ; historique conservé et appel concurrent empêché. |
| P1-AC8 | **Non exécuté** : clés SAM/Groq et jeu réel sélectionné absents. Test opt-in fourni. |
| P1-AC9 | API filtrée/paginée et parcours Chromium vérifiés ; captures générées localement dans `frontend/test-results/`. |
| P1-AC10 | Logs sans description complète ni clés vérifiés par tests ; erreurs LLM et formulaire ne renvoient pas les données sensibles. |
| Calibration §9 | **Non évaluée** : 30 avis étiquetés par un humain sont nécessaires. Aucun rappel ≥90 % revendiqué. |

La Phase 1 **n'est pas cochée comme terminée**. La roadmap §0.1 impose :
« Ne commence jamais la phase N+1 avant que tous les critères d'acceptation de la phase N
soient verts. » Les phases 2 à 8 ne sont donc pas développées dans ce lot.

## Adaptation métier validée dans la conversation

L'utilisateur supervise une plateforme qui accompagne des entreprises candidates.
Il a choisi **un espace privé par entreprise, avec sa supervision et les validations
par l'équipe de chaque entreprise**. La décision D10 de la roadmap est remplacée.
Le schéma introduit `company` et `company_opportunity_state` ; scores et commentaires
référencent ces dossiers privés. L'état global historique sert uniquement au catalogue.
L'administrateur de plateforme peut superviser tous les espaces ; aucun client ne peut
changer d'entreprise en manipulant les paramètres. Les profils YAML d'exemple et les
entreprises des tests ne sont jamais traités comme de vraies qualifications.

## Blocages externes et actions nécessaires

1. Configurer **SAM_API_KEY**, **GROQ_API_KEY** et un **JWT_SECRET** privé dans les
   paramètres sécurisés / `.env` non versionné ; ne pas les transmettre dans le chat.
   Les noms et l'absence ont été vérifiés ; aucune clé externe n'a été inventée.
2. Autoriser les destinations nécessaires et appliquer la configuration de l'environnement.
   SAM.gov refusait le tunnel proxy (403). L'accès réel Groq n'a pas été tenté sans clé.
3. Créer le compte administrateur par la commande masquée du README, puis un espace et
   un profil réel d'entreprise. Sélectionner un avis encore éligible pour la recette live.
4. Fournir 30 avis étiquetés par un décideur de cette entreprise pour évaluer le rappel.
   Le script d'évaluation refuse les jeux sans positifs ou sans 30 avis distincts.
5. Pour la Phase 2 après ces validations : trois RFP représentatifs seront nécessaires.
   Les modèles approuvés et références vérifiées seront nécessaires pour les propositions.

Le brouillon de configuration réutilisable a été actualisé : installation, démarrage,
exigence Groq et JWT, domaines API. Sa sauvegarde **n'applique ni ne publie** les paramètres.
La publication doit être effectuée par l'utilisateur dans les paramètres de l'environnement.
Aucune restauration dans une nouvelle tâche n'a été testée.

## Git et revue

- Phase 0.5 enregistrée sur `feat/phase-0-5-foundations`.
- Lot suivant sur `feat/phase-1-enterprise-dashboard`, incluant l'adaptation multi-entreprise.
- Des descriptions de PR locales sont fournies dans `docs/PR_PHASE_*.md`.
- API GitHub refusée par le proxy ; **aucune PR distante, aucun push ni merge effectués**.
  Les commits restent locaux et ne constituent pas une publication distante.

## Limites explicitement conservées

La combinaison `ptype=o,k` héritée doit être validée avec la clé SAM. Le quota réel dépend
du rôle de cette clé. Les échecs sont explicites et ne déplacent pas le curseur.
La pagination correcte a été vérifiée dans la source officielle GSA, qui contredit
l'incrément de lignes prescrit dans la roadmap ; voir `SAM_API_CONTRACT.md`.
Le disjoncteur LLM est par processus, les plafonds et réservations de budget sont en base.
La rétention physique est désactivée pour préserver les liens métier.
Un avertissement de dépréciation upstream Starlette/httpx est présent dans les tests ;
les tests passent, aucune vérification n'a été désactivée pour le masquer.
La disponibilité, le chiffrement de volume, PostgreSQL/S3, les sauvegardes, la limitation
de débit et les intégrations commerciales restent des travaux de production en Phase 8.

## Espaces distincts et Company Statement

L’administrateur arrive sur la supervision des entreprises (comptage des profils,
création d’espaces et d’accès, accès au catalogue global). L’équipe entreprise
arrive sur ses opportunités, sans sélection d’une autre entreprise ni commande SAM,
avec un thème et une navigation propres.

Le Company Statement est une présentation privée persistée indépendamment du
profil de ciblage : promesse, description, expertises, réalisations, atouts, moyens,
contact. Les équipes internes peuvent le compléter et l’administrateur le superviser.
Les accès entre entreprises sont refusés ; les écritures sont validées et auditées.
La présentation n’est pas encore injectée dans les prompts d’analyse IA.

Migration : `uv run alembic upgrade head` après la mise à jour du code.
Validation : 106 tests Python réussis, couverture des modules métier 96 % ; tests
navigateur pour les deux espaces, persistance après rechargement et affichage mobile.

## PDF privé et suivi des candidatures

- 110 tests Python réussis ; couverture des modules métier 96,08 %.
- 9 scénarios navigateur réussis, dont dépôt PDF, téléchargement authentifié,
  choix persistant PDF/formulaire, conservation du formulaire et suppression PDF.
- Tests d’accès entre entreprises pour lecture, écriture et suppression PDF ; rejet
  des faux PDF, fichiers corrompus, documents chiffrés, vides et supérieurs à 5 Mo.
- Suivi privé : ajout idempotent, récupération des Bid existants, notes persistantes,
  déclaration de dépôt conditionnée à un Bid et transitions jusqu’au résultat.
- Migration ajoutant les documents et le suivi appliquée ; Alembic check sans écart.
- Les dépôts/résultats sont des déclarations manuelles ; aucune transmission SAM.
