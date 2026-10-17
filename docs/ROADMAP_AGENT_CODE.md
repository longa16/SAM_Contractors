# ROADMAP TECHNIQUE — GovContractAI (repo `SAM_Contractors`)

> **Destinataire : agent de code.** Ce document est ta source de vérité d'implémentation. Il complète `cahier_des_charges.md` (le *quoi*) par le *comment*, les contrats de données, l'ordre de travail et les critères d'acceptation testables.
> **Version** 1.0 — 17 octobre 2026 — Langue des docs : français. Langue du code (identifiants, commentaires, docstrings, messages de commit) : **anglais**, sauf textes affichés à l'utilisateur.

---

## 0. Règles de travail (à lire en premier)

1. **Une phase = une branche = une PR.** Ne commence jamais la phase N+1 avant que tous les critères d'acceptation de la phase N soient verts.
2. **Ne casse pas l'existant** : `sync.py` et `scheduler.py` fonctionnent en production locale. Toute modification passe par la Phase 0.5.
3. **Spec ambiguë ou contradictoire → ne devine pas.** Applique la valeur par défaut indiquée en §11 (« Décisions et hypothèses »), note l'écart dans `docs/DECISIONS.md` (une entrée par décision : contexte, choix, alternatives).
4. **Hors périmètre = interdit.** Respecte les colonnes « Hors scope » de chaque phase. Pas de refactor opportuniste.
5. **Tout agent IA est une fonction typée, testable sans réseau** (LLM mocké). Aucun appel LLM/HTTP réel dans la suite de tests par défaut.
6. **Aucune action irréversible sans validation humaine** : envoi d'email externe, soumission SAM.gov, envoi de facture. Ces actions passent par une file `pending_approval` (§4.3).
7. **Secrets** : uniquement via variables d'environnement / `.env` (déjà ignoré). Jamais dans le code, les logs, les fixtures, les prompts.
8. **Définition de « terminé »** (toutes phases) :
   - [ ] critères d'acceptation de la phase satisfaits, chacun couvert par au moins un test ;
   - [ ] `ruff check`, `ruff format --check`, `mypy --strict` (sur `app/`) et `pytest` verts ;
   - [ ] nouvelles variables documentées dans `.env.example` ;
   - [ ] README/`docs/` mis à jour (commande de lancement, endpoints) ;
   - [ ] chaque action d'agent ou d'humain écrit une ligne dans `audit_log` ;
   - [ ] la case de phase est cochée dans §10 de ce document.

---

## 1. Audit de l'existant — écart entre spec et code réel

Le cahier des charges déclare la Phase 0 « ✅ Terminé ». L'analyse du dépôt (`sync.py`, `scheduler.py`, `data/opportunities_db.json` = 901 offres) montre que **la Phase 0 est fonctionnelle une fois, puis cassée aux exécutions suivantes**. Ces points sont à corriger en **Phase 0.5, avant toute nouvelle fonctionnalité**.

### 1.1 Bugs bloquants (P0)

| # | Constat (preuve) | Impact | Correctif attendu |
|---|---|---|---|
| B1 | `save_db()` écrit une **liste** JSON (`list(db.values())`), mais `load_db()` renvoie cette liste telle quelle ; le reste du code traite `db` comme un **dict** (`notice_id in db`, `db.items()`). | 2ᵉ exécution : le dédoublonnage ne marche jamais (`in` sur une liste de dicts) et `purge_expired` lève `AttributeError`. Cohérent avec `last_sync.json` figé au 2026-10-09. | `load_db()` reconstruit `{noticeId: opp}` ; ajouter un test aller-retour save→load→sync. |
| B2 | `purge_expired` compare `datetime.fromisoformat("2026-10-19")` (naïf) à `datetime.now(tz=utc)` (aware) → `TypeError` silencieusement capturé → l'offre est **gardée**. 514/901 deadlines sont au format date seule, 6 sans fuseau. | Les offres expirées ne sont jamais purgées pour ~58 % des cas. | Fonction unique `parse_deadline(raw) -> datetime (UTC, aware)` : date seule → fin de journée `23:59:59` **heure de l'Est (America/New_York)** puis conversion UTC ; sans fuseau → même hypothèse. Test paramétré sur les 3 formats (10, 19, 25 caractères). |
| B3 | Le dédoublonnage remplace l'enregistrement **entier** (`db[notice_id] = formatted`). | Dès la Phase 1, les champs système (`_status`, screening, décisions humaines) seraient **écrasés** à chaque mise à jour SAM. | Séparer **données SAM** (écrasables) et **données système** (jamais écrasées) — cf. §3 et migration SQLite. |
| B4 | `description` vaut, pour **100 %** des offres, une **URL** `…/noticedesc?noticeid=…` et non le texte. `additionalInfoLink` vaut la chaîne `"None"` pour 901/901 (la spec le liste comme champ utile). | Le screening IA n'aurait aucun texte à analyser. | Étape d'enrichissement : récupérer le texte de description (HTML → texte) à la demande (§5 Phase 1, « screening en 2 étages »). Normaliser `"None"` → `null`. |
| B5 | `_status` est cité dans la spec (« 20 champs ») mais **n'existe pas** dans `format_opp`. | Aucun suivi de pipeline possible. | Introduit par la table `opportunity_state` (§3). |
| B6 | `MAX_PAGES = 5` tronque **silencieusement** à 5 000 offres. Le volume cité est ~2 700/jour : après un arrêt de plusieurs jours, des offres sont perdues sans alerte. | Perte de données. | Boucler jusqu'à `totalRecords` épuisé ; découper la fenêtre par jour si besoin ; logguer un `WARNING` si plafond atteint. |
| B7 | La fenêtre glissante utilise `last_sync` au **jour** près (`%m/%d/%Y`) et `last_sync` n'est sauvé qu'en fin de succès. | Correct mais redondant ; OK si le dédoublonnage (B1) marche. | Conserver, avec recouvrement de **1 jour** volontaire. |

### 1.2 Écarts spec ↔ code (P1)

