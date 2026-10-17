# feat: add private enterprise workspaces and opportunity screening

Le produit accompagne plusieurs entreprises candidates sous la supervision de la plateforme.
Ce lot ajoute leurs espaces privés, l'authentification JWT/Argon2, les profils de qualification,
le dashboard filtrable et les décisions/commentaires audités. Le catalogue SAM est partagé ;
les dossiers, scores et décisions sont isolés par entreprise, avec tests de refus inter-entreprises.

Le screening combine triage déterministe et analyse Groq structurée, avec cache, historique,
réparation JSON, preuves vérifiées, quotas et réservations de budget. Un échec IA reste visible.
Le client SAM corrige aussi l'offset selon la source officielle GSA (indice de page).

Validation locale : 88 tests Python et un parcours Chromium passent, couverture métier 95,87 %,
Ruff, format, mypy strict et Alembic check verts. La recette réelle SAM/Groq et la calibration
sur 30 avis humains restent bloquées par les accès et données manquants ; Phase 1 non close.
Les phases 2 à 8 ne sont pas incluses. Aucun envoi externe, push ou merge n'a été effectué.
