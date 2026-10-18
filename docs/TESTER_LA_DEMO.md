# Tester GovContractAI sans clé API

Cette démonstration utilise exclusivement des entreprises et offres fictives, avec une IA
simulée. Elle n'envoie aucun email, ne soumet aucune candidature et ne contacte pas SAM/Groq.
Elle crée sa propre base temporaire et ne modifie pas les 901 offres historiques.

## Démarrage sur votre ordinateur

Installer Python 3.13 et uv, puis ouvrir un terminal dans le dossier extrait du projet :

```bash
uv sync --frozen --python 3.13
uv run python -m scripts.run_demo
```

Laisser ce terminal ouvert et saisir `127.0.0.1:8000` dans la barre d'adresse du navigateur
**sur le même ordinateur**. Aucun compte cloud ou serveur public n'est nécessaire.
Le port 8000 doit être disponible. Pour arrêter : Ctrl+C dans le terminal.

## Comptes de démonstration

Mot de passe commun, réservé à la démonstration locale : `Demo-test-2026!`.

| Connexion | Rôle |
|---|---|
| `admin@demo.test` | Votre supervision : accès aux deux entreprises et administration. |
| `alpha@demo.test` | Équipe de l'entreprise Alpha, décideur de ses candidatures. |
| `beta@demo.test` | Équipe de l'entreprise Beta, espace privé distinct. |

Aucun de ces accès n'est créé dans la base réelle. Le serveur écoute uniquement sur
votre ordinateur. Les clés externes de votre environnement sont explicitement désactivées
pour cette démo. Le redémarrage du programme crée une nouvelle base vide, puis réinitialise
les données fictives : les décisions précédentes ne sont pas réutilisées.

## Parcours conseillé — environ 10 minutes

1. Connexion **admin** : sélectionner Alpha puis Beta et constater que chacun a ses propres
   scores et dossiers. Dans Administration, créer une troisième entreprise et un accès
   client si vous souhaitez tester l'onboarding. Renseigner son profil avant toute analyse.
2. Connexion **alpha** : parcourir les quatre offres, rechercher « Maintenance », filtrer
   les recommandations et ouvrir une fiche. Cliquer sur « Analyser pour mon entreprise ».
   L'IA est simulée : vous testez le parcours, pas la qualité réelle des recommandations.
3. Sur une offre recommandée, choisir **Bid**. Sur la seconde, essayer **No-Bid** : un motif
   est obligatoire. Ajouter un commentaire à un dossier.
4. Vérifier les offres bloquées : marché réservé **8(a)** sans certification correspondante,
   et marché dont le délai est inférieur au minimum du profil.
5. Déconnexion, puis connexion **beta** : les décisions et commentaires d'Alpha ne sont
   pas visibles. Beta peut prendre ses propres décisions sur les mêmes offres SAM.
6. Dans Profil entreprise, changer les qualifications et enregistrer. Les analyses sont
   signalées à renouveler ; relancer l'analyse avant une nouvelle décision.
7. Réduire la largeur de la fenêtre ou utiliser un navigateur mobile pour vérifier la mise
   en page. Le serveur n'est pas accessible depuis un autre appareil sans configuration
   réseau supplémentaire ; la simulation mobile du navigateur suffit.

## Limites à connaître

L'actualisation réelle du catalogue échoue volontairement sans clé SAM ; le bouton admin
n'est pas une fonction de simulation. Le lien SAM d'un avis fictif ne correspond pas à un
marché existant. La sonde de migration `/readyz` vise la base réelle migrée : la démo utilise
un schéma temporaire généré pour ses seuls tests d'interface.
Les modules RFP, sous-traitants, email, prix, propositions et post-award ne font pas partie
de cette version. Pour tester de vrais avis et Groq, utiliser le lancement normal du README
avec vos clés et un profil d'entreprise réel, pas `run_demo`.

Notez pour chaque anomalie : compte utilisé, offre concernée, action, résultat attendu et
résultat observé. Ne transmettez jamais vos clés ou mots de passe réels dans le chat.

## Une entreprise de test dans votre base habituelle

Après `git pull origin main` et `uv run alembic upgrade head`, exécutez :

```bash
uv run python -m scripts.seed_example_company
```

Cette commande utilise `DATABASE_URL` comme l’application, sans appeler SAM ni Groq.
Elle crée **Potomac Facility Services LLC — Test**, entreprise fictive spécialisée
nettoyage de bâtiments et de moquettes, et `proposals@potomac-facility.example`,
utilisateur habilité à analyser et décider Bid/No-Bid pour cette seule entreprise.
Le mot de passe aléatoire s’affiche une seule fois lors de la création : conservez-le.
Relancer la commande ne crée aucun doublon et ne change pas les accès existants.

Profil : NAICS 561720 et 561740 ; PSC S201 et S214 ; États VA, MD et DC ;
contrats de 25 000 à 1 500 000 USD ; cautionnement maximal 500 000 USD ;
minimum 7 jours pour répondre, idéalement 21. Small business de test sans
certification spéciale. L’échéance SAM simulée est à 300 jours de la création.
Toutes ces informations sont fictives et modifiables dans « Profil entreprise » ;
elles ne constituent pas une inscription ni une certification SAM réelle.
L’application ne dispose pas encore de champs pour l’adresse, les effectifs ou l’UEI.

Cette base est distincte de la base temporaire de `scripts.run_demo` : lancez le
serveur habituel pour retrouver cette entreprise.
