# Cahier des Charges — GovContractAI
## Plateforme AI-Native d'Obtention de Marchés Publics Américains

**Version** : 1.1 | **Date** : 17 octobre 2026

---

## 1. Contexte et Objectif

### 1.1 Problème

Les entreprises cherchant à répondre aux appels d'offres du gouvernement fédéral américain (SAM.gov) font face à :
- Un volume quotidien de **~2 700 nouvelles opportunités**, impossible à traiter manuellement.
- Un processus de réponse long et complexe (analyse RFP, sourcing sous-traitants, prix, rédaction de proposition, conformité).
- Un risque de disqualification élevé pour non-conformité documentaire.

### 1.2 Vision

Construire une plateforme **AI-native de bout en bout** qui accompagne l'entreprise cliente depuis la **détection automatisée** d'une opportunité pertinente jusqu'à **l'exécution opérationnelle** du contrat obtenu, en minimisant les interventions humaines aux décisions stratégiques uniquement.

### 1.3 Périmètre

| Inclus | Hors périmètre (V1) |
|--------|---------------------|
| Surveiller SAM.gov (Solicitation + Combined) | Marchés d'États ou locaux |
| Screening IA + validation humaine | Marchés étrangers (OTAN, UE…) |
| Analyse RFP et extraction structurée | Gestion comptable/ERP |
| Sourcing et outreach sous-traitants | Portail public pour les agences |
| Génération de propositions | |
| Vérification de conformité | |
| Opérations post-award + facturation | |

---

## 2. Target Operating Model

Le processus couvre 12 jalons séquentiels avec des boucles de validation humaine aux points critiques.

```
[SAM.gov]
    │
    ▼
① Opportunity Scout (Détection continue + normalisation)
    │
    ▼
② Opportunity Analyst (Screening IA : fit / no-bid)
    │
    ▼
③ [🧑 Validation humaine — BID / NO BID]
    │
    ▼
④ RFP Analyst (Extraction structurée du cahier des charges)
    │
    ▼
⑤ Contractor Matchmaker (Sélection sous-traitants)
    │
    ▼
⑥ Contractor Outreach Agent (Prise de contact + collecte d'intérêt)
    │
    ▼
⑦ Pricing Agent (Construction des scénarios de prix)
    │
    ▼
⑧ Proposal Agent (Rédaction du draft de proposition)
    │
    ▼
⑨ Compliance Agent (Vérification de la complétude)
    │
    ▼
⑩ [🧑 Approbation finale humaine]
    │
    ▼
⑪ Soumission sur SAM.gov / portail acheteur
    │
    ▼
⑫ [Award] → Operations Agent (Post-award)
                  ├── Work orders + planning
                  ├── QC
                  └── Invoicing
```

---

## 3. Modules Fonctionnels

### MODULE A — Surveillance & Ingestion (Opportunity Scout)

**Objectif** : Maintenir une base de données locale à jour des opportunités actives et pertinentes.

#### Fonctionnalités
- Polling automatique de l'API SAM.gov **3 fois par jour** (00h00 / 08h00 / 16h00 UTC).
- Fenêtre glissante basée sur `last_sync.json` pour ne récupérer que les nouvelles offres.
- Pagination automatique (offset) pour dépasser la limite de 1 000/requête.
- Filtre strict à l'ingestion :
  - `type` ∈ `{Solicitation, Combined Synopsis/Solicitation}`
  - `active` = `Yes`
- Dédoublonnage par `noticeId` (on conserve la version la plus récente sur `postedDate`).
- Suppression automatique des offres dont la `responseDeadLine` est dépassée.

#### Champs stockés par opportunité
| Champ | Source |
|-------|--------|
| `noticeId` | SAM.gov |
| `solicitationNumber` | SAM.gov |
| `title` | SAM.gov |
| `type` | SAM.gov |
| `description` | SAM.gov |
| `pointOfContact` | SAM.gov |
| `additionalInfoLink` | SAM.gov |
| `fullParentPathName` | SAM.gov |
| `naicsCode` | SAM.gov |
| `classificationCode` | SAM.gov |
| `typeOfSetAside` + description | SAM.gov |
| `postedDate` / `responseDeadLine` | SAM.gov |
| `placeOfPerformance` | SAM.gov |
| `resourceLinks` | SAM.gov |
| `_synced_at` | Système |
| `_status` | Système (`new` / `screening` / `bid` / `no-bid` / `submitted` / `awarded`) |

