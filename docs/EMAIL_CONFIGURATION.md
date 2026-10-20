# Invitations, comptes et emails d’accès

## Parcours

L’admin ouvre Utilisateurs et invitations, choisit une entreprise et invite un membre.
Le membre reçoit un lien de choix du mot de passe ; son compte ne peut se connecter
avant acceptation. Renvoyer l’invitation remplace l’ancien lien. Annuler l’invitation
ou désactiver le compte annule les liens en attente et révoque les anciennes sessions.
Le rôle et l’activation d’un membre peuvent être modifiés sans effacer son historique.
Les comptes administrateurs ne sont pas désactivables par cet écran.

Mot de passe oublié utilise une réponse générique identique pour les adresses connues
et inconnues. Le lien peut être utilisé une seule fois avant expiration. Les opérations
publiques et les demandes répétées de liens sont limitées en fréquence. Mon compte
permet de changer le mot de passe avec vérification du mot de passe actuel. Les anciens
jetons de session sont révoqués après changement, réinitialisation ou mise à jour d’accès.
Une déconnexion explicite révoque les sessions du compte et les liens en attente.

## Mode local — aucun email envoyé

Configuration par défaut :

```dotenv
MAIL_MODE=local
PUBLIC_APP_URL=http://127.0.0.1:8000
INVITE_EXPIRY_HOURS=24
RESET_EXPIRY_MINUTES=30
```

La boîte de test est accessible à l’admin dans Utilisateurs et invitations. Elle contient
les liens de test encore utilisables. Les contractors n’ont pas accès à cette boîte.
Ce mode permet la recette locale ; il ne remet pas les emails à leurs destinataires.
La démo impose également ce mode, indépendamment des variables SMTP de la machine.

## Envoi réel SMTP

Ajouter les variables adaptées à votre fournisseur dans le fichier `.env` local,
sans les publier sur GitHub ni les transmettre dans les messages de support :

```dotenv
MAIL_MODE=smtp
PUBLIC_APP_URL=https://app.votre-domaine.fr
MAIL_FROM=GovContractAI <acces@votre-domaine.fr>
SMTP_HOST=smtp.votre-fournisseur.fr
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=votre-utilisateur-smtp
SMTP_PASSWORD=votre-mot-de-passe-smtp
SMTP_TIMEOUT_SECONDS=15
INVITE_EXPIRY_HOURS=24
RESET_EXPIRY_MINUTES=30
```

Pour un serveur SMTP TLS dès la connexion : `SMTP_SECURITY=ssl`, avec le port indiqué
par le fournisseur (souvent 465). `plain` est disponible pour un relais de test contrôlé.
L’adresse d’expédition doit être autorisée par le fournisseur. PUBLIC_APP_URL doit
être l’adresse réelle par laquelle les utilisateurs accèdent à l’application ; elle
n’est jamais déduite du header Host d’une requête. Redémarrer le serveur après modification.

L’envoi est effectué après enregistrement du compte et du message. Les états sont :
en attente, en cours, remis au serveur SMTP ou échoué. « Remis au serveur SMTP »
ne garantit pas une réception effective dans la boîte du destinataire. Un échec peut
être relancé par l’admin, au maximum cinq tentatives pour ce message, tant que son lien
reste valide. En cas de lien expiré, renvoyer l’invitation ou créer une nouvelle demande.
Après une interruption alors qu’un envoi était en cours, vérifier la remise et générer
un nouveau lien si nécessaire ; le système ne répète pas un envoi au statut incertain.
Aucun worker permanent ou service email externe n’est nécessaire pour la recette locale.

## Stockage et liens

Les jetons sont aléatoires et leur empreinte seule est enregistrée dans account_action.
Le corps des messages de la file est chiffré avec une clé dérivée de JWT_SECRET. Conserver
ce secret pour relire les messages locaux ; après rotation, générer de nouveaux messages
si nécessaire. Les liens non utilisables ne sont pas exposés dans la boîte locale.
Le contenu des emails et les réponses SMTP ne sont jamais journalisés.

Le lien utilise un fragment `/#account=…`, que le navigateur ne transmet pas au serveur.
Le fragment est retiré de la barre d’adresse à l’ouverture, et le jeton envoyé seulement
au moment où le titulaire confirme son mot de passe. Une page déjà ouverte traite aussi
un nouveau lien reçu. Aucun jeton de récupération n’est placé en localStorage.
Les sessions normales restent dans sessionStorage avec la durée JWT configurée.

## Vérifications effectuées

Recette automatisée locale : invitation, choix du mot de passe, renvoi invalidant
l’ancien lien, usage unique, expiration, désactivation/réactivation, fermeture des
anciennes sessions, changement de mot de passe et réinitialisation. La remise SMTP,
son échec et la relance sont testés avec un serveur simulé. Aucun email réel n’a été
envoyé pendant le développement ; une recette avec votre fournisseur configuré reste
nécessaire avant utilisation réelle.