| # | Spec | Code | Correctif |
|---|---|---|---|
| E1 | Exécution à **00h00 / 08h00 / 16h00 UTC** | `sleep(8h)` à partir du lancement (dérive, horaires non fixes, pas d'exécution immédiate après redémarrage si le créneau a été manqué) | APScheduler `CronTrigger(hour="0,8,16", timezone="UTC")` + rattrapage au démarrage si `last_sync` > 8 h |
| E2 | Retry automatique sur erreur réseau | Une erreur réseau `break` la pagination et **sauvegarde quand même** un état partiel + avance `last_sync` | Distinguer échec partiel : ne **pas** avancer `last_sync` si une page échoue ; retry exponentiel (3 essais) par page |
| E3 | Appel API avec clé en query string | Clé dans l'URL potentiellement loggée en cas d'erreur | Ne jamais logger l'URL complète ; masquer `api_key` |
| E4 | Filtre `active = Yes` | Paramètre `is_active=true` envoyé à l'API **dont le nom/le support est à vérifier** dans la doc officielle ; le filtre local est correct | Garder le filtre local (défense en profondeur) ; vérifier le paramètre réel |
| E5 | Structure `SAM_Contract/…` | Repo nommé `SAM_Contractors`, package `sam-contract`, fichiers à la racine | Décision en §11 (D1) |
| E6 | Logs d'audit exigés (§5 spec) | `print()` partout | Module `logging` structuré (JSON) + table `audit_log` |

### 1.3 Hygiène du dépôt (P2)

- `__pycache__/` est **commité** (et référence des modules absents : `main`, `sam_tool`, `server`) → `git rm -r --cached __pycache__` et l'ajouter à `.gitignore`.
- `data/opportunities_db.json` (1,3 Mo) est commité → l'ignorer (`data/`), fournir `data/.gitkeep` et un script de seed.
- `pyproject.toml` : `description` à renseigner ; dépendances manquantes pour la suite (cf. §2.4).
- Aucun test, aucun linter, aucun `.env.example`.
- Le cahier des charges contient un mot russe parasite (« Создание du Dossier de Projet », MODULE I) : lire « Création du dossier de projet ».

### 1.4 Contraintes de données découvertes (conditionnent la conception)

| Fait mesuré sur 901 offres | Conséquence de conception |
|---|---|
| 200 offres seulement (22 %) ont des `resourceLinks` ; 701 n'en ont aucun | Le RFP Analyst **ne peut pas** supposer des PDF disponibles. Prévoir l'état `rfp_docs_missing` + **upload manuel** + repli sur le texte de description (cf. Phase 2). |
| 745 offres sans `placeOfPerformance` | La dimension « Géographie » du screening doit gérer l'inconnu (score neutre + facteur « lieu non précisé »), et utiliser `officeAddress` en repli. |
| Valeurs `typeOfSetAside` : `SBA` (355), `null` (277), `''` (136), `NONE` (44), `SDVOSBC` (41), `HZC` (20), `WOSB` (16), `8A` (6) | Normaliser en table de correspondance code → libellé → catégorie ; `null`/`''`/`NONE` = « pas de set-aside ». |
| Types : 631 « Combined Synopsis/Solicitation », 270 « Solicitation » | Le filtre de type est correct. |
| Aucun doublon de `noticeId` dans la base actuelle | À re-vérifier après correction B1 (le dédoublonnage n'a jamais fonctionné). |
| Obtenir le texte d'une description nécessite **1 appel API par offre** | **Contrainte de quota majeure** : le quota de la clé SAM.gov (à vérifier ; les clés personnelles sans rôle sont fortement limitées par jour) interdit d'enrichir 900+ offres. D'où le screening **en 2 étages** (Phase 1). |

---

## 2. Architecture cible et conventions

### 2.1 Principe : monolithe modulaire

Un seul service FastAPI + un worker de planification, organisé en modules à frontières nettes. Pas de microservices en V1. Chaque module expose une **interface Python** (service) ; l'API HTTP n'est qu'une couche fine par-dessus.

```
SAM_Contractors/
├── pyproject.toml
├── .env.example
├── README.md
├── docs/
│   ├── DECISIONS.md              # journal des décisions (ADR courts)
│   └── ROADMAP_AGENT_CODE.md     # ce document
├── app/
│   ├── main.py                   # create_app(), montage des routers
│   ├── config.py                 # pydantic-settings (env) + chargement company_profile.yaml
│   ├── logging_setup.py          # logs JSON, masquage des secrets
│   ├── db/
│   │   ├── base.py               # engine, session, Base
│   │   ├── models/               # un fichier par agrégat (opportunity.py, contractor.py, ...)
│   │   └── migrations/           # Alembic
│   ├── schemas/                  # modèles Pydantic (I/O API + sorties d'agents)
│   ├── repositories/             # accès données (aucune logique métier)
│   ├── services/                 # logique métier + transitions d'état
│   ├── sam/                      # client SAM.gov, parsing, normalisation (ex-sync.py)
│   ├── llm/
│   │   ├── client.py             # interface LLMClient + implémentation Groq (+ OpenAI optionnelle)
│   │   ├── structured.py         # appel -> JSON -> validation Pydantic -> retry réparation
│   │   └── prompts/              # un fichier .md ou .j2 par prompt, versionné
│   ├── agents/                   # 1 fichier par agent (cf. §6)
│   ├── scheduler/                # jobs APScheduler
│   ├── api/
│   │   ├── deps.py               # auth, session, rôles
│   │   └── routers/              # opportunities.py, screening.py, rfp.py, contractors.py, ...
│   └── storage/                  # abstraction fichiers (local -> S3)
├── frontend/                     # Phase 1 : HTML/JS natif ; Phase 8 : Next.js optionnel
├── config/
│   ├── company_profile.yaml      # profil entreprise (cf. §12.2)
│   └── templates/                # templates de proposition (Markdown/DOCX)
├── data/                         # ignoré par git (sauf .gitkeep)
├── scripts/
│   ├── import_json_to_db.py      # migration opportunities_db.json -> SQLite
│   └── seed_contractors.py
└── tests/
    ├── unit/  ├── integration/  ├── fixtures/ (échantillons SAM anonymisés)  └── conftest.py
```

> `sync.py` et `scheduler.py` à la racine sont **déplacés** vers `app/sam/` et `app/scheduler/` en Phase 0.5, avec des *shims* à la racine (`python sync.py`, `python scheduler.py` continuent de fonctionner) jusqu'à la fin de la Phase 1.

### 2.2 Conventions de code (obligatoires)

- Python **3.13**, gestionnaire **uv** (déjà utilisé). Typage complet ; `mypy --strict` sur `app/`.
- **Pydantic v2** pour toute donnée qui traverse une frontière (API, LLM, fichier).
- **SQLAlchemy 2.x** (style typé `Mapped[...]`) + **Alembic**. Aucune requête SQL dans les routers.
- `ruff` (lint + format, ligne ≤ 100). Pas de `print` : `logging` uniquement.
- Fonctions courtes, une responsabilité ; pas de logique métier dans les routers ni dans les prompts.
- Toutes les dates en **UTC aware** en base ; conversion de fuseau uniquement à l'affichage.
- Tout ce qui est paramétrable (seuils, taux, modèles LLM, horaires) vient de `config.py` / `company_profile.yaml`, **jamais** codé en dur.
- Erreurs : exceptions métier typées (`DomainError` + sous-classes) converties en réponses HTTP par un handler unique.
- Commits : Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`).

### 2.3 Idempotence et reprise

Chaque job/agent doit pouvoir être relancé sans effet de bord dupliqué : clé d'idempotence = `(entity_id, agent_name, input_hash)`. Si une `agent_run` réussie existe pour le même `input_hash`, retourner le résultat stocké (sauf paramètre `force=true`).

### 2.4 Dépendances à ajouter (au fil des phases)

| Phase | Paquets |
|---|---|
| 0.5 | `sqlalchemy>=2`, `alembic`, `pydantic-settings`, `apscheduler>=3.10`, `tenacity`, `httpx`, `pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy` |
| 1 | `beautifulsoup4` (HTML→texte), `jinja2` |
| 2 | `pymupdf`, `python-multipart`, `pdfplumber` (tableaux), `pytesseract` (OCR optionnel, derrière un flag) |
| 3-4 | `sendgrid` ou `httpx` vers Mailgun, `email-validator` |
| 5-6 | `openpyxl` (export Excel), `python-docx`, `markdown`, `weasyprint` ou `reportlab` (PDF) |
| 8 | `psycopg[binary]`, `boto3`, `python-jose`/`pyjwt`, `passlib[argon2]` |

---

## 3. Modèle de données

> Cible : **SQLite en dev, PostgreSQL en prod** avec le même schéma (SQLAlchemy). Les JSON d'origine sont importés par `scripts/import_json_to_db.py` (décision D2).
> Principe directeur (corrige B3) : **les données SAM sont séparées des données système**. Une re-synchronisation met à jour `opportunity` mais ne touche jamais aux tables `opportunity_state`, `screening_result`, etc.

Notation : `PK` clé primaire, `FK` clé étrangère, `?` nullable, `JSON` colonne JSON validée par un schéma Pydantic.

### 3.1 Cœur « opportunités »

**`opportunity`** (données SAM, écrasables)
`notice_id PK` · `solicitation_number` (index) · `title` · `type` · `description_url` · `description_text?` · `description_fetched_at?` · `point_of_contact JSON` · `additional_info_link?` (null si `"None"`) · `agency_path` (= `fullParentPathName`) · `agency_code` · `organization_type` · `office_address` · `naics_code` (index) · `classification_code` · `set_aside_code?` · `set_aside_label?` · `set_aside_category` (enum normalisée, cf. §12.3) · `posted_date` (date) · `response_deadline_utc` (datetime UTC, index) · `response_deadline_raw` (str d'origine) · `active` (bool) · `place_of_performance_raw?` · `pop_state?` (code 2 lettres, extrait) · `resource_links JSON` (liste d'URL — **liste**, plus la chaîne `" | "`) · `raw_payload JSON` (réponse SAM brute, pour re-parsing) · `synced_at` · `first_seen_at`.

**`opportunity_state`** (données système, jamais écrasées par la sync) — 1 ligne par opportunité
`notice_id PK/FK` · `status` (enum §4.1) · `status_changed_at` · `status_changed_by` · `screening_stage` (`none|stage1|stage2`) · `bid_decision?` (`bid|no_bid`) · `bid_decision_reason?` · `assigned_to?` (FK user) · `pipeline_stage` (enum Kanban §4.2) · `updated_at`.

**`opportunity_comment`** : `id` · `notice_id` · `author_id` · `body` · `created_at`.

**`screening_result`** : `id` · `notice_id` · `stage` (1|2) · `score` (0-100) · `recommendation` (`RECOMMENDED|REVIEW|PASS`) · `dimension_scores JSON` (9 dimensions §Phase 1) · `positive_factors JSON[]` · `blocking_factors JSON[]` · `estimated_value?` · `model` · `prompt_version` · `input_hash` · `created_at`. *Conserver l'historique ; la vue courante = ligne la plus récente.*

### 3.2 Analyse RFP

**`rfp_document`** : `id` · `notice_id` · `source` (`resource_link|additional_info|manual_upload`) · `source_url?` · `filename` · `storage_key` · `sha256` (index, dédoublonnage) · `mime` · `pages?` · `text_extracted?` · `extraction_method` (`text|ocr`) · `is_amendment` (bool) · `downloaded_at`.
**`rfp_analysis`** : `id` · `notice_id` · `version` (int, +1 par ré-analyse) · `data JSON` (schéma `RfpExtraction`, §12.1) · `confidence JSON` (par champ) · `validated_by?` · `validated_at?` · `created_at`.

### 3.3 Prestataires

**`contractor`** : `id` · `legal_name` · `contact_name` · `email` · `phone?` · `status` (`invited|active|suspended`) · `trades JSON[]` · `naics_codes JSON[]` · `home_location` · `service_areas JSON[]` (codes d'états + rayon optionnel) · `bonding_capacity?` (USD) · `certifications JSON[]` (`8a|hubzone|sdvosb|wosb|small_business…`) · `availability JSON` (plages de dates) · `created_at`.
**`contractor_license`** : `id` · `contractor_id` · `state` · `specialty` · `number` · `expires_on`.
**`contractor_document`** : `id` · `contractor_id` · `kind` (`coi|license|certification|reference|other`) · `storage_key` · `expires_on?` · `coverage_amounts JSON?` · `uploaded_at`.
**`contractor_past_project`** : `id` · `contractor_id` · `title` · `client` · `value?` · `year` · `verified` (bool) · `reference_contact?`.
**`contractor_match`** : `id` · `notice_id` · `contractor_id` · `hard_filter_passed` (bool) · `score` · `breakdown JSON` · `approved_by_human?` (`approved|rejected|null`) · `created_at`.

### 3.4 Outreach, pricing, proposition, conformité

**`outreach_thread`** : `id` · `notice_id` · `contractor_id` · `status` (`draft|pending_approval|sent|interested|declined|no_response`) · `followup_count` · `next_followup_at?`.
**`outreach_message`** : `id` · `thread_id` · `direction` (`out|in`) · `subject` · `body` · `provider_message_id?` · `sent_at?` · `approved_by?`.
**`pricing_scenario`** : `id` · `notice_id` · `kind` (`conservative|median|aggressive`) · `assumptions JSON` · `line_items JSON[]` · `totals JSON` (coût, overhead, mgmt fee, contingency, prix, marge €/%) · `status` (`draft|validated`) · `validated_by?` · `created_at`.
**`proposal`** : `id` · `notice_id` · `version` · `status` (`draft|in_review|approved`) · `created_at` ; **`proposal_section`** : `id` · `proposal_id` · `key` (`cover_letter|executive_summary|technical_approach|management_plan|past_performance|price_volume|required_forms`) · `content_md` · `generated_by_model` · `edited_by_human` (bool) · `validated` (bool).
**`compliance_report`** : `id` · `notice_id` · `proposal_id` · `checks JSON[]` (`{category, code, status: pass|fail|warn|na, detail, corrective_action}`) · `all_green` (bool) · `created_at`.

### 3.5 Post-award

**`project`** : `id` · `notice_id` · `contract_number?` · `award_date` · `value` · `status` · `milestones JSON[]`.
**`work_order`** : `id` · `project_id` · `contractor_id` · `scope` · `due_date` · `required_documents JSON[]` · `progress_pct` · `status`.
**`qc_checkpoint`** : `id` · `work_order_id` · `deliverable` · `checklist JSON[]` · `evidence_keys JSON[]` · `status` (`pending|passed|failed`) · `validated_by?`.
**`invoice`** : `id` · `project_id` · `work_order_id?` · `deliverable_ref` · `amount` · `status` (`draft|pending_approval|sent|paid`) · `pdf_key?` · `external_id?`.

### 3.6 Transverse

**`user`** : `id` · `email` (unique) · `password_hash` · `role` (`admin|business_developer|ops_manager|subcontractor`) · `contractor_id?` (si prestataire) · `is_active` · `created_at`.
**`audit_log`** (append-only) : `id` · `ts_utc` · `actor_type` (`user|agent|system`) · `actor_id` · `action` · `entity_type` · `entity_id` · `before JSON?` · `after JSON?` · `meta JSON?`.
**`agent_run`** : `id` · `agent_name` · `entity_id` · `input_hash` · `status` (`running|success|failed`) · `model` · `prompt_version` · `tokens_in/out` · `latency_ms` · `error?` · `output JSON?` · `started_at` · `finished_at`.
**`approval_request`** : `id` · `kind` (`outreach_send|submission|invoice_send|…`) · `entity_id` · `payload JSON` · `status` (`pending|approved|rejected`) · `requested_by` · `decided_by?` · `decided_at?`.
**`sync_run`** : `id` · `started_at` · `finished_at` · `window_from` · `window_to` · `fetched` · `inserted` · `updated` · `skipped_*` · `purged` · `status` · `error?`.

---

## 4. Machine à états, validation humaine, audit

### 4.1 Statut macro de l'opportunité (`opportunity_state.status`)

La spec liste `new / screening / bid / no-bid / submitted / awarded`. On les conserve et on **ajoute** les états manquants nécessaires au pipeline et aux cas d'échec :

```
new ──► screening ──► bid ──► (pipeline_stage détaille RFP→…→Compliance) ──► submitted ──► awarded
  │         │           │                                                          │
  │         └──► no-bid │                                                          └──► lost
  └──► expired (deadline dépassée, état terminal, purge différée)
```

Valeurs : `new | screening | bid | no-bid | submitted | awarded | lost | expired`.
Transitions autorisées **uniquement** via `OpportunityStateService.transition(notice_id, to, actor, reason)` qui vérifie la matrice, écrit `audit_log`, et lève `InvalidTransition` sinon. Interdictions notables :
- `submitted` n'est atteignable que si le dernier `compliance_report.all_green == true` **et** qu'une `approval_request` `submission` est `approved` (spec §H).
- `bid` / `no-bid` : réservé aux rôles `business_developer` et `admin`.
- Une opportunité `expired` ne peut plus être `bid`.

### 4.2 Étapes Kanban (`pipeline_stage`), actives quand `status = bid`

`rfp → matching → outreach → pricing → proposal → compliance → ready_to_submit`. Le Kanban de l'interface admin (§9.1 spec) = `new, screening, bid` (statuts) + ces étapes + `submitted, awarded`.
Chaque étape avance **automatiquement** quand l'agent a terminé, mais **ne passe à l'étape suivante qu'après validation humaine** là où la spec le demande (RFP validé, shortlist approuvée, pricing validé, proposition validée).

### 4.3 File d'approbation (human-in-the-loop)

Toute action externe ou irréversible crée une `approval_request` `pending` au lieu de s'exécuter : envoi d'email aux prestataires (au moins pour le 1ᵉʳ contact d'un prestataire), soumission, envoi de facture. L'exécution réelle a lieu **dans le handler d'approbation**, jamais dans l'agent. Un paramètre `AUTO_APPROVE_OUTREACH` (défaut `false`) permet d'assouplir plus tard.

### 4.4 Audit

Décorateur/contexte `audited(action, entity)` appliqué à **chaque** méthode de service qui modifie l'état. `audit_log` est append-only (aucun UPDATE/DELETE exposé). Les `agent_run` tracent coût et latence (exigence « < 2 min screening », « < 5 min analyse RFP »).

---

## 5. Plan de réalisation par phase

Ordre : **0.5 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8**. Chaque phase suit le gabarit : *Objectif · Livrables · Spécifications détaillées · Critères d'acceptation (AC) · Tests · Hors scope*.

---

### PHASE 0.5 — Stabilisation et fondations (prérequis de tout le reste)

**Objectif** : corriger les bugs §1.1, poser la base technique (DB, config, logs, tests, CI) sans changer le comportement fonctionnel visible.

**Livrables**
- Squelette `app/` §2.1 ; `pyproject.toml` complété ; `.env.example` ; `.gitignore` corrigé (`__pycache__/`, `data/*` sauf `.gitkeep`, `*.db`) ; `git rm -r --cached __pycache__ data/*.json`.
- `app/config.py` : `Settings` (SAM_API_KEY, GROQ_API_KEY, DATABASE_URL défaut `sqlite:///data/app.db`, SCREENING_*, LOG_LEVEL…).
- Modèles SQLAlchemy `opportunity`, `opportunity_state`, `sync_run`, `audit_log`, `agent_run`, `user` + migration Alembic initiale.
- `scripts/import_json_to_db.py` : import idempotent de `data/opportunities_db.json` (+ création de `opportunity_state(status='new')`). Un fichier d'origine 901 offres doit s'importer sans perte.
- `app/sam/client.py` (httpx, retry `tenacity`, masquage de clé), `app/sam/normalize.py` (`format_opp`, `parse_deadline`, `normalize_set_aside`, `extract_pop_state`), `app/sam/sync_service.py` (`run_sync()`), `app/scheduler/jobs.py` (APScheduler cron UTC).
- Shims `sync.py` / `scheduler.py` à la racine.
- CI GitHub Actions : ruff + mypy + pytest.

**Spécifications détaillées**
1. `parse_deadline(raw: str | None) -> datetime | None` : gère `YYYY-MM-DD` (→ 23:59:59 America/New_York → UTC), `YYYY-MM-DDTHH:MM:SS` (idem fuseau Est), ISO avec offset, suffixe `Z`, chaîne vide/None → `None`. Ne lève jamais.
2. `run_sync()` : (a) calcule la fenêtre `[last_sync - 1 jour, now]` ; (b) pagine **jusqu'à épuisement** (`offset += 1000` tant que `offset < totalRecords`) ; garde-fou à 50 pages avec `WARNING` ; (c) **upsert** dans `opportunity` sur `notice_id` en ne remplaçant que si `posted_date` ≥ celle en base ; (d) ne touche jamais `opportunity_state` sauf création pour une nouvelle offre ; (e) purge : passe `status → expired` pour les offres dont `response_deadline_utc < now` (**ne supprime pas** physiquement avant J+30, pour ne pas casser les dossiers liés ; suppression physique seulement si `status in (new, no-bid, expired)` et pas de lien) ; (f) écrit un `sync_run` ; (g) n'avance `last_sync` que si **toutes** les pages ont réussi.
3. Planification : `CronTrigger(hour="0,8,16", minute=0, timezone="UTC")` ; au démarrage, si `now - last_success > 8h`, exécuter immédiatement un rattrapage. Un seul `run_sync` à la fois (verrou : `sync_run.status='running'` + timeout de péremption 30 min).
4. Logging JSON (`ts, level, module, event, **fields`) ; `SAM_API_KEY` jamais présent dans les logs (test automatisé qui grep un log capturé).
5. `description_url` conservée ; `description_text` rempli **plus tard** (Phase 1) — ne pas appeler l'endpoint de description pendant la sync.

**AC**
- P05-AC1 : deux `run_sync()` consécutifs (API mockée avec `respx`) ne créent aucun doublon et ne lèvent pas d'exception (régression B1).
- P05-AC2 : `parse_deadline` renvoie une datetime **aware UTC** pour les 3 formats observés ; test paramétré avec ≥ 8 cas incluant bords d'heure d'été.
- P05-AC3 : une offre dont la deadline est passée (format date seule) passe en `expired` (régression B2).
- P05-AC4 : modifier `opportunity_state.status` puis relancer une sync qui met à jour la même offre **conserve** le statut (régression B3).
- P05-AC5 : si la page 2 d'une pagination échoue (réseau), `last_sync` n'est pas avancé et `sync_run.status='failed'`.
- P05-AC6 : avec `totalRecords=4200` simulé, 5 pages sont récupérées (pas de troncature à 5 000 → test avec 6 pages).
- P05-AC7 : `scripts/import_json_to_db.py` importe les 901 offres actuelles, relancé deux fois = 901 lignes.
- P05-AC8 : `pytest -q`, `ruff`, `mypy --strict app/` verts en CI.

**Hors scope** : toute logique IA, toute interface, authentification complète.

---

### PHASE 1 — Dashboard, Screening IA, décision BID / NO-BID

**Objectif** : l'utilisateur voit chaque jour la liste triée des opportunités pertinentes avec score et justification, et décide *Bid / No-Bid* en un clic.

**Livrables**
- `app/llm/` (client Groq + sortie structurée validée) ; `app/agents/opportunity_analyst.py` ; prompts versionnés.
- `config/company_profile.yaml` + schéma Pydantic `CompanyProfile` (§12.2).
- Auth minimale (D4) : `user`, login JWT, dépendance `require_role(...)`, utilisateur admin créé par script `scripts/create_admin.py`.
- API : voir §7 (routers `auth`, `opportunities`, `screening`).
- `frontend/index.html` (+ `app.js`, `styles.css`) : dashboard.
- Job de screening planifié après chaque sync.

**Spécifications détaillées — screening en deux étages** (impératif à cause du quota API, §1.4)

*Étage 1 — « triage » sans texte de description, 100 % local, < 50 ms/offre, aucun LLM.*
Score déterministe sur les dimensions calculables à partir des champs SAM :

| Dimension (spec) | Règle déterministe |
|---|---|
| Capacité | `naics_code ∈ profile.naics_primary` → 100 ; `∈ naics_secondary` → 60 ; même préfixe 4 chiffres → 30 ; sinon 0. `classification_code ∈ profile.psc_codes` → bonus +10 (plafonné à 100). |
| Géographie | `pop_state ∈ profile.service_states` → 100 ; lieu inconnu → 50 (facteur « lieu non précisé ») ; sinon 0. |
| Set-aside | éligibilité selon `profile.certifications` : set-aside pour lequel l'entreprise **n'est pas** éligible (ex. `8A`, `HZC`, `SDVOSBC`, `WOSB` sans la certification) → **blocage dur** (PASS) ; `SBA` si `profile.small_business` ; aucun set-aside → 100. |
| Timeline | jours entre maintenant et la deadline : `< profile.min_days_to_respond` → blocage dur ; 100 si ≥ `ideal_days` ; interpolation linéaire entre les deux. |
| Taille de contrat | non disponible à l'étage 1 → neutre (50), marqué `unknown`. |

Score étage 1 = somme pondérée (poids dans `profile.screening.weights`, normalisés). Blocage dur ⇒ `recommendation = PASS` immédiat, avec `blocking_factors` explicites. Les offres avec score étage 1 ≥ `stage1_threshold` (défaut 40) ET non bloquées passent à l'étage 2, dans la limite de `stage2_daily_cap` (défaut 100, triées par score décroissant) pour borner quota API et coût LLM.

*Étage 2 — analyse approfondie (LLM).*
1. Récupérer le texte de description (`GET description_url` + `api_key`), convertir HTML→texte, tronquer à `MAX_DESC_CHARS` (défaut 24 000), stocker dans `opportunity.description_text`. Si l'appel échoue (quota, 4xx/5xx) → étage 2 reporté, l'offre reste `stage1` avec un indicateur `needs_description` (pas d'échec silencieux).
2. Appeler le LLM **uniquement** pour ce qui vient du texte : licences/assurances requises, bonding, contraintes prime/sub, valeur estimée, risques (périmètre flou, pénalités, annulation), mots-clés de capacité. Sortie JSON stricte `ScreeningLLMOutput` (§12.1) : pas de calcul de score par le LLM sur les dimensions déjà déterministes.
3. Fusion en code : `score_final = Σ poids_d × score_d`, `recommendation` par seuils (`≥ 70` RECOMMENDED, `40–69` REVIEW, `< 40` PASS — configurables), blocages durs prioritaires (ex. bonding requis > `profile.max_bonding` ⇒ PASS).
4. Persister `screening_result`, passer `opportunity_state.status` de `new` à `screening` (RECOMMENDED/REVIEW) ou laisser `new` avec `recommendation=PASS` visible dans un filtre « Passés ».
5. Chaque facteur est une phrase courte lisible par un humain, en **français**, avec une `evidence` (extrait ≤ 200 caractères de la description) quand il vient du LLM. Interdit d'inventer : si le texte ne dit rien sur le bonding, `bonding_required = null`, jamais 0.

*Garde-fous LLM* (voir §6) : température 0–0.2 ; validation Pydantic ; 1 retry de « réparation » avec le message d'erreur de validation ; si échec → `agent_run.failed`, l'offre reste à l'étage 1 et apparaît avec le badge « analyse IA indisponible ».

*Interface dashboard* (`frontend/`) : HTML/JS natif servi par FastAPI (`StaticFiles`).
- Liste de **cartes** `RECOMMENDED` puis `REVIEW` : titre, agence (dernier niveau de `agency_path`), deadline (compte à rebours, rouge si < 72 h), score (jauge), NAICS, lieu (`place_of_performance` ou `office_address`), set-aside (libellé), valeur estimée (ou « n/c »).
- Détail (volet latéral) : champs SAM, facteurs positifs/bloquants avec extraits, lien SAM.gov, historique des commentaires.
- Boutons **✅ Bid / 🚫 No-Bid / 💬 Discuss** → `POST /api/opportunities/{id}/decision` ; No-Bid exige un motif court (liste + texte libre) pour alimenter plus tard l'amélioration du screening ; *Discuss* ajoute un commentaire sans changer le statut.
- Filtres : statut, score min, NAICS, état, set-aside, deadline ; tri par score ou deadline ; pagination côté serveur.
- Encart d'état : date/heure de la dernière sync, nombre d'offres à l'étage 1/2, alertes (agent en erreur).
- Accessibilité minimale : navigation clavier, contrastes, pas de dépendance CDN obligatoire (fonctionne hors-ligne en dev).

**AC**
- P1-AC1 : sur l'échantillon de test (≥ 50 offres réelles anonymisées en `tests/fixtures/`), l'étage 1 produit un score et des facteurs pour chacune en < 2 s au total, sans réseau.
- P1-AC2 : une offre `set-aside=8A` avec un profil sans 8(a) est `PASS` avec un `blocking_factor` mentionnant le set-aside, sans aucun appel LLM.
- P1-AC3 : une offre avec deadline dans moins de `min_days_to_respond` est bloquée (PASS).
- P1-AC4 : avec un LLM simulé renvoyant un JSON invalide, puis valide au retry, le résultat est enregistré ; avec deux réponses invalides, `agent_run.status='failed'` et l'offre reste visible avec le badge « analyse IA indisponible ».
- P1-AC5 : le LLM n'est **jamais** appelé pour une offre bloquée dur ou sous `stage1_threshold`, ni au-delà de `stage2_daily_cap` (test avec compteur sur le mock).
- P1-AC6 : `POST decision` fait passer `screening → bid|no-bid`, écrit `audit_log` (acteur, avant/après), refuse un rôle non autorisé (403) et une transition invalide (409).
- P1-AC7 : relancer le screening sur la même entrée (même `input_hash`) ne crée pas de nouvel appel LLM (idempotence §2.3) ; `force=true` le recrée en gardant l'historique.
- P1-AC8 : latence mesurée d'un screening complet (étages 1+2, LLM réel Groq) < 120 s ; test marqué `@pytest.mark.live`, exclu par défaut.
- P1-AC9 : le dashboard affiche les opportunités RECOMMENDED/REVIEW, les filtres fonctionnent, et le bouton Bid met à jour la carte sans rechargement (test manuel documenté + tests API).
- P1-AC10 : aucun texte de description complet n'est écrit dans les logs.

**Tests** : unitaires étage 1 (tables de cas), contrat Pydantic du LLM, intégration API (FastAPI `TestClient`) pour auth/rôles/transitions, snapshot du prompt rendu.

**Hors scope** : analyse des PDF/pièces jointes (Phase 2), valeur estimée par recherche externe, apprentissage à partir des No-Bid.

---

### PHASE 2 — RFP Analyst (extraction structurée)

**Objectif** : pour une opportunité `bid`, produire un JSON `RfpExtraction` complet, vérifiable par l'humain, exploitable par les agents suivants.

**Livrables** : `app/agents/rfp_analyst.py` · `app/services/rfp_documents.py` (collecte + extraction texte) · `app/storage/` (local, interface compatible S3) · routers `rfp` · écran « Analyse RFP » dans l'interface admin.

**Spécifications détaillées**
1. **Collecte des documents** (par ordre) : `resource_links` → `additional_info_link` (si non nul, seulement si c'est un lien direct vers un fichier) → **upload manuel** (`POST /api/opportunities/{id}/rfp/documents`, PDF/DOCX/XLSX, ≤ 50 Mo). Chaque fichier est hashé (`sha256`) pour le dédoublonnage. *Les liens de ressources SAM peuvent nécessiter `api_key` ; vérifier et gérer.*
2. **Cas « aucun document »** (≈ 78 % des offres, §1.4) : statut `rfp_docs_missing`, l'analyse tourne quand même sur `description_text` avec `confidence` réduite et un bandeau « Documents non fournis — analyse partielle ». L'UI propose l'upload.
3. **Extraction texte** : PyMuPDF par page (conserver le n° de page) ; si une page a < 50 caractères utiles et que `OCR_ENABLED=true`, OCR de cette page ; les tableaux (critères d'évaluation, CLINs/prix) via `pdfplumber`.
4. **Analyse LLM par étapes** (documents longs) : découpage en sections (≈ 6 000 tokens, chevauchement 10 %) → extraction partielle par section → fusion/dédoublonnage → passe de **consolidation** qui résout les contradictions. Respecter la cible < 5 min/document (paralléliser les sections, `asyncio.Semaphore(4)`).
5. **Schéma de sortie** : `RfpExtraction` (§12.1) = le JSON du cahier des charges **plus** : `source_refs` (pour chaque `deliverable`, `mandatory_requirement`, `evaluation_criteria` : `{doc, page, quote ≤ 200 car.}`), `confidence` par champ (0–1), `open_questions[]` (ce qui est ambigu, à poser avant `questions_cutoff`), `clins[]` (si présents), `place_of_performance_detail`, `submission_instructions` (méthode, adresse/email/portail, format, nb de pages max, polices) — **indispensable à la Phase 6**.
6. **Amendements** : à chaque sync, si une offre a la même `solicitation_number` qu'un dossier en cours mais un nouveau `notice_id`/documents, créer un `rfp_document(is_amendment=true)` et marquer l'analyse `stale` ; nouvelle analyse = `version+1` avec un **diff** lisible (champs ajoutés/modifiés).
7. **Validation humaine** : l'écran affiche l'extraction à côté des extraits sources ; l'utilisateur peut corriger chaque champ ; `validated_by/at` renseignés ; seul un RFP validé débloque `pipeline_stage = matching`.
8. **Règle anti-hallucination** : une exigence sans `source_refs` valide (le `quote` doit être retrouvé verbatim dans le texte du document, vérification en code) est marquée `unverified` et surlignée en orange.

**AC**
- P2-AC1 : sur 3 RFP PDF d'exemple (fixtures) l'extraction valide le schéma, chaque `mandatory_requirement` a un `quote` retrouvable dans le texte source.
- P2-AC2 : un PDF scanné (sans couche texte) est traité par OCR si activé, sinon l'analyse passe en `needs_ocr` sans erreur.
- P2-AC3 : une offre sans document : `rfp_docs_missing` + analyse partielle sur la description ; l'upload manuel relance l'analyse.
- P2-AC4 : deux fois le même fichier = un seul `rfp_document` (même `sha256`).
- P2-AC5 : un amendement crée `version=2` avec diff et marque l'ancienne validation « à revalider ».
- P2-AC6 : un document de ~150 pages est analysé en < 5 min (test `live`), les sections tournent en parallèle (≤ 4 appels simultanés vérifié sur mock).
- P2-AC7 : un `quote` inventé par un LLM simulé est détecté et l'élément passe `unverified`.

**Hors scope** : analyse d'images/plans, traduction, signature de formulaires.

---

### PHASE 3 — Base prestataires + Contractor Matchmaker

**Objectif** : gérer les prestataires et obtenir, pour une opportunité `bid`, une shortlist triée et justifiée.

**Livrables** : modèles §3.3 · `app/agents/contractor_matchmaker.py` (**100 % déterministe**, pas de LLM requis) · CRUD prestataires (admin) · `scripts/seed_contractors.py` (≥ 15 prestataires fictifs réalistes) · écrans « Prestataires » et « Matching ».

**Spécifications détaillées**
1. **Filtres durs** (un prestataire exclu garde sa trace dans `contractor_match.hard_filter_passed=false` + raison) : `status = active` ; au moins un `trade` ou `naics` couvrant l'opportunité (via `rfp_analysis.scope` + NAICS) ; zone d'intervention couvre `pop_state` (si lieu inconnu → prestataire conservé avec avertissement) ; **toutes** les licences obligatoires détectées dans `mandatory_requirements` existent pour l'état concerné **et** ne sont pas expirées à la date de fin de période d'exécution ; COI valide.
2. **Score de compatibilité** (0–100, pondérations configurables, défaut) : disponibilité sur la période (30) · capacité/bonding vs valeur estimée (25) · certifications utiles (set-aside de l'offre) (15) · expérience pertinente — `past_projects` vérifiés dans le même NAICS/agence (20) · réactivité historique (taux de réponse aux outreach) (10). Retourner `breakdown` par critère.
3. Shortlist = top N (défaut 8) triée par score, avec l'**explication** de chaque exclusion et chaque point faible.
4. **Approbation humaine** : l'admin approuve/écarte chaque prestataire (`approved_by_human`). Seuls les approuvés alimentent la Phase 4.
5. **Alerte d'expiration** : job quotidien listant les `contractor_document` expirant dans < 30 jours (consommé par le portail en Phase 4/8).

**AC**
- P3-AC1 : jeu de données de test (10 prestataires, 1 opportunité) → le filtre dur exclut exactement ceux attendus et la raison est lisible.
- P3-AC2 : un prestataire avec licence expirant avant la fin de période est exclu avec la raison « licence expire le … ».
- P3-AC3 : le tri est déterministe (mêmes entrées ⇒ même ordre ; départage par `contractor_id`).
- P3-AC4 : CRUD complet avec rôles ; suspendre un prestataire le retire des matchings futurs sans supprimer l'historique.
- P3-AC5 : matching sur 500 prestataires en < 2 s.

**Hors scope** : envoi d'email, portail prestataire, scoring appris.

---

### PHASE 4 — Outreach + Portail prestataire v1

**Objectif** : contacter les prestataires approuvés, relancer, collecter leur intérêt, et leur offrir un portail minimal pour répondre et déposer des documents.

**Livrables** : `app/agents/outreach_agent.py` · `app/services/email/` (interface `EmailSender`, implémentations `ConsoleSender` (dev), `SendGridSender`) · tables §3.4 (outreach) · portail prestataire v1 (`frontend/portal/`) · file d'approbation (§4.3) · job de relance.

**Spécifications détaillées**
1. **Génération** : l'agent produit un email **personnalisé** (nom, métier, résumé de l'opportunité, scope simplifié, deadline de réponse souhaitée, lien portail) en anglais, ton professionnel ; **ne divulgue jamais** le prix cible, la marge ni les autres prestataires. Brouillon créé en `pending_approval`.
2. **Envoi** : à l'approbation (ou si `AUTO_APPROVE_OUTREACH=true`), via `EmailSender`. Idempotence : un seul email initial par `(notice_id, contractor_id)`. Limitation de débit (`OUTREACH_MAX_PER_HOUR`). Capturer `provider_message_id`.
3. **Relance J+2** : job quotidien ; si `status=sent` sans réponse après 2 jours ouvrés (calendrier US), créer une relance (max `OUTREACH_MAX_FOLLOWUPS`, défaut 2) ; arrêt automatique dès réponse. Passage à `no_response` après la dernière relance + 2 jours.
4. **Collecte des réponses** : (a) **liens magiques** signés dans l'email (« Intéressé » / « Décliner ») → endpoint public à jeton à usage unique, expirant ; (b) page portail avec formulaire (disponibilité, devis, commentaires) ; (c) *optionnel* webhook d'email entrant. Chaque réponse met à jour `outreach_thread.status` → `interested | declined`.
5. **Prestataire inconnu / nouveau** : « routing vers qualification » = création `contractor.status='invited'` + checklist de documents à fournir (COI, licences, références) ; il n'est matchable qu'une fois `active` après validation admin.
6. **Portail v1** (§9.2 spec, périmètre minimal) : invitation par email → création de compte (rôle `subcontractor`, lié à un `contractor_id`) ; catalogue filtré par ses `trades`/`naics`/états ; bouton « Déclarer mon intérêt » ; tableau de suivi (colonnes de la spec) ; upload de documents avec date d'expiration ; chat par dossier (table `message`, polling 15 s suffit) ; profil éditable. **Isolation stricte** : un prestataire ne voit jamais les autres prestataires, ni prix, ni marges, ni notes internes.
7. Notifications email sur changement de statut/nouveau message (via `EmailSender`, gabarits Jinja2).

**AC**
- P4-AC1 : l'agent génère un brouillon qui contient le titre, la deadline, la zone et un lien portail, et **ne contient aucun** champ de prix/marge (test d'absence sur 5 cas).
- P4-AC2 : aucun email n'est envoyé avant approbation quand `AUTO_APPROVE_OUTREACH=false` (compteur du `ConsoleSender`).
- P4-AC3 : pas de double envoi initial pour le même couple ; relance créée à J+2 ouvré (horloge simulée), stoppée si réponse reçue.
- P4-AC4 : un lien « Intéressé » valide met `interested` ; réutilisé ou expiré → 410, sans changement.
- P4-AC5 : tests d'autorisation : un utilisateur `subcontractor` reçoit 403/404 sur toute ressource d'un autre prestataire et sur `/api/pricing`, `/api/proposals`, `/api/audit`.
- P4-AC6 : upload COI avec `expires_on` ; une alerte est produite 30 jours avant expiration.
- P4-AC7 : un nouveau prestataire invité arrive en `invited` et n'apparaît pas dans un matching tant qu'il n'est pas `active`.

**Hors scope** : paiement, signature électronique, visioconférence, application mobile.

---

### PHASE 5 — Pricing Agent + Proposal Agent

**Objectif** : produire 3 scénarios de prix défendables et un premier draft de proposition conforme, relus et validés par l'humain.

**Livrables** : `app/agents/pricing_agent.py` (moteur de calcul **déterministe**) · `app/agents/proposal_agent.py` · `config/templates/` · exports Excel/JSON/DOCX/PDF · écrans « Pricing » et « Proposition ».

**Spécifications — Pricing**
1. **Les chiffres sont calculés en code (`Decimal`), jamais par le LLM.** Le LLM peut seulement proposer des hypothèses textuelles (ex. quantités à vérifier) qui sont **affichées pour validation**, pas injectées dans le calcul.
2. Entrées : devis/taux des prestataires `interested` (saisis ou uploadés via portail) ; matériaux (saisie manuelle ou benchmarks configurés) ; paramètres `profile.pricing` (overhead %, management fee %, contingency % de base, assurance %, bonding % de la valeur).
3. Formule (documentée dans le code et `docs/`) :
   `coût_direct = main_d'œuvre + matériaux` ; `assurance_bonding = % × valeur_contrat` (itération à point fixe, max 10 passes, tolérance 0,01 $) ; `overhead = % × coût_direct` ; `contingency = (base % + majoration risques RFP) × coût_direct` (majoration = barème configurable par type de risque détecté) ; `management_fee = % × (coût_direct + overhead)` ; `coût_total = Σ` ; `prix = coût_total / (1 − marge_cible)`.
4. Scénarios : `conservative` (marge haute, contingency haute), `median`, `aggressive` (marge basse, contingency réduite) ; paramètres de chaque scénario dans le profil. Toujours afficher marge €/% et **alerte** si le scénario agressif passe sous `profile.pricing.min_margin`.
5. Sorties : tableau récapitulatif, prix unitaires par CLIN si présents, export **Excel** (`openpyxl`, formules vivantes, pas des valeurs figées) + JSON. Verrouillage `validated` : un scénario validé devient immuable (nouvelle version pour modifier).

**Spécifications — Proposal**
1. Sections (clés §3.4) générées **à partir de données validées uniquement** : `rfp_analysis` validée, `company_profile`, `past_performance` (références `verified=true`), scénario de prix validé, prestataires confirmés. Si une donnée manque, la section contient un marqueur visible `[À COMPLÉTER : …]` ; **jamais de fait inventé** (références, certifications, chiffres).
2. Alignement RFP : la section « Technical Approach » doit adresser chaque `evaluation_criteria` et chaque `deliverable` ; produire une **matrice de traçabilité** `exigence → section/paragraphe` (utilisée par la Phase 6).
3. Templates Markdown/DOCX dans `config/templates/` ; variables Jinja2 ; styles imposés par `submission_instructions` (polices, marges, nb de pages) quand extraits.
4. **Formulaires requis** (SF-1449, SF-33…) : pour chaque formulaire de `forms_required`, produire une feuille `champ → valeur → source` pré-remplie depuis le profil entreprise ; remplir automatiquement le PDF **uniquement s'il est fourni** avec des champs AcroForm. Les champs de signature restent **toujours vides** (signature humaine).
5. Édition humaine section par section, versionnée (`proposal.version`), `edited_by_human`, `validated` ; export DOCX + PDF.

**AC**
- P5-AC1 : tests de propriété sur le moteur de prix : `prix ≥ coût_total`, marge effective = marge cible (± 0,01 %), monotonie conservative ≥ median ≥ aggressive.
- P5-AC2 : aucun montant n'est calculé hors du moteur (test qui scanne la sortie LLM mockée contenant un montant → rejeté ou ignoré).
- P5-AC3 : l'export Excel s'ouvre, contient des formules (cellules `=`), et les totaux recalculés = JSON.
- P5-AC4 : proposition générée sans données de past performance ⇒ marqueurs `[À COMPLÉTER]` présents, aucune référence inventée.
- P5-AC5 : la matrice de traçabilité couvre 100 % des `mandatory_requirements` ou liste explicitement les non couvertes.
- P5-AC6 : les champs de signature des formulaires sont vides ; un formulaire sans PDF source produit uniquement la feuille de valeurs.
- P5-AC7 : modifier une section crée une nouvelle `version` et conserve l'ancienne.

**Hors scope** : négociation de prix automatique, génération d'images/plans.

---

### PHASE 6 — Compliance Agent + paquet de soumission

**Objectif** : empêcher toute soumission non conforme et préparer le paquet final.

**Livrables** : `app/agents/compliance_agent.py` (**règles déterministes** + LLM uniquement pour comparer des exigences textuelles) · `compliance_report` · génération du **paquet de soumission** (ZIP) · écran « Conformité / Soumission ».

**Spécifications détaillées**
1. **Catalogue de contrôles** (chaque contrôle a un `code` stable, p. ex. `DOC-FORMS-PRESENT`, `CERT-SAM-ACTIVE`, `INS-COI-VALID`, `BOND-CAPACITY`, `TIME-DEADLINE`, `FMT-PAGES`, `REQ-TRACE-COVERAGE`) :
   - **Documents** : chaque formulaire de `forms_required` présent ; signé (champ de signature renseigné ou pièce signée uploadée) ;
   - **Certifications** : enregistrement SAM.gov de l'entreprise active (date d'expiration saisie dans le profil) ; certifications exigées valides ;
   - **Assurances** : COI couvrant les montants exigés, non expirée à la date de soumission **et** pendant la période d'exécution ;
   - **Bonding** : capacité ≥ montant exigé / valeur de l'offre ;
   - **Délais** : `now + marge_de_sécurité (défaut 2 h)` < `proposal_due` ; avertissement < 24 h ;
   - **Format** : nombre de pages, taille de fichier, type de fichier, polices selon `submission_instructions` ;
   - **Couverture** : tous les `mandatory_requirements` couverts (matrice Phase 5).
2. Chaque contrôle renvoie `pass | fail | warn | na` + `detail` + `corrective_action` (phrase d'action). `all_green` ⇔ aucun `fail` ; les `warn` exigent un accusé humain explicite.
3. **Soumission — réalité à respecter (décision D5)** : SAM.gov ne reçoit en général **pas** les offres ; la méthode (email au contracting officer, portail de l'agence, etc.) vient de `submission_instructions`. V1 = (a) assembler le **paquet** (PDFs/DOCX + prix + formulaires) nommé selon les instructions ; (b) afficher la méthode et les instructions ; (c) si la méthode est l'email, créer une `approval_request` `submission` d'envoi depuis la boîte configurée ; sinon (d) bouton « Marquer comme soumis » exigeant l'upload d'une **preuve** (accusé/capture). `status → submitted` uniquement après (c) ou (d), contrôle `all_green` et approbation `business_developer`/`admin`.
4. Un re-contrôle est automatique à chaque modification de proposition, prix ou document ; le dernier rapport vert périme si un élément change.

**AC**
- P6-AC1 : chaque contrôle du catalogue a un test « passe » et un test « échoue » avec `corrective_action` non vide.
- P6-AC2 : tentative de passer `submitted` avec un rapport non vert → 409 ; avec un rapport vert mais sans approbation → 409.
- P6-AC3 : modifier une section après un rapport vert invalide ce rapport (`stale`) et rebloque la soumission.
- P6-AC4 : le paquet ZIP contient exactement les pièces attendues, nommées selon `submission_instructions`, avec un manifeste `MANIFEST.json` (hash de chaque fichier).
- P6-AC5 : COI valide à la soumission mais expirant avant la fin d'exécution ⇒ `warn` (ou `fail` si l'exigence l'impose).

**Hors scope** : soumission automatisée par robot sur des portails tiers, signature électronique.

---

### PHASE 7 — Operations Agent (post-award), QC, facturation

**Objectif** : exécuter le contrat gagné, contrôler la qualité, facturer.

**Livrables** : modèles §3.5 · `app/agents/operations_agent.py` · écrans « Contrats actifs / Projet » · génération de factures PDF · interface `AccountingConnector` (stub + QuickBooks optionnel).

**Spécifications détaillées**
1. **Déclencheur award** : V1 manuel — bouton « Marquer comme attribué » (date, n° de contrat, valeur) ; la détection automatique des *Award Notices* est **hors scope** (le filtre d'ingestion les exclut). `status → awarded`, création du `project`.
2. **Dossier projet** : affectation des prestataires confirmés (ceux dont l'outreach est `interested` et le devis retenu) ; **planning** = jalons issus de `rfp_analysis.deliverables`/`deadlines`/`period_of_performance` (dates calculées depuis la date d'award, modifiables).
3. **Work orders** : un par prestataire/lot, avec scope, délai, documents requis ; visibles dans le portail (périmètre de ce prestataire seulement) ; avancement % saisi par le prestataire, validé par Ops.
4. **QC** : checklist par livrable dérivée du contrat (LLM propose, humain valide) ; upload de preuves (photos/rapports, stockage S3-compatible) ; statut `passed|failed` ; **aucune facture ne peut être générée pour un livrable dont le QC n'est pas `passed`**.
5. **Facturation** : facture générée à la validation QC (montant = prix contractuel du livrable ; jamais calculé par le LLM), traçabilité `invoice ↔ deliverable ↔ work_order` ; PDF numéroté séquentiellement sans trou ; envoi via `approval_request` ; export vers `AccountingConnector` (stub qui écrit un JSON ; QuickBooks si identifiants).
6. Tableau de bord : % d'avancement global/par work order, retards, factures en attente.

**AC**
- P7-AC1 : marquer un award crée le projet, le planning et les work orders attendus sur un jeu de test.
- P7-AC2 : génération de facture refusée si QC non validé (409) ; autorisée sinon ; numérotation sans doublon ni trou, sous concurrence (test avec 2 requêtes simultanées).
- P7-AC3 : le prestataire ne voit que ses work orders ; 403/404 sur tout le reste.
- P7-AC4 : chaque facture référence un livrable et un work order ; la suppression d'un livrable facturé est refusée.
- P7-AC5 : export comptable idempotent (même facture exportée deux fois = un seul enregistrement externe).

**Hors scope** : paie, ERP, gestion de stock, rapprochement bancaire.

---

### PHASE 8 — Production

**Objectif** : tenir les exigences non fonctionnelles (spec §5).

**Livrables et spécifications**
- **PostgreSQL** : même schéma ; migration `Alembic` ; script de bascule SQLite → Postgres ; index sur `naics_code`, `response_deadline_utc`, `status`, `(notice_id, created_at)` des tables d'historique ; test de charge ≥ 10 000 opportunités actives (liste paginée < 300 ms p95).
- **Stockage S3-compatible** (AWS S3 / Cloudflare R2) derrière `storage/` : URLs signées, chiffrement côté serveur, types MIME contrôlés, antivirus optionnel.
- **Auth durcie** : mots de passe Argon2, JWT court + refresh, verrouillage après N échecs, 2FA optionnel (admin), invitation prestataire à jeton, RBAC testé sur **chaque** route (test qui parcourt `app.routes` et vérifie qu'aucune route n'est publique sans être listée en liste blanche).
- **Sécurité** : TLS de bout en bout, chiffrement at-rest (volume/colonnes sensibles), en-têtes de sécurité, CORS restreint, limitation de débit, validation des uploads, secrets via gestionnaire de secrets.
- **Fiabilité** : Docker + `docker-compose` (app, worker, postgres), sondes `/healthz` `/readyz`, sauvegardes quotidiennes + test de restauration documenté, scheduler en processus séparé avec verrou distribué ; (option) Celery+Redis si le volume l'exige.
- **Observabilité** : logs JSON, métriques (durée/échecs par agent, tokens, coût estimé, âge de la dernière sync), alertes (sync > 12 h, agent en échec, deadline < 72 h sans action).
- **Conformité SAM.gov** : respect du quota/limites de débit, `User-Agent` identifié, pas de scraping du site ; documenter la politique dans `docs/`.
- **Disponibilité 99,5 %** : procédure de déploiement sans interruption, plan de maintenance documenté.

**AC**
- P8-AC1 : `docker compose up` démarre la stack complète ; `/readyz` vert.
- P8-AC2 : test de charge 10 000 offres conforme au p95 ci-dessus.
- P8-AC3 : restauration d'une sauvegarde sur environnement vierge validée par script.
- P8-AC4 : audit RBAC automatisé vert ; aucun secret dans l'image (scan).
- P8-AC5 : toutes les actions agents/humains présentes dans `audit_log` (test de couverture des services).

---

## 6. Contrat commun des agents et couche LLM

### 6.1 Interface d'un agent

```python
class Agent(Protocol[InT, OutT]):
    name: str  # ex. "opportunity_analyst"
    prompt_version: str  # ex. "screening.v1"

    def run(self, payload: InT, *, force: bool = False) -> OutT: ...
```
Obligations pour **chaque** agent :
1. Entrée et sortie = modèles Pydantic ; la sortie est persistée dans `agent_run.output` et dans la table métier.
2. `input_hash = sha256(json canonique de l'entrée + prompt_version + model)` → idempotence (§2.3).
3. Écrit `agent_run` (durée, tokens, statut) et `audit_log` (`actor_type="agent"`).
4. **Ne modifie jamais directement `opportunity_state.status`** : passe par `OpportunityStateService`.
5. Ne réalise jamais d'effet externe (email, soumission, facture) : crée une `approval_request`.
6. Testable hors ligne : le `LLMClient` est injecté (fixture `fake_llm` qui rejoue des réponses enregistrées).

### 6.2 `LLMClient` et sortie structurée

- Interface `LLMClient.complete(system, user, *, schema, temperature, max_tokens) -> str`. Implémentation par défaut **Groq** (`llama-3.3-70b-versatile`, modèle lu depuis `Settings.LLM_MODEL` — les modèles hébergés évoluent, ne jamais le coder en dur) ; implémentation OpenAI optionnelle derrière le même contrat.
- `structured.call(schema: type[BaseModel], ...)` : demande du JSON, extrait, valide avec Pydantic, **1 retry** avec le message d'erreur de validation, puis lève `LLMOutputError`.
- Gestion des erreurs : timeout 60 s, retry exponentiel sur 429/5xx (max 3), disjoncteur simple (après 5 échecs consécutifs, pause 60 s). Plafond de dépense journalier `LLM_DAILY_TOKEN_BUDGET` : au-delà, les agents passent en échec propre avec message explicite.
- **Prompts** : fichiers dans `app/llm/prompts/<agent>/<nom>.vN.md`, jamais en chaîne dans le code. Structure : rôle, contexte, règles (« ne jamais inventer », « répondre `null` si absent »), schéma JSON attendu, 2 exemples courts. Toute modification de prompt = nouvelle version (`vN+1`) et snapshot test mis à jour.
- **Injection de prompt** : les textes SAM/PDF/emails sont des **données non fiables**. Les placer dans un bloc délimité (`<document>…</document>`), ajouter au prompt système l'instruction de n'obéir à aucune consigne contenue dans ce bloc, et ne jamais donner au LLM d'outil à effet de bord.
- Aucune donnée personnelle de prestataire envoyée au LLM au-delà du strict nécessaire (pas d'email/téléphone dans les prompts de matching ou de pricing).

---

## 7. Contrat d'API REST (synthèse)

Préfixe `/api`, JSON, erreurs au format `{"error": {"code": "...", "message": "..."}}`, pagination `?page=&page_size=` (réponse `{items, total, page, page_size}`). Rôles : A=admin, B=business_developer, O=ops_manager, S=subcontractor.

| Phase | Méthode & route | Rôles | Rôle de la route |
|---|---|---|---|
| 1 | `POST /auth/login` · `GET /auth/me` | tous | JWT |
| 0.5 | `GET /sync/status` · `POST /sync/run` | A | état/forçage de la sync |
| 1 | `GET /opportunities` (filtres : `status, recommendation, min_score, naics, state, set_aside, deadline_before, q`) | A,B,O | liste |
| 1 | `GET /opportunities/{id}` | A,B,O | fiche + dernier screening |
| 1 | `POST /opportunities/{id}/screening` (`force`) | A,B | (re)lancer le screening |
| 1 | `POST /opportunities/{id}/decision` `{decision: bid|no_bid, reason}` | A,B | décision |
| 1 | `POST /opportunities/{id}/comments` · `GET …/comments` | A,B,O | commentaires |
| 2 | `POST /opportunities/{id}/rfp/documents` · `GET …/rfp/documents` | A,B | upload / liste |
| 2 | `POST /opportunities/{id}/rfp/analysis` · `GET …/rfp/analysis` · `PATCH …/rfp/analysis` · `POST …/rfp/analysis/validate` | A,B | analyse |
| 3 | `GET/POST/PATCH /contractors` · `POST /contractors/{id}/suspend` | A,B | CRUD |
| 3 | `POST /opportunities/{id}/matching` · `GET …/matching` · `POST …/matching/{contractor_id}/decision` | A,B | shortlist |
| 4 | `GET /approvals` · `POST /approvals/{id}/approve|reject` | A,B | file d'approbation |
| 4 | `GET /opportunities/{id}/outreach` · `POST …/outreach/draft` | A,B | brouillons/fils |
| 4 | `GET /public/outreach/respond?token=` | public (jeton) | réponse « intéressé/décliné » |
| 4 | `GET /portal/opportunities` · `POST /portal/opportunities/{id}/interest` · `GET /portal/applications` · `POST /portal/documents` · `GET/POST /portal/threads/{id}/messages` · `GET/PATCH /portal/profile` | S | portail |
| 5 | `POST /opportunities/{id}/pricing` · `GET …/pricing` · `POST …/pricing/{sid}/validate` · `GET …/pricing/{sid}/export?format=xlsx|json` | A,B | prix |
| 5 | `POST /opportunities/{id}/proposal` · `GET/PATCH …/proposal/sections/{key}` · `POST …/proposal/validate` · `GET …/proposal/export?format=docx|pdf` | A,B | proposition |
| 6 | `POST /opportunities/{id}/compliance` · `GET …/compliance` · `POST …/submission/package` · `POST …/submission/confirm` | A,B | conformité/soumission |
| 7 | `POST /opportunities/{id}/award` · `GET /projects` · `GET /projects/{id}` · `POST /work-orders` · `POST /qc/{id}/validate` · `POST /invoices` · `POST /invoices/{id}/send` | A,O (S : lecture/maj de ses work orders) | post-award |
| 1+ | `GET /audit?entity_type=&entity_id=` | A | journal d'audit |

Règle : **toute route est déclarée avec sa dépendance de rôle** ; une route sans dépendance de rôle est un échec de revue (test automatique en Phase 8, vérifié dès la Phase 1).

---

## 8. Rôles et sécurité (rappel opérationnel)

| Rôle | Peut | Ne peut pas |
|---|---|---|
| `admin` | tout, config, utilisateurs, audit | — |
| `business_developer` | décisions bid/no-bid, validation RFP/matching/pricing/proposition, approbation soumission | gérer les utilisateurs |
| `ops_manager` | post-award, QC, facturation | décisions bid/no-bid, soumission |
| `subcontractor` | son profil, ses candidatures, ses work orders, ses messages | tout le reste (autres prestataires, prix, marges, audit) |

Règles transverses : mot de passe jamais en clair ni dans les logs ; réponses d'erreur sans fuite d'information (404 plutôt que 403 sur ressource d'autrui pour les prestataires) ; validation stricte des uploads (extension + magic bytes + taille) ; aucun secret ni donnée personnelle dans les prompts/logs ; export de données d'un prestataire sur demande (suppression logique possible, `audit_log` conservé).

---

## 9. Qualité, tests, observabilité

- **Pyramide de tests** : unitaires (logique pure : parsing, scoring, pricing, compliance) > intégration (API + DB SQLite en mémoire) > `live` (LLM/SAM réels, marqués `@pytest.mark.live`, hors CI).
- **Fixtures** : `tests/fixtures/sam_sample.json` = ≥ 50 offres réelles **sans données personnelles** (e-mails et noms des `pointOfContact` anonymisés), couvrant : 3 formats de deadline, set-aside variés, lieu absent, `additionalInfoLink="None"`, `resourceLinks` présents/absents.
- **Golden tests** : prompts rendus (snapshot) et schémas JSON ; un changement non intentionnel casse le test.
- **Couverture** : ≥ 85 % sur `app/services`, `app/agents`, `app/sam`, `app/llm` ; pas d'objectif sur le frontend (tests manuels documentés).
- **Qualité LLM** : jeu d'évaluation `tests/eval/screening_gold.jsonl` (≥ 30 offres étiquetées RECOMMENDED/REVIEW/PASS par l'utilisateur) + script `scripts/eval_screening.py` qui rapporte accuracy et rappel sur RECOMMENDED. Objectif Phase 1 : **rappel RECOMMENDED ≥ 90 %** (ne pas manquer de bonnes offres) ; à utiliser pour ajuster poids et prompts.
- **Observabilité minimale dès la Phase 0.5** : `request_id` dans chaque log, endpoint `/healthz`, métrique « âge de la dernière sync ».
- **Revue de PR** : checklist `.github/pull_request_template.md` reprenant la définition de « terminé » (§0).

---

## 10. Suivi d'avancement (l'agent coche au fil de l'eau)

- [x] Phase 0.5 — Stabilisation et fondations
- [ ] Phase 1 — Dashboard + Screening + Bid/No-Bid
- [ ] Phase 2 — RFP Analyst
- [ ] Phase 3 — Prestataires + Matchmaker
- [ ] Phase 4 — Outreach + Portail v1
- [ ] Phase 5 — Pricing + Proposal
- [ ] Phase 6 — Compliance + Soumission
- [ ] Phase 7 — Operations + QC + Invoicing
- [ ] Phase 8 — Production

---

## 11. Décisions par défaut et points à valider par le propriétaire

L'agent applique la **valeur par défaut** ci-dessous et le signale dans `docs/DECISIONS.md` ; le propriétaire peut les renverser.

| ID | Sujet | Défaut retenu | Pourquoi / alternative |
|---|---|---|---|
| D1 | Nom/structure du projet | Repo `SAM_Contractors`, package `app/`, produit « GovContractAI » | La spec mélange 3 noms (`SAM_Contract/`, `sam-contract`, `GovContractAI`). Aligner si besoin. |
| D2 | **Persistance** | **SQLite + SQLAlchemy dès la Phase 0.5** (Postgres en Phase 8), import du JSON existant | La spec prévoit JSON puis Postgres. Le JSON est réécrit en entier à chaque sync, sans verrou, alors que l'API et le scheduler écriront en parallèle dès la Phase 1 (corruption/écrasement, cf. B3). *Écart par rapport à la spec — à confirmer.* Alternative : rester JSON jusqu'à la Phase 3 (risqué). |
| D3 | LLM | Groq `llama-3.3-70b-versatile` via config ; interface pour OpenAI | Modèle configurable car les offres de modèles évoluent. |
| D4 | Authentification | JWT + rôles introduits **dès la Phase 1** (la spec prévoit Phase 8) | Nécessaire pour l'audit (acteur), les décisions par rôle et le portail (Phase 4). Phase 8 = durcissement seulement. |
| D5 | Soumission | Paquet + instructions + preuve de dépôt ; envoi email possible ; **pas** de dépôt automatisé sur portails | SAM.gov ne reçoit en général pas les offres ; chaque agence a sa méthode. *À confirmer avec le métier.* |
| D6 | Détection de l'award | Manuelle en V1 | Les *Award Notices* sont exclues du filtre d'ingestion. |
| D7 | Quota SAM.gov | Screening en 2 étages, `stage2_daily_cap=100` | **À vérifier** : quota réel de la clé API et paramètre exact `is_active` (doc officielle). Ajuster le cap en conséquence. |
| D8 | Langue de l'interface | Interface et justifications du screening en **français** ; contenus destinés à l'extérieur (emails, proposition) en **anglais** | Marché américain. À confirmer. |
| D9 | Fuseau des deadlines | Date seule ⇒ 23:59:59 America/New_York | Hypothèse prudente ; à raffiner si l'avis précise un fuseau. |
| D10 | Prestataires multi-tenant | Mono-entreprise (un seul profil `company_profile.yaml`) | La spec ne couvre pas le multi-clients ; ne pas prévoir de `tenant_id` en V1. |

### Informations que le propriétaire doit fournir (bloquantes pour la qualité)

1. **Profil entreprise réel** (`config/company_profile.yaml`) : NAICS visés, PSC, états d'intervention, certifications (8(a), HUBZone, SDVOSB, WOSB, small business), limites de contrat min/max, bonding max, taux overhead/fee/contingency/marge cible, date d'expiration de l'enregistrement SAM.
2. **30 offres étiquetées** (RECOMMENDED/REVIEW/PASS) pour calibrer et évaluer le screening.
3. **3 RFP complets** (PDF) représentatifs pour tester la Phase 2.
4. **Templates de proposition** approuvés (Markdown/DOCX) et références de past performance vérifiées.
5. Fournisseur d'email choisi (SendGrid/Mailgun) et domaine d'expédition (SPF/DKIM).

---

## 12. Annexes

### 12.1 Schémas Pydantic de référence (simplifiés — à implémenter dans `app/schemas/`)

```python
class EvaluationCriterion(BaseModel):
    criterion: str
    weight: str | None  # ex. "50%" ou "Most important"
    source: SourceRef | None


class SourceRef(BaseModel):
    doc: str  # nom de fichier ou "description"
    page: int | None
    quote: str = Field(max_length=200)  # doit exister verbatim dans le texte source


class Deadlines(BaseModel):
    questions_cutoff: date | None
    proposal_due: datetime | None  # tz-aware
    site_visit: datetime | None


class SubmissionInstructions(BaseModel):
    method: Literal["email", "portal", "mail", "hand_delivery", "unknown"]
    destination: str | None
    max_pages: int | None
    file_format: list[str] = []
    notes: str | None


class RfpExtraction(BaseModel):
    scope: str
    deliverables: list[Item]  # Item = {text, source: SourceRef | None, verified: bool}
    mandatory_requirements: list[Item]
    evaluation_criteria: list[EvaluationCriterion]
    forms_required: list[str]
    deadlines: Deadlines
    risks: list[str]
    contract_type: str | None  # FFP, T&M, IDIQ…
    period_of_performance: str | None
    clins: list[dict] = []
    place_of_performance_detail: str | None
    submission_instructions: SubmissionInstructions
    open_questions: list[str] = []
    amendments: list[str] = []
    confidence: dict[str, float]  # par champ, 0–1


class ScreeningLLMOutput(BaseModel):  # sortie du LLM — étage 2
    required_licenses: list[str] | None
    required_insurance: list[str] | None
    bonding_required_usd: float | None  # null si non mentionné
    subcontracting_constraints: list[str] | None
    estimated_value_usd: float | None
    scope_complexity: Literal["low", "medium", "high"] | None
    risks: list[str]
    positive_factors: list[Factor]  # Factor = {text_fr, evidence ≤200 car.}
    blocking_factors: list[Factor]
    capability_keywords_matched: list[str]
```

### 12.2 `config/company_profile.yaml` (exemple de structure)

```yaml
company:
  legal_name: "À COMPLÉTER"
  sam_registration_expires: "2027-03-31"
  small_business: true
  certifications: []            # ex: ["8a", "hubzone", "sdvosb", "wosb"]
targeting:
  naics_primary: ["238220"]
  naics_secondary: ["238210"]
  psc_codes: []
  service_states: ["VA", "MD", "DC"]
  min_days_to_respond: 5
  ideal_days: 21
  contract_value_min_usd: 25000
  contract_value_max_usd: 5000000
  max_bonding_usd: 1000000
screening:
  stage1_threshold: 40
  stage2_daily_cap: 100
  thresholds: { recommended: 70, review: 40 }
  weights: { capacity: 0.30, geography: 0.15, size: 0.10, timeline: 0.10, set_aside: 0.10,
             licenses: 0.10, bonding: 0.05, subcontracting: 0.05, risk: 0.05 }
pricing:
  overhead_pct: 0.12
  management_fee_pct: 0.05
  contingency_base_pct: 0.05
  insurance_pct: 0.01
  bonding_pct: 0.015
  min_margin_pct: 0.08
  scenarios:
    conservative: { margin: 0.20, contingency_mult: 1.5 }
    median:       { margin: 0.14, contingency_mult: 1.0 }
    aggressive:   { margin: 0.09, contingency_mult: 0.6 }
  risk_surcharges: { uncapped_penalties: 0.02, unclear_scope: 0.015 }
```

### 12.3 Normalisation des set-aside (table de départ, à compléter depuis la doc SAM.gov)

| Code SAM | Catégorie interne | Éligibilité requise |
|---|---|---|
| `SBA`, `SBP` | `small_business` | `company.small_business` |
| `8A`, `8AN` | `eight_a` | certification `8a` |
| `HZC`, `HZS` | `hubzone` | certification `hubzone` |
| `SDVOSBC`, `SDVOSBS` | `sdvosb` | certification `sdvosb` |
| `WOSB`, `EDWOSB`, `WOSBSS`, `EDWOSBSS` | `wosb` | certification `wosb` |
| `NONE`, `''`, `null` | `none` | aucune |
| autre | `other` | **non éligible par défaut + avertissement** (ne jamais ignorer silencieusement un code inconnu : le loguer) |

### 12.4 Notes API SAM.gov (à vérifier dans la documentation officielle avant de coder)

- Endpoint : `https://api.sam.gov/opportunities/v2/search` ; `postedFrom`/`postedTo` au format `MM/dd/yyyy` (plage maximale d'un an) ; `limit` ≤ 1 000 ; `ptype` : `o` (Solicitation), `k` (Combined Synopsis/Solicitation).
- Vérifier : nom exact des paramètres de filtre d'activité, quota quotidien de la clé, nécessité d'ajouter `api_key` aux URLs de description et de ressources, comportement des avis modifiés (nouveau `noticeId` vs mise à jour).
- Ne jamais enfreindre les limites de débit ; respecter les CGU (pas de scraping du site web).

---

*Fin du document. En cas de doute entre ce document et le cahier des charges : le cahier des charges définit le **besoin**, ce document définit la **réalisation** ; tout écart est consigné dans `docs/DECISIONS.md`.*