#### Base de données
- **Format** : JSON (fichier `data/opportunities_db.json`) — remplaçable ultérieurement par PostgreSQL/MongoDB.
- **Scheduler** : `scheduler.py` (processus Python continu, retry automatique en cas d'erreur réseau).

---

### MODULE B — Screening Automatique (Opportunity Analyst)

**Objectif** : Évaluer en moins de 2 minutes si une opportunité vaut la peine d'être examinée par un humain.

#### Critères d'évaluation IA

| Dimension | Critères analysés |
|-----------|------------------|
| **Capacité** | Code NAICS, classification code, mots-clés du secteur |
| **Géographie** | `placeOfPerformance` vs zones d'intervention de l'entreprise |
| **Taille de contrat** | Valeur estimée (si disponible), seuil min/max configuré |
| **Timeline** | Délai entre `postedDate` et `responseDeadLine` |
| **Set-aside** | Small business, 8(a), HUBZone, SDVOSB, WOSB, etc. |
| **Licences / assurances** | Prérequis détectés dans la description |
| **Bonding** | Montant de cautionnement requis |
| **Sous-traitance** | Contraintes de prime/sub détectées |
| **Risque** | Complexité du scope, ambiguïtés, historique d'annulation |

#### Outputs
- Score de fit `0–100`
- Recommandation : `RECOMMENDED / REVIEW / PASS`
- Facteurs positifs et bloquants (justification lisible par l'humain)
- Mise à jour de `_status` → `screening`

#### Interface humaine
- Dashboard listant les opportunités `RECOMMENDED` et `REVIEW`.
- Chaque carte affiche : titre, agence, deadline, score, NAICS, lieu, set-aside, valeur estimée.
- Boutons : **✅ Bid** | **🚫 No-Bid** | **💬 Discuss**.
- Mise à jour de `_status` → `bid` ou `no-bid`.

---

### MODULE C — Analyse RFP (RFP Analyst)

**Objectif** : Lire les documents du dossier d'appel d'offres et en extraire une structure JSON complète et lisible par les agents suivants.

#### Sources d'ingestion
- PDF téléchargés depuis `resourceLinks` et `additionalInfoLink`
- Amendments postérieurs (détectés au prochain cycle de sync)

#### Extraction structurée

```json
{
  "scope": "Description technique du travail à effectuer",
  "deliverables": ["Livrable 1", "Livrable 2"],
  "mandatory_requirements": ["Certification X", "Assurance Y"],
  "evaluation_criteria": [
    { "criterion": "Technical Approach", "weight": "50%" },
    { "criterion": "Price", "weight": "30%" },
    { "criterion": "Past Performance", "weight": "20%" }
  ],
  "forms_required": ["SF-1449", "SF-33", "Attachement A"],
  "deadlines": {
    "questions_cutoff": "2026-10-25",
    "proposal_due": "2026-11-01T14:00:00-05:00"
  },
  "risks": ["Ambiguïté sur le lieu d'exécution", "Pénalités de retard non plafonnées"],
  "contract_type": "FFP",
  "period_of_performance": "12 mois",
  "amendments": []
}
```

---

### MODULE D — Sourcing Sous-traitants (Contractor Matchmaker)

**Objectif** : Identifier parmi la base de données de sous-traitants ceux qui sont qualifiés et disponibles pour l'opportunité.

#### Base de données Contractors
Chaque sous-traitant est enregistré avec :
- `trades` (métiers, codes NAICS couverts)
- `location` + zones d'intervention
- `licenses` (par état/spécialité)
- `insurance` (certificat COI, montants)
- `bonding_capacity`
- `availability` (calendrier)
- `past_projects` (références vérifiées)
- `certifications` (8a, HUBZone, etc.)

#### Algorithme de matching
1. Filtrage dur (trades + location + licenses obligatoires).
2. Scoring de compatibilité (disponibilité, capacité, certifications additionnelles).
3. Retour d'une shortlist triée par score.

---

### MODULE E — Outreach Sous-traitants (Contractor Outreach Agent)

**Objectif** : Contacter automatiquement les sous-traitants shortlistés pour valider leur intérêt.

#### Workflow
1. Génération d'un email personnalisé (résumé de l'opportunité + scope + délai de réponse souhaité).
2. Envoi via SMTP ou API (SendGrid, Mailgun...).
3. Suivi automatique à J+2 si pas de réponse.
4. Collecte de réponses et mise à jour du statut du contractor (`interested` / `declined` / `no-response`).
5. Routing vers le processus de qualification si nouveau contractor.

---

### MODULE F — Tarification (Pricing Agent)

**Objectif** : Construire un ou plusieurs scénarios de prix complets et défendables.

#### Composantes de coût

| Poste | Source |
|-------|--------|
| Main d'œuvre sous-traitants | Devis / taux vérifiés en base |
| Matériaux | Devis fournisseurs ou benchmarks |
| Assurance & bonding | Calcul sur % de la valeur contrat |
| Overhead (frais généraux) | Taux configuré par l'entreprise |
| Management fee | % configurable |
| Contingency | % configurable (risque détecté par RFP Analyst) |

#### Outputs
- Scénario conservateur / médian / agressif
- Prix unitaires + total
- Tableau récapitulatif exportable (Excel / JSON)
- Marge prévisionnelle

---

### MODULE G — Rédaction de Proposition (Proposal Agent)

**Objectif** : Générer un premier draft de proposition commerciale et technique conforme aux exigences RFP.

#### Sections générées automatiquement
1. **Cover Letter** — Personnalisée par agence et opportunité.
2. **Executive Summary** — Synthèse de l'approche.
3. **Technical Approach** — Basé sur le scope extrait par le RFP Analyst.
4. **Management Plan** — Organisation, sous-traitants, organigramme.
5. **Past Performance** — Sélection des références pertinentes depuis la base.
6. **Price Volume** — Intégration du pricing scenario validé.
7. **Required Forms** — Pré-remplissage des formulaires identifiés (SF-1449, etc.).

#### Sources de génération
- Templates approbés par l'entreprise (Markdown / DOCX).
- Données validées : profil entreprise, certifications, past performance DB, pricing.
- Extraction RFP structurée.

---

### MODULE H — Vérification de Conformité (Compliance Agent)

**Objectif** : Bloquer la soumission si des éléments obligatoires sont manquants ou invalides.

#### Vérifications automatiques

| Catégorie | Vérifications |
|-----------|--------------|
| **Documents** | Tous les formulaires requis sont présents et signés |
| **Certifications** | SAM.gov registration active, certifications à jour |
| **Assurances** | COI dans les limites requises, non expirée |
| **Bonding** | Capacité suffisante par rapport à la valeur de l'offre |
| **Délais** | Soumission avant la deadline |
| **Format** | Respect des instructions de soumission (format, taille, nb de pages) |

#### Outputs
- ✅ Checklist verte = Proposition prête à soumettre.
- ❌ Blocages listés avec description + action corrective requise.
- Le statut `submitted` n'est atteignable qu'après un scan 100% vert.

---

### MODULE I — Opérations Post-Award (Operations Agent)

**Objectif** : Assurer l'exécution, le suivi qualité et la facturation du contrat obtenu.

#### Создание du Dossier de Projet
- Création automatique d'un `project_record` sur award.
- Affectation des sous-traitants confirmés.
- Génération du planning (milestones issus du RFP).

#### Suivi d'Exécution
- Work orders par sous-traitant avec scope, délai, documents requis.
- Tableau de bord d'avancement (% livrable complété).

#### Quality Control (QC)
- Checklist QC par livrable (issue du contrat).
- Upload des justificatifs / photos / rapports.
- Validation avant facturation.

#### Facturation
- Génération des invoices sur validation QC.
- Traçabilité : lien invoice ↔ livrable ↔ work order.
- Export au format PDF + intégration API comptable (QuickBooks, etc.).

---

## 4. Architecture Technique

### 4.1 Stack Recommandée

| Couche | Technologie |
|--------|-------------|
| **Backend** | Python (FastAPI) |
| **Base de données** | PostgreSQL (production) / JSON local (prototype) |
| **LLM / Agents** | Groq API (llama-3.3-70b-versatile) ou OpenAI GPT-4o |
| **Ingestion PDF** | PyMuPDF + LangChain Document Loaders |
| **Frontend** | React (Next.js) ou HTML/JS natif (prototype) |
| **Email Outreach** | SendGrid / Mailgun |
| **Scheduler** | APScheduler (intégré FastAPI) ou Celery + Redis |
| **Stockage fichiers** | S3-compatible (AWS S3, Cloudflare R2) |
| **Auth** | JWT + rôles (Admin, Reviewer, Ops) |

### 4.2 Structure des Dossiers

```
SAM_Contract/
├── data/
│   ├── opportunities_db.json      ← Base des opportunités
│   └── last_sync.json             ← Dernier horodatage de sync
├── sync.py                        ← Logique d'extraction SAM.gov ✅
├── scheduler.py                   ← Planificateur 3x/jour ✅
├── agents/
│   ├── opportunity_analyst.py     ← Agent Screening
│   ├── rfp_analyst.py             ← Agent Analyse RFP
│   ├── contractor_matchmaker.py   ← Agent Matching
│   ├── outreach_agent.py          ← Agent Outreach
│   ├── pricing_agent.py           ← Agent Pricing
│   ├── proposal_agent.py          ← Agent Rédaction
│   ├── compliance_agent.py        ← Agent Conformité
│   └── operations_agent.py        ← Agent Post-Award
├── api/
│   └── server.py                  ← API FastAPI
├── frontend/
│   └── index.html                 ← Dashboard
└── contractors/
    └── contractors_db.json        ← Base des sous-traitants
```

---

## 5. Exigences Non-Fonctionnelles

| Exigence | Cible |
|----------|-------|
| **Disponibilité** | 99,5% (hors fenêtres maintenance) |
| **Latence screening** | < 2 min par opportunité |
| **Latence analyse RFP** | < 5 min par document |
| **Volume DB** | ≥ 10 000 opportunités actives simultanées |
| **Sécurité** | Chiffrement at-rest + in-transit, accès par rôle |
| **Audit** | Toute action agent et humaine loggée avec timestamp |
| **Conformité** | Respect des CGU SAM.gov (pas de scraping abusif) |

---

## 6. Rôles Utilisateurs

| Rôle | Permissions |
|------|------------|
| **Admin** | Configuration globale, gestion utilisateurs, logs |
| **Business Developer** | Valider bid/no-bid, approuver propositions |
| **Ops Manager** | Suivi post-award, validation QC, invoicing |
| **Sub-traitant** | Portail externe (répondre aux outreach) |

---

## 7. Jalons de Développement

| Phase | Contenu | Statut |
|-------|---------|--------|
| **Phase 0** | Sync SAM.gov + DB JSON + Scheduler | ✅ **Terminé** |
| **Phase 1** | Dashboard d'opportunités + Screening IA + BID/NO-BID | 🔜 Suivant |
| **Phase 2** | RFP Analyst (extraction PDF) | |
| **Phase 3** | Contractors DB + Matchmaker | |
| **Phase 4** | Outreach Agent + Email | |
| **Phase 5** | Pricing Agent + Proposal Agent | |
| **Phase 6** | Compliance Agent + Soumission | |
| **Phase 7** | Operations Agent + QC + Invoicing | |
| **Phase 8** | Production (Auth, PostgreSQL, S3) | |

---

## 8. Acquis Existants (Base de Code Actuelle)

Le module **Opportunity Scout (MODULE A)** est déjà implémenté et opérationnel :

- `sync.py` — Extraction paginée, filtrage, dédoublonnage, nettoyage des expirés.
- `scheduler.py` — Planificateur 3×/jour avec retry automatique.
- Champs extraits : tous les 20 champs définis au MODULE A.
- Filtres actifs : `active=Yes`, `type ∈ {Solicitation, Combined}`, `ptype=o,k`.

---

*Document vivant — à mettre à jour à chaque phase achevée.*

---

## 9. Interfaces Utilisateurs

La plateforme expose **deux interfaces distinctes** avec des accès, des rôles et des objectifs totalement séparés.

---

### 9.1 Interface Admin (Back-office)

> Accessible uniquement à l'équipe interne (Admin, Business Developer, Ops Manager).

#### Dashboard Principal
- Vue consolidée : nombre d'opportunités actives, en cours de traitement, soumises, gagnées.
- Alertes : opportunités dont la deadline approche (< 72h), agents en erreur, documents manquants.

#### Gestion des Opportunités SAM.gov
- Tableau listant toutes les opportunités en base avec colonnes filtrables (statut, NAICS, deadline, score, agence).
- Fiche détaillée de chaque opportunité : champs SAM + résultat du screening IA (score, justification, facteurs bloquants).
- Actions : **✅ Bid** | **🚫 No-Bid** | **📎 Ouvrir RFP** | **💬 Commenter**.
- Vue de l'avancement : pipeline Kanban (New → Screening → Bid → RFP → Pricing → Proposal → Submitted → Awarded).

#### Gestion des Prestataires
- Base de données des prestataires : fiche complète (métiers, zones, licences, assurances, bonding, disponibilités, références).
- Ajout / modification / suspension d'un prestataire.
- Vue des prestataires matchés pour chaque opportunité.
- Historique des collaborations par prestataire.

#### Process de Candidature (par opportunité)
Tableau de bord dédié à chaque opportunité en statut `bid` :

| Étape | Contenu |
|-------|---------|
| Analyse RFP | Visualiser l'extraction structurée, valider les livrables et critères détectés |
| Matching | Voir la shortlist de prestataires, approuver ou écarter |
| Outreach | Voir les emails envoyés, les réponses, relancer manuellement |
| Pricing | Consulter les scénarios de prix, ajuster les marges, valider |
| Proposition | Lire le draft généré, éditer, valider section par section |
| Conformité | Checklist de conformité, débloquer les points rouges |
| Soumission | Déclencher la soumission finale après approbation |

#### Suivi Post-Award
- Liste des contrats actifs avec leur avancement.
- Détail d'un projet : work orders, planning, assignations prestataires.
- Validation des QC checkpoints.
- Génération et envoi des factures.
- Journal de toutes les actions agents + humaines (audit trail).

---

### 9.2 Interface Prestataire (Portail externe)

> Accessible aux sous-traitants après invitation et création de compte.

#### Catalogue des Opportunités
- Vue des opportunités pour lesquelles le prestataire **peut être pertinent** (filtrées par ses métiers déclarés).
- Filtres disponibles : NAICS, état/région, type de set-aside, valeur estimée, date limite.
- Fiche résumée de chaque opportunité : titre, agence, lieu, deadline, scope simplifié.
- Bouton **"Déclarer mon intérêt"** → envoie une demande de participation à l'équipe interne.

#### Suivi des Demandes en Cours
Tableau listant toutes ses candidatures actives :

| Colonne | Détail |
|---------|--------|
| Opportunité | Nom + référence SAM |
| Statut | En attente / Retenu / Écarté / En cours d'exécution |
| Action requise | Document manquant, devis à fournir, information à compléter |
| Deadline | Date limite de réponse à l'action |

#### Messagerie & Documents (par candidature)
- **Chat intégré** avec l'équipe interne pour chaque dossier en cours (questions sur le scope, précisions techniques).
- **Upload de documents** : devis, certificats, licences, COI, références — directement dans le dossier de l'opportunité.
- Notifications email à chaque mise à jour de statut ou nouveau message.

#### Profil & Qualifications
- Mise à jour autonome de son profil : zones d'intervention, licences, assurances, capacité de bonding.
- Upload des documents de qualification (COI, licences, certifications) avec leur date d'expiration.
- Alertes automatiques avant expiration d'un document critique.
