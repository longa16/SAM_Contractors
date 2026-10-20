# Dossier de candidature et contrôle final

## Accès et périmètre

L’administrateur ouvre le dossier depuis une candidature, pour une entreprise et
un avis SAM précis. Le contractor conserve son rôle : informations, vœux et suivi
public des jalons. Les documents de travail, checklists et archives internes sont
réservés à l’admin dans l’interface et l’API.

Les documents acceptés sont des PDF non chiffrés de 5 Mo et 200 pages maximum.
Les documents de consultation sont déposés manuellement : l’interface fournit le
lien SAM et les liens publiés par l’acheteur, sans télécharger automatiquement des
ressources externes. La description de l’avis peut constituer la source de consultation
lorsqu’aucun document n’est disponible ; le contrôle doit le mentionner.

## Versions et pièces contractor

Catégories : consultation, pièces contractor, dossier préparé et annexes de travail.
Chaque nouveau document commence à v1. Une nouvelle version cible la dernière version
active du même document et du même type ; les anciennes versions restent téléchargeables.
L’archivage retire toutes les versions d’un document du dossier actif sans les supprimer.

L’import d’un justificatif vérifie son entreprise, son acceptation et son expiration.
Il conserve une copie indépendante : retirer l’original de l’onboarding n’efface pas
la preuve utilisée par la candidature. L’identifiant source est une référence historique
sans clé étrangère bloquant le retrait de l’original. Un Company Statement PDF peut
également être copié dans le dossier. Les copies ne sont jamais synchronisées silencieusement.

## Checklist

Six points initiaux obligatoires :

1. Consultation et exigences examinées.
2. Éligibilité et Bid vérifiés.
3. Pièces contractor nécessaires réunies.
4. Offre technique vérifiée.
5. Offre financière vérifiée.
6. Assemblage final, signatures, formats et modalités de dépôt vérifiés.

L’admin ajoute les exigences spécifiques, obligatoires ou facultatives. Chaque
contrôle a un état À vérifier, Vérifié ou Non applicable. Vérifié et Non applicable
exigent une note ; la non-applicabilité est une décision explicite de l’admin.
Les contrôles restent manuels : aucune vérification sémantique du contenu d’un PDF
ou de la conformité à toutes les clauses de la consultation n’est revendiquée.

## Validation finale et transitions

Le contrôle final exige un Bid à jour, une offre active et non expirée, un profil
présent, les critères obligatoires respectés, une source de consultation, un PDF
préparé actif et tous les contrôles obligatoires traités. Les pièces périmées et
les justificatifs sources toujours présents mais refusés sont bloquants. Si les
pièces contractor sont déclarées réunies, au moins une copie doit être attachée ;
leur non-applicabilité peut être justifiée si le marché ne les demande pas à ce stade.
L’admin sélectionne la version préparée exacte et rédige sa conclusion finale.

Chaque mutation utilise une révision attendue pour refuser un formulaire périmé.
Les modifications documentaires et de checklist incrémentent cette révision.
La validation conserve une empreinte des documents, contrôles, informations de
l’entreprise et données SAM. Une évolution de ces sources invalide le contrôle
courant et exige une nouvelle validation ; les validations précédentes sont conservées.

Le passage Préparation → Prêt pour envoi, puis Prêt pour envoi → Envoyé / En suivi,
exige une validation courante. Les contrôles sont également vérifiés par l’API.
Les dossiers déjà prêts avant cette évolution doivent être complétés et validés
avant un nouveau passage à Envoyé. Les dossiers déjà envoyés conservent leur suivi.
Après envoi ou clôture, pièces et checklist sont en lecture. Les expirations
ultérieures ne modifient pas les archives ni les jalons historiques.

## Archive interne

Pour chaque validation, l’archive ZIP contient les dernières versions actives à
cet instant et un manifeste JSON avec profil, contexte SAM, checklist, empreintes
SHA-256, version préparée choisie, auteur et date de validation. Elle utilise les
copies stockées en base, y compris si un document est archivé plus tard. L’intégrité
des documents est vérifiée avant l’export. Les fichiers sont nommés par leurs
identifiants internes ; aucun chemin fourni par l’utilisateur n’est utilisé.

Cette archive interne contient des informations privées et des pièces de travail.
Pour l’acheteur, utiliser uniquement la version finale sélectionnée et les pièces
exigées par la consultation. Aucune transmission à l’acheteur n’est effectuée.

## API principale

Préfixe : `/api/companies/{company_id}/applications/{notice_id}/dossier`.

- `GET /` : documents, versions, checklist, blocages et validations.
- `POST /documents` : dépôt PDF ou nouvelle version.
- `POST /import` et `/import-statement` : copies des pièces contractor.
- `GET /documents/{id}/pdf` : téléchargement privé d’une version.
- `POST /documents/{id}/archive` : archivage conservant l’historique.
- `POST /checklist` et `PATCH /checklist/{id}` : exigences et contrôles.
- `POST /approve` : validation finale sur révision et PDF choisis.
- `GET /approvals/{id}/package` : archive interne de la validation.

Les dépôts PDF utilisent `Content-Type: application/pdf`. Les opérations sensibles
sont auditées. Migration requise : `uv run alembic upgrade head`, puis redémarrage.
