# Onboarding des entreprises clientes

Dans ce projet, « contractor » désigne une entreprise cliente accompagnée.

| Information | Emplacement |
|---|---|
| Identité, adresse, contact, site, UEI/CAGE | Profil entreprise |
| Trade / service categories | Métiers et services, NAICS/PSC |
| Service area | États couverts et précisions de zone |
| W-9 | Justificatif privé de type W-9 |
| Certificate of Insurance | Justificatif privé de type assurance |
| Workers’ compensation | Applicabilité expliquée et justificatif si applicable |
| Required business/trade licenses | Applicabilité, liste de licences et justificatifs |
| Certifications | Déclarations de ciblage, fiches détaillées et justificatifs |
| Capability statement | Company Statement formulaire ou PDF |
| References | Références structurées : client, projet, année, mission, montant, contact |
| Government experience | Expérience gouvernementale, références publiques et numéro de contrat |
| Capacity | Effectif, équipes, projets simultanés, équipements, cautionnement |
| Availability | Disponible / limitée / indisponible, date et précisions |
| Pricing / rate information | Tarifs USD par prestation et unité, avec conditions |

Les informations sont déclaratives. Les identifiants UEI/CAGE sont contrôlés sur
leur format, sans interrogation d’un registre. Les licences et certifications
structurées documentent les déclarations ; le ciblage existant utilise toujours
les certifications déclarées dans le champ de ciblage. Le nouveau contenu n’active
pas automatiquement une qualification juridique ou une nouvelle règle de scoring.

Les PDF sont conservés en base et téléchargés après authentification et contrôle
de l’entreprise. Le fichier peut contenir des informations sensibles (notamment W-9) :
il ne reçoit aucune URL publique et n’est pas envoyé à l’IA. Les dates d’expiration
sont recalculées à l’affichage. Aucun système d’alerte email n’est introduit.

Le contractor ajoute et retire ses documents. L’admin les consulte et les accepte
ou demande une correction avec un motif visible par l’entreprise. Le nombre de
justificatifs attendu pour licences/certifications tient compte du nombre déclaré ;
la correspondance exacte entre pièces et déclarations est vérifiée par l’admin.
Le statut « déclaré non applicable » ne signifie pas « accepté par l’admin ».

L’admin accède au dossier depuis Administration ou Dossiers entreprises, et choisit
l’entreprise dans Espace actif. Les onglets de saisie contractor restent masqués et
les API d’écriture lui sont refusées. Les contractors ne voient aucun onglet de
supervision et ne peuvent pas appeler les API de contrôle ou de validation de candidature.

Mise à jour : `git pull origin main`, `uv run alembic upgrade head`, redémarrer le serveur.
Les profils historiques restent lisibles ; les nouvelles informations sont à compléter.
