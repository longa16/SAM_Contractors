# Décisions d'implémentation

## D1–D9 — Valeurs de la roadmap

Contexte : les deux documents décrivent la même plateforme avec des détails divergents.
Choix : dépôt `SAM_Contractors`, package `app`, produit GovContractAI ; SQLite/SQLAlchemy
avec migrations Alembic dès les fondations ; Groq configurable ; authentification dès
le dashboard ; soumission via paquet et preuve ; attribution manuelle ; screening
à deux étages ; interface française et documents commerciaux anglais ; dates sans
fuseau interprétées en America/New_York. Alternatives : JSON réécrit à chaque sync,
authentification tardive, automatisation des portails tiers. Ces alternatives ne sont
pas retenues pour les raisons de la roadmap.

## D10 révisée — Intermédiaire et espaces privés par entreprise

Contexte : le propriétaire a précisé dans la conversation qu'il met en relation les
opportunités SAM et les entreprises, puis a choisi un espace privé par entreprise,
avec sa supervision et les validations effectuées par l'équipe de l'entreprise.
Choix : les opportunités SAM forment un catalogue partagé ; les profils, évaluations,
décisions, commentaires et dossiers sont rattachés à l'entreprise candidate.
Le propriétaire est administrateur de plateforme ; une entreprise ne voit pas les
dossiers des autres. Les prestataires sous-traitants restent un rôle distinct des
entreprises clientes candidates. L'éligibilité d'une entreprise ne se déduit pas de
celle de l'intermédiaire. Alternative rejetée : profil unique de l'intermédiaire ou
back-office sans compte client. Cette instruction utilisateur remplace le défaut
mono-entreprise D10 et nécessite une adaptation explicite du schéma en Phase 1.

## D11 — Contrat d'API SAM à confirmer sans désactiver les protections

Contexte : le proxy refuse la documentation officielle `open.gsa.gov` (HTTP 403).
Choix : utiliser l'endpoint et les paramètres documentés dans la roadmap, sans envoyer
`is_active`, dont le support n'est pas vérifié. Filtrage local strict de l'activité
et du type. Ne pas présenter le quota personnel ni la compatibilité réelle comme
vérifiés. Alternative rejetée : inventer un paramètre ou contourner le proxy.

## D12 — Reprise transactionnelle et rétention conservatrice

Contexte : une page perdue ne doit pas avancer le curseur ; les futures références
à un dossier ne doivent pas être détruites.
Choix : récupération complète avant transaction des offres ; le curseur est
`sync_run.checkpoint_at` de la dernière exécution réussie (borne haute de la fenêtre,
non l'heure de fin). La table remplace `last_sync.json`, laissé intact. Un index
unique conditionnel et un bail de 30 minutes empêchent les workers concurrents ;
une mise à jour conditionnelle bloque aussi le retour tardif d'un worker périmé.
Une exécution trop longue échoue sans avancer le curseur. La reprise refait la fenêtre.
L'expiration ne détruit aucune ligne : la suppression après J+30 est facultative et
reste désactivée tant que les liens des phases suivantes ne sont pas établis.
Les dossiers déjà soumis/attribués/perdus ne sont pas expirés par leur ancienne deadline.
Alternative : écritures partielles et purge physique, rejetées pour préserver les dossiers.

## D13 — Import et données historiques

Contexte : les JSON de production et anciens bytecodes étaient suivis par Git.
Choix : les retirer de l'index sans les supprimer du disque ; importer les 901 offres
sans filtre de type/deadline pour ne perdre aucune donnée. Un échantillon complet
compressé sans contacts est conservé pour les régressions ; le fichier original reste
local et intact. Les tests ne font aucun appel réseau réel par défaut.

## D14 — La pagination officielle prévaut sur l'incrément de la roadmap

Contexte : la source officielle GSA, accessible sur son dépôt GitHub, définit `offset`
comme un **indice de page** ; la roadmap prescrit à tort un offset de lignes.
Choix : incrément de 1 ; l'arrêt compare `indice * taille_page` à `totalRecords`.
Alternative rejetée : incrément de 1 000, qui sauterait des pages et perdrait des offres.
Les tests de 4 200 et 5 200 résultats vérifient les indices exacts et les dernières
pages partielles. Voir `SAM_API_CONTRACT.md` pour les sources.


## D15 — Catalogue SAM accessible sans entreprise active

Demande utilisateur : consulter les offres sans sélectionner ni créer une entreprise.
L'administrateur arrive sur un catalogue SAM global ; liste, recherche, fiches et état
 de synchronisation fonctionnent sans `company_id`. Les jointures de dossiers privés
sont exclues de cette vue : aucun score, motif de décision ou commentaire d'entreprise
n'y apparaît. L'analyse, les décisions et discussions nécessitent toujours un espace
entreprise explicite. Les comptes clients restent rattachés à leur entreprise.
Tests : catalogue avec zéro entreprise, absence de fuite après une décision privée,
refus des mutations sans entreprise, et navigation globale/entreprise dans Chromium.


## D16 — Initialisation annuelle unique, puis appels manuels

L'utilisateur remplace le fonctionnement récurrent par une initialisation automatique
unique sur 365 jours, puis des synchronisations qu'il déclenche lui-même. Une migration
ajoute le marqueur reprenable `sam_bootstrap`, `sync_run.kind` et `last_checked_at`.
La fenêtre initiale est figée au premier lancement, traitée des dates récentes vers les
anciennes et validée période par période. Les dates déjà traitées ne sont pas rejouées
après erreur, et aucun redémarrage ne refait l'année une fois `completed_at` renseigné.
Les marqueurs et les offres d'une période sont enregistrés dans la même transaction.
Le bail est renouvelé à chaque page réseau pour couvrir les chargements longs.

La documentation officielle consultée ne propose pas de filtre de modification. Une
sync quotidienne recherche donc les nouvelles publications et revérifie les avis actifs
connus par identifiant. Les bornes de publication restent limitées à un an par requête.
`last_checked_at` permet de reprendre les vérifications en cas de quota ; au plus une
vérification d'un même ancien avis par journée UTC. Les réponses vides ne sont pas
assimilées à des suppressions. Les nouvelles offres expirées sont ignorées, les dossiers
historiques expirés sont conservés avec leurs liens. Le curseur global n'avance que
lorsque toutes les opérations du passage ont réussi ; les périodes/avis déjà validés
restent néanmoins enregistrés. Cette rétention partielle explicite remplace l'ancienne
transaction unique pour permettre une reprise sous quota sans recommencer indéfiniment.
Les quotas SAM restent une contrainte réelle ; aucun volume journalier n'est inventé.
L'ancien planificateur est désactivé par défaut, avec réactivation explicite possible.
