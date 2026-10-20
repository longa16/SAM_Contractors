'use strict';
const $ = (id) => document.getElementById(id);
const state = {
  token: null,
  user: null,
  company: null,
  companies: [],
  page: 1,
  total: 0,
  selected: null,
  profile: null,
  statement: null,
  applicationsPage: 1,
};
const SESSION_KEY = 'sam-session-token';
function savedToken() {
  try {
    return sessionStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}
function saveToken(token) {
  try {
    if (token) sessionStorage.setItem(SESSION_KEY, token);
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Connexion en mémoire si le navigateur interdit le stockage.
  }
}
let noticeTimer;
let syncPollTimer;
function notify(message) {
  $('notice').textContent = message;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => {
    $('notice').textContent = '';
  }, 6500);
}
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function companyQuery() {
  return state.company ? `company_id=${encodeURIComponent(state.company)}` : '';
}
async function api(path, options = {}, scoped = true, responseType = 'json') {
  const suffix = scoped && companyQuery() ? (path.includes('?') ? '&' : '?') + companyQuery() : '';
  const response = await fetch('/api' + path + suffix, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(state.token ? { Authorization: 'Bearer ' + state.token } : {}),
      ...options.headers,
    },
  });
  if (response.ok && responseType === 'blob') return response.blob();
  const payload = await response.json();
  if (!response.ok) {
    if (response.status === 401 && state.token) logout();
    throw new Error(payload.error?.message || 'La requête n’a pas abouti.');
  }
  return payload;
}
function bind(id, event, action) {
  $(id).addEventListener(event, async (e) => {
    e.preventDefault();
    const button = e.submitter || (e.currentTarget.tagName === 'BUTTON' ? e.currentTarget : null);
    if (button) button.disabled = true;
    try {
      await action(e);
    } catch (err) {
      if (button?.id === 'sync-button') button.disabled = false;
      notify(err.message);
    } finally {
      if (button && button.id !== 'sync-button') button.disabled = false;
    }
  });
}
function logout() {
  saveToken(null);
  clearTimeout(syncPollTimer);
  state.token = null;
  state.user = null;
  state.company = null;
  state.selected = null;
  state.companies = [];
  state.profile = null;
  state.page = 1;
  state.total = 0;
  $('detail-content').replaceChildren();
  $('metrics').replaceChildren();
  $('company-select').replaceChildren();
  $('identity').textContent = '';
  $('sync-state').textContent = '';
  $('workspace').hidden = true;
  $('login-view').hidden = false;
  $('detail-dialog').close();
  $('cards').replaceChildren();
  $('comments').replaceChildren();
  $('profile-form').reset();
  $('user-form').reset();
  $('statement-form').reset();
  $('statement-preview').replaceChildren();
  $('company-portfolio').replaceChildren();
  $('admin-metrics').replaceChildren();
  $('workspace').className = '';
  $('application-list').replaceChildren();
  $('statement-pdf-form').reset();
  $('statement-pdf-actions').hidden = true;
  state.applicationsPage = 1;
  state.statement = null;
  $('contractor-review').replaceChildren();
  $('contractor-documents').replaceChildren();
}
function switchView(view) {
  if (['admin', 'contractors'].includes(view) && state.user?.role !== 'admin') return;
  if (['profile', 'statement'].includes(view) && state.user?.role === 'admin') return;
  if (['profile', 'statement'].includes(view) && !state.company) return;
  if (view === 'applications' && !state.company && state.user?.role !== 'admin') return;
  for (const name of [
    'opportunities',
    'profile',
    'statement',
    'applications',
    'admin',
    'contractors',
  ])
    $(name + '-view').hidden = name !== view;
  document
    .querySelectorAll('[data-view]')
    .forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $('page-title').textContent = {
    opportunities: state.company ? 'Vos opportunités' : 'Catalogue SAM',
    profile: 'Profil entreprise',
    admin: 'Supervision de la plateforme',
    statement: 'Company Statement',
    applications:
      state.user.role === 'admin' ? 'Traitement des candidatures' : 'Suivi des candidatures',
    contractors: 'Dossiers entreprises',
  }[view];
  if (view === 'contractors') loadContractorReview().catch((e) => notify(e.message));
  if (view === 'applications') loadApplications().catch((e) => notify(e.message));
  if (view === 'statement') loadStatement().catch((e) => notify(e.message));
  if (view === 'profile') loadProfile().catch((e) => notify(e.message));
}
async function loadCompanies(preferred) {
  state.companies = await api('/companies', {}, false);
  $('company-select').replaceChildren(
    ...(state.user.role === 'admin'
      ? [Object.assign(node('option', 'Catalogue SAM — toutes les offres'), { value: '' })]
      : []),
    ...state.companies.map((c) => {
      const option = node('option', c.name);
      option.value = c.id;
      return option;
    }),
  );
  state.company = preferred ?? state.company ?? state.user.company_id ?? null;
  $('company-select').value = state.company ? String(state.company) : '';
  updateScope();
  renderPortfolio();
}
function updateScope() {
  const global = !state.company;
  for (const id of ['recommendation', 'status', 'min-score']) {
    $(id).disabled = global;
    if (global) $(id).value = '';
  }
  $('sort').querySelector('option[value="score"]').disabled = global;
  if (global) $('sort').value = 'deadline';
  document.querySelector('[data-view="profile"]').disabled = global;
  $('statement-nav').disabled = global;
  $('applications-nav').disabled = global && state.user.role !== 'admin';
  $('user-form').querySelector('button[type="submit"]').disabled = global;
  if (
    global &&
    (!$('profile-view').hidden ||
      !$('statement-view').hidden ||
      (!$('applications-view').hidden && state.user.role !== 'admin'))
  )
    switchView('opportunities');
  if (!$('opportunities-view').hidden)
    $('page-title').textContent = global ? 'Catalogue SAM' : 'Vos opportunités';
}
async function refresh() {
  const params = new URLSearchParams({ page: state.page, page_size: 20, sort: $('sort').value });
  for (const [id, key] of [
    ['search', 'q'],
    ['recommendation', 'recommendation'],
    ['status', 'status'],
    ['naics', 'naics'],
    ['state', 'state'],
    ['min-score', 'min_score'],
    ['set-aside', 'set_aside'],
  ])
    if ($(id).value) params.set(key, $(id).value);
  if ($('deadline-before').value)
    params.set('deadline_before', $('deadline-before').value + 'T23:59:59Z');
  const data = await api('/opportunities?' + params);
  state.total = data.total;
  $('cards').replaceChildren(...data.items.map(card));
  if (!data.items.length)
    $('cards').append(
      node(
        'p',
        'Aucune opportunité pour ces critères. Ajustez les filtres ou actualisez le catalogue.',
        'panel',
      ),
    );
  $('result-count').textContent = data.total + ' résultat(s)';
  $('page-number').textContent = 'Page ' + state.page;
  $('previous').disabled = state.page === 1;
  $('next').disabled = state.page * 20 >= data.total;
  const sync = await api('/sync/status');
  clearTimeout(syncPollTimer);
  const initial = sync.initial_sync;
  $('sync-button').textContent =
    initial && !initial.completed
      ? initial.next_date
        ? 'Reprendre le chargement initial'
        : 'Charger les 365 derniers jours'
      : 'Récupérer les nouvelles offres et les mises à jour';
  $('sync-button').disabled = sync.last_run_status === 'running';
  if (sync.last_run_status === 'running') {
    syncPollTimer = setTimeout(() => {
      if (state.token) refresh().catch((error) => notify(error.message));
    }, 3000);
  }
  $('metrics').replaceChildren(
    ...[
      [state.user.role === 'admin' ? 'Catalogue filtré' : 'Offres disponibles', data.total],
      ...(state.company && state.user.role === 'admin'
        ? [
            ['Premier tri', sync.stages.stage1 || 0],
            ['Analyse approfondie', sync.stages.stage2 || 0],
            ['Analyses à reprendre', sync.analysis_errors],
          ]
        : []),
    ].map(([label, value]) => {
      const n = node('div', undefined, 'metric');
      n.append(node('span', label), node('strong', String(value)));
      return n;
    }),
  );
  $('sync-state').textContent = sync.last_success
    ? 'Dernière synchronisation réussie : ' + new Date(sync.last_success).toLocaleString('fr-FR')
    : 'Le catalogue historique est disponible. Aucune synchronisation API réussie enregistrée.';
  if (initial && !initial.completed) {
    $('sync-state').textContent +=
      ' Chargement initial : ' +
      initial.completed_days +
      '/365 jours traités. La progression est conservée.';
  }
  if (sync.last_run_status === 'failed') {
    const error = sync.last_run_error;
    $('sync-state').textContent +=
      ' Dernière tentative échouée : ' +
      (error?.message || 'Erreur SAM.gov.') +
      (error?.http_status ? ' (HTTP ' + error.http_status + ')' : '');
  } else if (sync.last_run_status === 'running') {
    $('sync-state').textContent += ' Synchronisation en cours.';
  }
}
function deadlineText(raw) {
  if (!raw) return 'Date limite non précisée';
  const delta = new Date(raw) - Date.now();
  const days = Math.ceil(delta / 86400000);
  return (
    new Date(raw).toLocaleDateString('fr-FR') + (days < 0 ? ' · Expirée' : ` · ${days} j restants`)
  );
}
function card(item) {
  const n = node('article', undefined, 'card');
  const top = node('div', undefined, 'card-header');
  const rec = item.screening?.recommendation;
  top.append(
    node(
      'span',
      rec
        ? { RECOMMENDED: 'RECOMMANDÉ', REVIEW: 'À ÉTUDIER', PASS: 'PASSÉ' }[rec]
        : state.company
          ? 'NON ANALYSÉ'
          : 'AVIS SAM',
      'badge ' + (rec || ''),
    ),
    node('span', item.screening ? Math.round(item.screening.score) + '/100' : '—', 'score'),
  );
  n.append(
    top,
    node(
      'span',
      {
        new: 'Nouveau',
        screening: 'À étudier',
        bid: 'Bid validé',
        'no-bid': 'No-Bid',
        expired: 'Expiré',
      }[item.status] ||
        item.status ||
        'Catalogue SAM',
      'muted',
    ),
    node('div', item.agency || 'Agence non précisée', 'agency'),
    node('h3', item.title),
  );
  const meta = node('div', undefined, 'card-meta');
  meta.append(
    node('span', 'NAICS ' + (item.naics_code || 'n/c')),
    node('span', item.place || 'Lieu non précisé'),
    node('span', item.set_aside || 'Sans restriction'),
    node(
      'span',
      item.screening?.estimated_value_usd
        ? new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'USD' }).format(
            item.screening.estimated_value_usd,
          )
        : 'Valeur : n/c',
    ),
  );
  n.append(meta);
  if (['failed', 'needs_description', 'daily_cap', 'stale_profile'].includes(item.analysis_status))
    n.append(
      node(
        'p',
        {
          failed: 'Analyse IA indisponible',
          needs_description: 'Description à récupérer',
          daily_cap: 'Analyse reportée : plafond journalier',
          stale_profile: 'Profil modifié : analyse à renouveler',
        }[item.analysis_status],
        'muted',
      ),
    );
  const foot = node('div', undefined, 'card-footer');
  foot.append(
    node(
      'span',
      deadlineText(item.deadline),
      item.deadline && new Date(item.deadline) - Date.now() < 72 * 3600000 ? 'urgent' : 'muted',
    ),
  );
  const button = node('button', 'Voir le dossier →', 'secondary');
  button.addEventListener('click', () =>
    openDetail(item.notice_id).catch((e) => notify(e.message)),
  );
  foot.append(button);
  n.append(foot);
  return n;
}
async function openDetail(id) {
  state.selected = id;
  const [item, comments] = await Promise.all([
    api('/opportunities/' + encodeURIComponent(id)),
    state.company && state.user.role === 'admin'
      ? api('/opportunities/' + encodeURIComponent(id) + '/comments')
      : Promise.resolve([]),
  ]);
  const content = $('detail-content');
  content.replaceChildren(
    node('h2', item.title),
    node('p', item.agency_path, 'muted'),
    node('p', deadlineText(item.deadline)),
    node(
      'p',
      state.company
        ? 'Statut : ' + item.status
        : 'Catalogue SAM — choisissez une entreprise pour analyser cet avis ou prendre une décision.',
    ),
  );
  const link = node('a', 'Voir l’avis sur SAM.gov');
  link.href = item.sam_url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  content.append(link);
  if (item.screening) {
    content.append(node('h3', 'Pertinence : ' + item.screening.score + '/100'));
    for (const [label, list] of [
      ['Points favorables', item.screening.positive_factors],
      ['Points de vigilance', item.screening.blocking_factors],
    ]) {
      content.append(node('h3', label));
      for (const f of list) {
        content.append(node('p', f.text_fr));
        if (f.evidence) content.append(node('blockquote', f.evidence));
      }
    }
  }
  if (item.description_text) content.append(node('p', item.description_text, 'description'));
  if (item.decision_reason)
    content.append(node('p', 'Motif de décision : ' + item.decision_reason));
  $('comments').replaceChildren(
    ...comments.map((c) =>
      node('div', `${new Date(c.created_at).toLocaleString('fr-FR')} · ${c.body}`, 'comment'),
    ),
  );
  $('company-discussion').hidden = !state.company || state.user.role !== 'admin';
  const decide = Boolean(state.company) && state.user.role === 'admin';
  $('screen').hidden = !decide;
  $('track-application').hidden = !state.company;
  $('track-application').textContent =
    state.user.role === 'admin' ? 'Ajouter au suivi' : 'Exprimer mon intérêt';
  $('bid').hidden = $('no-bid').hidden = !decide || item.status !== 'screening';
  $('reason-form').hidden = true;
  $('comment').value = '';
  if (!$('detail-dialog').open) $('detail-dialog').showModal();
}
function csv(id, upper = false) {
  return $(id)
    .value.split(',')
    .map((v) => (upper ? v.trim().toUpperCase() : v.trim()))
    .filter(Boolean);
}
async function loadProfile() {
  if (!state.company) return;
  const data = await api(`/companies/${state.company}/profile`, {}, false);
  state.profile = data.profile;
  const p = data.profile;
  const values = {
    'legal-name': p?.company.legal_name || data.name,
    'primary-naics': p?.targeting.naics_primary.join(', ') || '',
    'secondary-naics': p?.targeting.naics_secondary.join(', ') || '',
    psc: p?.targeting.psc_codes.join(', ') || '',
    'service-states': p?.targeting.service_states.join(', ') || '',
    certifications: p?.company.certifications.join(', ') || '',
    'sam-expires': p?.company.sam_registration_expires || '',
    'value-min': p?.targeting.contract_value_min_usd ?? 0,
    'value-max': p?.targeting.contract_value_max_usd ?? '',
    bonding: p?.targeting.max_bonding_usd ?? '',
    'min-days': p?.targeting.min_days_to_respond ?? 5,
    'ideal-days': p?.targeting.ideal_days ?? 21,
  };
  for (const [id, value] of Object.entries(values)) $(id).value = value;
  $('small-business').checked = p?.company.small_business || false;
  fillOnboarding(p);
  for (const input of $('profile-form').elements) input.disabled = false;
  await renderDocuments($('contractor-documents'), state.company, false);
}
bind('login-form', 'submit', async () => {
  const result = await api(
    '/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ email: $('email').value, password: $('password').value }),
    },
    false,
  );
  state.token = result.access_token;
  $('password').value = '';
  saveToken(state.token);
  await openWorkspace();
});
async function openWorkspace() {
  state.user = await api('/auth/me', {}, false);
  $('identity').textContent = state.user.email;
  const admin = state.user.role === 'admin';
  $('workspace').className = admin ? 'admin-space' : 'company-space';
  $('admin-nav').hidden = $('sync-button').hidden = $('contractors-nav').hidden = !admin;
  $('profile-nav').hidden = $('statement-nav').hidden = admin;
  $('applications-nav').textContent = admin
    ? '▤ Traitement des candidatures'
    : '▤ Suivi des candidatures';
  $('company-control').hidden = !admin;
  $('space-label').textContent = admin ? 'ADMINISTRATION' : 'ESPACE ENTREPRISE';
  $('nav-label').textContent = admin ? 'SUPERVISION' : 'VOTRE DÉVELOPPEMENT';
  $('opportunities-nav').textContent = admin ? '▦ Catalogue SAM' : '▦ Opportunités';
  $('space-note').textContent = admin
    ? 'Supervisez le catalogue et accompagnez chaque entreprise dans son espace privé.'
    : 'Complétez vos informations, exprimez vos vœux et suivez notre accompagnement.';
  $('company-welcome').hidden = admin;
  $('sync-state').hidden = !admin;
  $('all-applications').hidden = !admin;
  $('applications-intro').textContent = admin
    ? 'Traitez les vœux, examinez les offres et validez chaque étape terminée. Publiez un compte rendu pour informer l’entreprise. Les agents de préparation et l’envoi automatique ne sont pas encore intégrés.'
    : 'Retrouvez vos vœux et les étapes validées par votre accompagnateur. Il prend en charge l’examen, la préparation, l’envoi et le suivi de vos candidatures.';
  await loadCompanies();
  $('login-view').hidden = true;
  $('workspace').hidden = false;
  $('company-heading').textContent =
    state.companies.find((c) => c.id === state.company)?.name ||
    'Votre prochain marché commence ici';
  switchView(admin ? 'admin' : 'opportunities');
  await refresh();
}
async function restoreSession() {
  state.token = savedToken();
  if (!state.token) return;
  $('login-view').hidden = true;
  try {
    await openWorkspace();
  } catch (error) {
    // Une panne réseau ne supprime pas une session encore valide.
    $('workspace').hidden = true;
    $('login-view').hidden = false;
    notify(error.message);
  }
}
bind('logout', 'click', logout);
document
  .querySelectorAll('[data-view]')
  .forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
bind('company-select', 'change', async () => {
  state.company = $('company-select').value ? Number($('company-select').value) : null;
  updateScope();
  state.page = 1;
  state.selected = null;
  $('detail-dialog').close();
  await refresh();
  if (!$('profile-view').hidden) await loadProfile();
  if (!$('statement-view').hidden) await loadStatement();
  if (!$('contractors-view').hidden) await loadContractorReview();
  state.applicationsPage = 1;
  if (!$('applications-view').hidden) await loadApplications();
});
bind('filters', 'submit', async () => {
  state.page = 1;
  await refresh();
});
bind('previous', 'click', async () => {
  state.page--;
  await refresh();
});
bind('next', 'click', async () => {
  state.page++;
  await refresh();
});
bind('close-detail', 'click', () => {
  $('detail-dialog').close();
});
bind('screen', 'click', async () => {
  await api(`/opportunities/${encodeURIComponent(state.selected)}/screening?force=true`, {
    method: 'POST',
  });
  await openDetail(state.selected);
  await refresh();
  notify('Analyse mise à jour. Consultez les facteurs et le statut de l’analyse approfondie.');
});
bind('bid', 'click', async () => {
  await api(`/opportunities/${encodeURIComponent(state.selected)}/decision`, {
    method: 'POST',
    body: JSON.stringify({ decision: 'bid' }),
  });
  await openDetail(state.selected);
  await refresh();
  notify('Décision Bid enregistrée pour votre entreprise.');
});
bind('no-bid', 'click', () => {
  $('reason-form').hidden = false;
  $('reason').focus();
});
bind('reason-form', 'submit', async () => {
  await api(`/opportunities/${encodeURIComponent(state.selected)}/decision`, {
    method: 'POST',
    body: JSON.stringify({ decision: 'no_bid', reason: $('reason').value }),
  });
  $('reason').value = '';
  await openDetail(state.selected);
  await refresh();
});
bind('comment-form', 'submit', async () => {
  await api(`/opportunities/${encodeURIComponent(state.selected)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body: $('comment').value }),
  });
  await openDetail(state.selected);
});
bind('profile-form', 'submit', async () => {
  const profile = {
    company: {
      ...(state.profile?.company || {}),
      ...readIdentity(),
      legal_name: $('legal-name').value,
      small_business: $('small-business').checked,
      certifications: csv('certifications'),
      sam_registration_expires: $('sam-expires').value || null,
    },
    targeting: {
      naics_primary: csv('primary-naics'),
      naics_secondary: csv('secondary-naics'),
      psc_codes: csv('psc'),
      service_states: csv('service-states', true),
      min_days_to_respond: Number($('min-days').value),
      ideal_days: Number($('ideal-days').value),
      contract_value_min_usd: Number($('value-min').value),
      contract_value_max_usd: Number($('value-max').value),
      max_bonding_usd: Number($('bonding').value),
    },
  };
  profile.operations = readOperations();
  if (state.profile?.screening) profile.screening = state.profile.screening;
  await api(
    `/companies/${state.company}/profile`,
    { method: 'PUT', body: JSON.stringify(profile) },
    false,
  );
  state.profile = profile;
  notify('Profil enregistré. Les prochains screenings utiliseront ces qualifications.');
});
bind('company-form', 'submit', async () => {
  const c = await api(
    '/companies',
    { method: 'POST', body: JSON.stringify({ name: $('company-name').value }) },
    false,
  );
  await loadCompanies(c.id);
  $('company-form').reset();
  notify(
    'Espace créé. Ajoutez les utilisateurs pour qu’ils complètent leur profil et leurs justificatifs.',
  );
  await refresh();
});
bind('user-form', 'submit', async () => {
  if (!state.company) throw new Error('Sélectionnez une entreprise.');
  await api(
    '/users',
    {
      method: 'POST',
      body: JSON.stringify({
        email: $('new-email').value,
        password: $('new-password').value,
        role: $('new-role').value,
        company_id: Number(state.company),
      }),
    },
    false,
  );
  $('user-form').reset();
  notify(
    'Utilisateur ajouté à cette entreprise. Transmettez les identifiants par votre canal sécurisé habituel.',
  );
});
bind('sync-button', 'click', async () => {
  await api('/sync/run', { method: 'POST' }, false);
  await refresh();
  $('sync-button').disabled = true;
  clearTimeout(syncPollTimer);
  syncPollTimer = setTimeout(() => {
    if (state.token) refresh().catch((error) => notify(error.message));
  }, 1000);
  notify('Synchronisation demandée. La progression est visible dans le catalogue.');
});

const statementFields = [
  'tagline',
  'description',
  'capabilities',
  'achievements',
  'differentiators',
  'resources',
  'contact_name',
  'contact_email',
  'contact_phone',
];
function renderStatement(data, name, preview = $('statement-preview')) {
  preview.replaceChildren(node('h2', name));
  for (const [field, label] of [
    ['tagline', 'Notre engagement'],
    ['description', 'Présentation'],
    ['capabilities', 'Expertises'],
    ['achievements', 'Réalisations'],
    ['differentiators', 'Nos atouts'],
    ['resources', 'Nos moyens'],
  ]) {
    if (data[field]) preview.append(node('h3', label), node('p', data[field]));
  }
  const contact = [data.contact_name, data.contact_email, data.contact_phone]
    .filter(Boolean)
    .join(' · ');
  if (contact) preview.append(node('h3', 'Contact'), node('p', contact));
  if (!Object.values(data).some((value) => (Array.isArray(value) ? value.length : Boolean(value))))
    preview.append(node('p', 'Votre présentation apparaîtra ici après enregistrement.', 'muted'));
}
async function loadStatement() {
  const company = state.company;
  $('statement-form').reset();
  $('statement-preview').replaceChildren();
  $('statement-saved').textContent = 'Chargement de la présentation…';
  for (const input of $('statement-form').elements) input.disabled = true;
  const data = await api(`/companies/${company}/statement`, {}, false);
  if (company !== state.company) return;
  for (const input of $('statement-form').elements) input.disabled = false;
  for (const field of statementFields) $('stmt-' + field).value = data.statement[field] || '';
  $('statement-saved').textContent = '';
  state.statement = data.statement;
  renderCollection(
    'statement-references',
    'Références de projets',
    referenceFields,
    data.statement.references || [],
  );
  $('government-experience').value = data.statement.government_experience || '';
  renderStatement(data.statement, data.name);
  showStatementMethod(data.mode || 'form');
  renderPdfState(data.pdf_available, data.mode);
}
bind('statement-form', 'submit', async () => {
  const company = state.company;
  const statement = {
    ...(state.statement || {}),
    ...Object.fromEntries(statementFields.map((field) => [field, $('stmt-' + field).value.trim()])),
  };
  const data = await api(
    `/companies/${company}/statement`,
    { method: 'PUT', body: JSON.stringify(statement) },
    false,
  );
  if (company !== state.company) return;
  state.statement = data.statement;
  renderStatement(
    data.statement,
    state.companies.find((c) => c.id === company)?.name || 'Votre entreprise',
  );
  $('statement-active-mode').textContent = 'Présentation utilisée : fiche remplie.';
  $('statement-saved').textContent =
    'Présentation enregistrée. Vous pourrez la compléter à tout moment.';
});
bind('complete-statement', 'click', () => switchView('statement'));
bind('open-catalog', 'click', async () => {
  state.company = null;
  $('company-select').value = '';
  state.page = 1;
  updateScope();
  switchView('opportunities');
  await refresh();
});
function renderPortfolio() {
  if (state.user.role !== 'admin') return;
  $('admin-metrics').replaceChildren(
    ...[
      ['Entreprises accompagnées', state.companies.length],
      ['Profils de ciblage renseignés', state.companies.filter((c) => c.profile_complete).length],
      ['Profils à compléter', state.companies.filter((c) => !c.profile_complete).length],
    ].map(([label, count]) => {
      const n = node('div', undefined, 'metric');
      n.append(node('span', label), node('strong', String(count)));
      return n;
    }),
  );
  $('company-portfolio').replaceChildren(
    ...state.companies.map((company) => {
      const item = node('article', undefined, 'portfolio-item');
      item.append(
        node('h3', company.name),
        node(
          'p',
          company.profile_complete
            ? 'Profil de ciblage renseigné'
            : 'Profil de ciblage à compléter',
          'muted',
        ),
      );
      const button = node('button', 'Contrôler le dossier entreprise', 'secondary');
      button.addEventListener('click', async () => {
        try {
          state.company = company.id;
          $('company-select').value = String(company.id);
          state.page = 1;
          updateScope();
          switchView('contractors');
          await refresh();
        } catch (error) {
          notify(error.message);
        }
      });
      item.append(button);
      return item;
    }),
  );
  if (!state.companies.length)
    $('company-portfolio').append(
      node('p', 'Créez votre première entreprise pour commencer son accompagnement.', 'muted'),
    );
}

function showStatementMethod(mode) {
  $('statement-method-form').checked = mode === 'form';
  $('statement-method-pdf').checked = mode === 'pdf';
  $('statement-form-panel').hidden = mode === 'pdf';
  $('statement-pdf-panel').hidden = mode !== 'pdf';
}
function renderPdfState(available, mode) {
  $('statement-pdf-actions').hidden = !available;
  $('statement-pdf-status').textContent = available
    ? 'Un PDF est enregistré. Un nouvel envoi le remplacera.'
    : 'Aucun PDF enregistré.';
  $('statement-active-mode').textContent =
    mode === 'pdf'
      ? 'Présentation utilisée : document PDF.'
      : 'Présentation utilisée : fiche remplie.';
}
bind('statement-method-form', 'change', () => showStatementMethod('form'));
bind('statement-method-pdf', 'change', () => showStatementMethod('pdf'));
bind('statement-pdf-form', 'submit', async () => {
  const file = $('statement-pdf-file').files[0];
  if (!file || file.size > 5 * 1024 * 1024) throw new Error('Sélectionnez un PDF de 5 Mo maximum.');
  const company = state.company;
  await api(
    `/companies/${company}/statement/pdf`,
    { method: 'PUT', headers: { 'Content-Type': 'application/pdf' }, body: file },
    false,
  );
  if (company !== state.company) return;
  $('statement-pdf-form').reset();
  renderPdfState(true, 'pdf');
  notify('PDF enregistré comme présentation de votre entreprise.');
});
bind('download-statement-pdf', 'click', async () => {
  const blob = await api(`/companies/${state.company}/statement/pdf`, {}, false, 'blob');
  const url = URL.createObjectURL(blob);
  const link = node('a');
  link.href = url;
  link.download = 'company-statement.pdf';
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
bind('activate-statement-pdf', 'click', async () => {
  const company = state.company;
  await api(`/companies/${company}/statement/pdf/activate`, { method: 'POST' }, false);
  if (company === state.company) renderPdfState(true, 'pdf');
});
bind('delete-statement-pdf', 'click', async () => {
  const company = state.company;
  await api(`/companies/${company}/statement/pdf`, { method: 'DELETE' }, false);
  if (company === state.company) {
    renderPdfState(false, 'form');
    showStatementMethod('form');
  }
});
const applicationStages = {
  interested: 'Vœu reçu',
  examining: 'Examen en cours',
  preparing: 'Préparation du dossier',
  ready: 'Prêt pour envoi',
  submitted: 'Envoyé · En suivi',
  won: 'Marché gagné',
  lost: 'Non retenue',
  withdrawn: 'Candidature arrêtée',
};
const nextStageLabels = {
  interested: 'Prendre en charge et commencer l’examen',
  examining: 'Valider l’examen et passer à la préparation',
  preparing: 'Valider le dossier prêt pour envoi',
  ready: 'Confirmer l’envoi et passer au suivi',
  submitted: 'Publier le résultat',
};
async function loadApplications() {
  const company = state.company;
  const admin = state.user.role === 'admin';
  $('application-list').replaceChildren();
  $('applications-count').textContent = 'Chargement des candidatures…';
  const path = company ? `/companies/${company}/applications` : '/admin/applications';
  const data = await api(path + `?page=${state.applicationsPage}`, {}, false);
  if (company !== state.company) return;
  $('applications-count').textContent =
    data.total + (admin ? ' dossier(s) à accompagner' : ' vœu(x) / candidature(s)');
  $('applications-page').textContent = 'Page ' + state.applicationsPage;
  $('applications-previous').disabled = state.applicationsPage === 1;
  $('applications-next').disabled = state.applicationsPage * 20 >= data.total;
  for (const item of data.items) {
    const card = node('article', undefined, 'panel application-card');
    card.append(
      node('span', applicationStages[item.stage], 'badge'),
      node('h3', item.title),
      node('p', deadlineText(item.deadline)),
    );
    if (admin)
      card.append(
        node('p', item.company_name, 'agency'),
        node(
          'p',
          item.decision === 'bid' ? 'Bid validé' : 'Examen et décision Bid à effectuer',
          'muted',
        ),
      );
    const steps = node('ol', undefined, 'application-progress');
    const ordered = ['interested', 'examining', 'preparing', 'ready', 'submitted'];
    for (const [index, stage] of ordered.entries()) {
      const completed = ['won', 'lost'].includes(item.stage) || index < ordered.indexOf(item.stage);
      const step = node(
        'li',
        applicationStages[stage],
        completed ? 'completed' : stage === item.stage ? 'current' : '',
      );
      if (stage === item.stage) step.setAttribute('aria-current', 'step');
      steps.append(step);
    }
    card.append(steps);
    if (item.history.length) {
      const history = node('div', undefined, 'application-history');
      history.append(node('h4', 'Avancement communiqué'));
      for (const entry of item.history)
        history.append(
          node(
            'p',
            new Date(entry.created_at).toLocaleString('fr-FR') +
              ' · ' +
              applicationStages[entry.to_stage],
            'muted',
          ),
          node('p', entry.summary),
        );
      card.append(history);
    } else
      card.append(
        node(
          'p',
          item.stage === 'interested'
            ? 'Votre vœu est enregistré. Votre accompagnateur va examiner cette offre.'
            : 'Votre accompagnateur prend en charge cette candidature.',
          'muted',
        ),
      );
    const open = node('button', admin ? 'Examiner le dossier' : 'Revoir l’offre', 'secondary');
    open.addEventListener('click', async () => {
      try {
        if (admin) {
          state.company = item.company_id;
          $('company-select').value = String(item.company_id);
          updateScope();
        }
        await openDetail(item.notice_id);
      } catch (error) {
        notify(error.message);
      }
    });
    card.append(open);
    if (admin) {
      const endpoint = `/companies/${item.company_id}/applications/${encodeURIComponent(item.notice_id)}`;
      const notesForm = node('form', undefined, 'application-notes-form');
      const notesLabel = node('label', 'Notes internes (invisibles à l’entreprise)');
      const notes = node('textarea');
      notes.value = item.notes;
      notes.maxLength = 5000;
      notesLabel.append(notes);
      const save = node('button', 'Enregistrer les notes internes', 'secondary');
      save.type = 'submit';
      notesForm.append(notesLabel, save);
      notesForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        save.disabled = true;
        try {
          await api(
            endpoint,
            { method: 'PUT', body: JSON.stringify({ notes: notes.value }) },
            false,
          );
          notify('Notes internes enregistrées.');
        } catch (error) {
          notify(error.message);
        } finally {
          save.disabled = false;
        }
      });
      card.append(notesForm);
      if (!['won', 'lost', 'withdrawn'].includes(item.stage)) {
        const form = node('form', undefined, 'application-validation');
        const label = node('label', 'Compte rendu visible par l’entreprise');
        const summary = node('textarea');
        summary.required = true;
        summary.maxLength = 2000;
        label.append(summary);
        form.append(label);
        let evidence;
        if (item.stage === 'ready') {
          const evidenceLabel = node(
            'label',
            'Référence de l’envoi effectué (accusé, portail ou email)',
          );
          evidence = node('input');
          evidence.required = true;
          evidence.maxLength = 500;
          evidenceLabel.append(evidence);
          form.append(evidenceLabel);
          form.append(
            node(
              'p',
              'Confirmez uniquement un envoi déjà réalisé. Ce bouton ne transmet aucun dossier.',
              'muted',
            ),
          );
        }
        let outcome;
        if (item.stage === 'submitted') {
          const outcomeLabel = node('label', 'Résultat du marché');
          outcome = node('select');
          for (const stage of ['won', 'lost']) {
            const option = node('option', applicationStages[stage]);
            option.value = stage;
            outcome.append(option);
          }
          outcomeLabel.append(outcome);
          form.append(outcomeLabel);
        }
        const advance = node('button', nextStageLabels[item.stage]);
        advance.type = 'submit';
        if (['examining', 'preparing', 'ready'].includes(item.stage) && item.decision !== 'bid') {
          advance.disabled = true;
          form.append(
            node('p', 'Examinez le dossier et validez le Bid avant de poursuivre.', 'muted'),
          );
        }
        const stop = node('button', 'Arrêter cette candidature', 'secondary');
        stop.type = 'submit';
        stop.dataset.outcome = 'withdrawn';
        stop.formNoValidate = true;
        form.append(advance, stop);
        form.addEventListener('submit', async (event) => {
          event.preventDefault();
          if (!summary.value.trim()) {
            summary.reportValidity();
            return;
          }
          const target = event.submitter?.dataset.outcome || outcome?.value || null;
          const buttons = form.querySelectorAll('button');
          buttons.forEach((b) => (b.disabled = true));
          try {
            await api(
              endpoint + '/validate',
              {
                method: 'POST',
                body: JSON.stringify({
                  expected_stage: item.stage,
                  summary: summary.value,
                  outcome: target,
                  evidence: evidence?.value || '',
                }),
              },
              false,
            );
            await loadApplications();
          } catch (error) {
            notify(error.message);
            advance.disabled =
              ['examining', 'preparing', 'ready'].includes(item.stage) && item.decision !== 'bid';
            stop.disabled = false;
          }
        });
        card.append(form);
      }
    }
    $('application-list').append(card);
  }
  if (!data.items.length)
    $('application-list').append(
      node(
        'p',
        admin
          ? 'Aucun vœu à traiter pour le moment.'
          : 'Aucun vœu pour le moment. Ouvrez une opportunité puis cliquez sur « Exprimer mon intérêt ». Votre accompagnateur s’occupera de la suite.',
        'panel',
      ),
    );
}
bind('track-application', 'click', async () => {
  const company = state.company;
  await api(
    `/companies/${company}/applications/${encodeURIComponent(state.selected)}/follow`,
    { method: 'POST' },
    false,
  );
  $('detail-dialog').close();
  state.applicationsPage = 1;
  switchView('applications');
});
bind('applications-previous', 'click', async () => {
  state.applicationsPage--;
  await loadApplications();
});
bind('applications-next', 'click', async () => {
  state.applicationsPage++;
  await loadApplications();
});
bind('refresh-applications', 'click', loadApplications);
bind('all-applications', 'click', async () => {
  state.company = null;
  $('company-select').value = '';
  state.applicationsPage = 1;
  updateScope();
  await loadApplications();
});
bind('admin-applications', 'click', () => {
  state.company = null;
  $('company-select').value = '';
  state.applicationsPage = 1;
  updateScope();
  switchView('applications');
});

const identityFields = [
  'address',
  'city',
  'postal_code',
  'country',
  'website',
  'contact_email',
  'contact_phone',
  'uei',
  'cage_code',
];
const operationFields = [
  'employee_count',
  'crew_count',
  'concurrent_projects',
  'equipment',
  'service_area_details',
  'availability',
  'available_from',
  'availability_notes',
  'workers_comp_applicable',
  'workers_comp_explanation',
  'licenses_applicable',
  'licenses_explanation',
];
const credentialFields = [
  ['name', 'Intitulé', 'text', true],
  ['issuer', 'Organisme émetteur'],
  ['number', 'Numéro'],
  ['jurisdiction', 'État / juridiction'],
  ['expires_on', 'Expiration', 'date'],
];
const rateFields = [
  ['service', 'Prestation', 'text', true],
  [
    'unit',
    'Unité',
    'select',
    true,
    {
      hour: 'Heure',
      day: 'Jour',
      project: 'Projet',
      sqft: 'Pied carré',
      visit: 'Intervention',
      other: 'Autre',
    },
  ],
  ['amount_usd', 'Tarif (USD)', 'number', true],
  ['notes', 'Conditions', 'textarea'],
];
const referenceFields = [
  ['customer', 'Client / organisme', 'text', true],
  ['project', 'Projet réalisé', 'text', true],
  ['year', 'Année', 'number'],
  ['scope', 'Mission et résultats', 'textarea'],
  ['value_usd', 'Montant (USD)', 'number'],
  ['contact', 'Contact de référence'],
  ['government', 'Client public', 'checkbox'],
  ['contract_number', 'Numéro de contrat'],
];
function renderCollection(id, title, fields, values = []) {
  const root = $(id);
  root.replaceChildren(node('h3', title));
  const list = node('div', undefined, 'collection-list');
  const addRow = (value = {}) => {
    const row = node('div', undefined, 'collection-row form-grid');
    for (const [key, label, type = 'text', required = false, options] of fields) {
      const wrapper = node('label', label);
      const input = node(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
      if (!['textarea', 'select'].includes(type)) input.type = type;
      input.dataset.property = key;
      input.required = required;
      if (type === 'select')
        for (const [val, text] of Object.entries(options)) {
          const option = node('option', text);
          option.value = val;
          input.append(option);
        }
      if (type === 'checkbox') {
        input.checked = Boolean(value[key]);
        wrapper.className = 'checkbox';
      } else input.value = value[key] ?? (type === 'select' ? Object.keys(options)[0] : '');
      if (type === 'number') {
        input.min = key === 'year' ? '1900' : '0';
        input.step = key === 'year' ? '1' : 'any';
        if (key === 'year') input.max = '2200';
      }
      if (['text', 'textarea'].includes(type))
        input.maxLength =
          type === 'textarea'
            ? key === 'scope'
              ? 3000
              : 1000
            : key === 'contact'
              ? 300
              : key === 'number' || key === 'jurisdiction' || key === 'contract_number'
                ? 100
                : 200;
      wrapper.append(input);
      row.append(wrapper);
    }
    const remove = node('button', 'Retirer cette ligne', 'secondary');
    remove.type = 'button';
    remove.addEventListener('click', () => row.remove());
    row.append(remove);
    list.append(row);
  };
  values.forEach(addRow);
  const add = node('button', 'Ajouter : ' + title, 'secondary');
  add.type = 'button';
  add.addEventListener('click', () => {
    if (list.children.length >= 30) {
      notify('30 lignes maximum.');
      return;
    }
    addRow();
  });
  root.append(list, add);
}
function readCollection(id) {
  return [...$(id).querySelectorAll('.collection-row')].map((row) =>
    Object.fromEntries(
      [...row.querySelectorAll('[data-property]')].map((input) => [
        input.dataset.property,
        input.type === 'checkbox'
          ? input.checked
          : input.type === 'number'
            ? input.value === ''
              ? null
              : Number(input.value)
            : input.type === 'date'
              ? input.value || null
              : input.value.trim(),
      ]),
    ),
  );
}
function fillOnboarding(profile) {
  for (const key of identityFields)
    $('company-' + key).value = profile?.company?.[key] || (key === 'country' ? 'US' : '');
  $('company-service_categories').value = profile?.company?.service_categories?.join(', ') || '';
  for (const key of operationFields) {
    const value = profile?.operations?.[key];
    $('op-' + key).value =
      value === undefined || value === null
        ? key === 'availability'
          ? 'unknown'
          : ''
        : String(value);
  }
  renderCollection(
    'profile-licenses',
    'Licences professionnelles',
    credentialFields,
    profile?.operations?.licenses || [],
  );
  renderCollection(
    'profile-certifications',
    'Certifications détaillées',
    credentialFields,
    profile?.operations?.certifications || [],
  );
  renderCollection('profile-rates', 'Tarifs', rateFields, profile?.operations?.rates || []);
}
function readIdentity() {
  return {
    ...Object.fromEntries(identityFields.map((key) => [key, $('company-' + key).value.trim()])),
    service_categories: csv('company-service_categories'),
  };
}
function readOperations() {
  const result = {};
  for (const key of operationFields) {
    const value = $('op-' + key).value.trim();
    result[key] = ['employee_count', 'crew_count', 'concurrent_projects'].includes(key)
      ? value === ''
        ? null
        : Number(value)
      : key.endsWith('_applicable')
        ? value === ''
          ? null
          : value === 'true'
        : key === 'available_from'
          ? value || null
          : value;
  }
  result.licenses = readCollection('profile-licenses');
  result.certifications = readCollection('profile-certifications');
  result.rates = readCollection('profile-rates');
  return result;
}
bind('experience-form', 'submit', async () => {
  const company = state.company;
  const data = await api(
    `/companies/${company}/statement/experience`,
    {
      method: 'PATCH',
      body: JSON.stringify({
        government_experience: $('government-experience').value,
        references: readCollection('statement-references'),
      }),
    },
    false,
  );
  if (company !== state.company) return;
  state.statement = data.statement;
  $('experience-status').textContent = 'Références et expérience enregistrées.';
});
const documentKinds = {
  w9: 'W-9',
  insurance: 'Certificate of Insurance',
  workers_comp: 'Workers’ compensation',
  license: 'Licence professionnelle',
  certification: 'Certification',
};
const documentStatuses = {
  missing: 'Manquant',
  to_confirm: 'Applicabilité à préciser',
  not_applicable: 'Déclaré non applicable',
  pending: 'À contrôler',
  accepted: 'Accepté par l’admin',
  rejected: 'À corriger',
  expired: 'Expiré',
};
async function downloadPrivate(path, filename) {
  const blob = await api(path, {}, false, 'blob');
  const url = URL.createObjectURL(blob);
  const link = node('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function renderDocuments(target, company, admin) {
  target.replaceChildren(node('p', 'Chargement des justificatifs…', 'muted'));
  const data = await api(`/companies/${company}/documents`, {}, false);
  if (state.company !== company) return;
  target.replaceChildren();
  const checklist = node('div', undefined, 'document-checklist');
  for (const requirement of data.requirements)
    checklist.append(
      node('p', documentKinds[requirement.kind] + ' : ' + documentStatuses[requirement.status]),
    );
  target.append(checklist);
  for (const doc of data.items) {
    const card = node('article', undefined, 'document-card');
    card.append(
      node('h3', doc.title),
      node('p', documentKinds[doc.kind] + ' · ' + documentStatuses[doc.status]),
      node(
        'p',
        'Expiration : ' + (doc.expires_on || 'non précisée / sans échéance déclarée'),
        'muted',
      ),
    );
    if (doc.review_note) card.append(node('p', 'Retour de l’administrateur : ' + doc.review_note));
    const endpoint = `/companies/${company}/documents/${doc.id}`;
    const download = node('button', 'Télécharger le justificatif', 'secondary');
    download.type = 'button';
    download.addEventListener('click', () =>
      downloadPrivate(endpoint + '/pdf', `justificatif-${doc.id}.pdf`).catch((e) =>
        notify(e.message),
      ),
    );
    card.append(download);
    if (admin) {
      const form = node('form', undefined, 'document-review-form');
      const statusLabel = node('label', 'Contrôle du justificatif');
      const status = node('select');
      for (const [value, label] of [
        ['accepted', 'Accepter'],
        ['rejected', 'Demander une correction'],
      ]) {
        const option = node('option', label);
        option.value = value;
        status.append(option);
      }
      statusLabel.append(status);
      const noteLabel = node('label', 'Retour visible par l’entreprise');
      const note = node('textarea');
      note.maxLength = 2000;
      note.value = doc.review_note || '';
      noteLabel.append(note);
      const save = node('button', 'Valider le contrôle');
      save.type = 'submit';
      form.append(statusLabel, noteLabel, save);
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        save.disabled = true;
        try {
          await api(
            endpoint + '/review',
            { method: 'POST', body: JSON.stringify({ status: status.value, note: note.value }) },
            false,
          );
          await renderDocuments(target, company, admin);
        } catch (error) {
          notify(error.message);
        } finally {
          save.disabled = false;
        }
      });
      card.append(form);
    } else {
      const remove = node('button', 'Supprimer ce justificatif', 'secondary');
      remove.type = 'button';
      remove.addEventListener('click', async () => {
        remove.disabled = true;
        try {
          await api(endpoint, { method: 'DELETE' }, false);
          await renderDocuments(target, company, admin);
        } catch (error) {
          notify(error.message);
          remove.disabled = false;
        }
      });
      card.append(remove);
    }
    target.append(card);
  }
  if (!admin) {
    const form = node('form', undefined, 'document-upload-form');
    form.append(node('h3', 'Ajouter un justificatif PDF'));
    const kindLabel = node('label', 'Type de justificatif');
    const kind = node('select');
    for (const [value, text] of Object.entries(documentKinds)) {
      const option = node('option', text);
      option.value = value;
      kind.append(option);
    }
    kindLabel.append(kind);
    const titleLabel = node('label', 'Intitulé (précisez la licence ou certification concernée)');
    const title = node('input');
    title.required = true;
    title.maxLength = 200;
    titleLabel.append(title);
    const expiryLabel = node('label', 'Date d’expiration si applicable');
    const expiry = node('input');
    expiry.type = 'date';
    expiryLabel.append(expiry);
    const fileLabel = node('label', 'Justificatif PDF');
    const file = node('input');
    file.type = 'file';
    file.accept = '.pdf,application/pdf';
    file.required = true;
    fileLabel.append(file);
    const upload = node('button', 'Déposer le justificatif');
    upload.type = 'submit';
    form.append(kindLabel, titleLabel, expiryLabel, fileLabel, upload);
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      upload.disabled = true;
      try {
        const pdf = file.files[0];
        if (!pdf || pdf.size > 5 * 1024 * 1024) throw new Error('PDF de 5 Mo maximum.');
        const params = new URLSearchParams({ kind: kind.value, title: title.value });
        if (expiry.value) params.set('expires_on', expiry.value);
        await api(
          `/companies/${company}/documents?${params}`,
          { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: pdf },
          false,
        );
        await renderDocuments(target, company, admin);
      } catch (error) {
        notify(error.message);
      } finally {
        upload.disabled = false;
      }
    });
    target.append(form);
  }
}
const dossierLabels = {
  company: 'Identité',
  targeting: 'Ciblage',
  operations: 'Capacité et disponibilité',
  legal_name: 'Raison sociale',
  small_business: 'Small business',
  certifications: 'Certifications',
  address: 'Adresse',
  city: 'Ville',
  postal_code: 'Code postal',
  country: 'Pays',
  website: 'Site web',
  contact_email: 'Email',
  contact_phone: 'Téléphone',
  uei: 'UEI',
  cage_code: 'CAGE',
  service_categories: 'Métiers et services',
  naics_primary: 'NAICS principaux',
  naics_secondary: 'NAICS secondaires',
  psc_codes: 'PSC',
  service_states: 'États couverts',
  min_days_to_respond: 'Délai minimum (jours)',
  ideal_days: 'Délai idéal (jours)',
  contract_value_min_usd: 'Marché minimum (USD)',
  contract_value_max_usd: 'Marché maximum (USD)',
  max_bonding_usd: 'Cautionnement (USD)',
  sam_registration_expires: 'Expiration SAM',
  employee_count: 'Effectif',
  crew_count: 'Équipes',
  concurrent_projects: 'Projets simultanés',
  equipment: 'Équipements',
  service_area_details: 'Zone détaillée',
  availability: 'Disponibilité',
  available_from: 'Disponible à partir du',
  availability_notes: 'Précisions',
  workers_comp_applicable: 'Workers’ compensation applicable',
  workers_comp_explanation: 'Justification',
  licenses_applicable: 'Licences applicables',
  licenses_explanation: 'Justification',
  licenses: 'Licences',
  rates: 'Tarifs indicatifs',
  name: 'Intitulé',
  issuer: 'Émetteur',
  number: 'Numéro',
  jurisdiction: 'Juridiction',
  expires_on: 'Expiration',
  service: 'Prestation',
  unit: 'Unité',
  amount_usd: 'Montant (USD)',
  notes: 'Conditions',
  customer: 'Client',
  project: 'Projet',
  year: 'Année',
  scope: 'Mission et résultats',
  value_usd: 'Montant (USD)',
  contact: 'Contact',
  government: 'Client public',
  contract_number: 'Contrat',
};
function appendDossierData(target, value) {
  if (Array.isArray(value)) {
    value.forEach((entry) => {
      const block = node('div', undefined, 'dossier-entry');
      if (typeof entry === 'object') appendDossierData(block, entry);
      else block.textContent = entry;
      target.append(block);
    });
    return;
  }
  for (const [key, val] of Object.entries(value || {})) {
    if (val === null || val === '' || (Array.isArray(val) && !val.length)) continue;
    const block = node('div', undefined, 'dossier-field');
    block.append(node('strong', dossierLabels[key] || key));
    if (typeof val === 'object') appendDossierData(block, val);
    else block.append(node('p', typeof val === 'boolean' ? (val ? 'Oui' : 'Non') : String(val)));
    target.append(block);
  }
}
async function loadContractorReview() {
  const company = state.company;
  const target = $('contractor-review');
  target.replaceChildren();
  if (!company) {
    target.append(
      node(
        'p',
        'Sélectionnez une entreprise dans « Espace actif » pour contrôler son dossier.',
        'panel',
      ),
    );
    return;
  }
  const [profile, statement] = await Promise.all([
    api(`/companies/${company}/profile`, {}, false),
    api(`/companies/${company}/statement`, {}, false),
  ]);
  if (company !== state.company) return;
  const panel = node('section', undefined, 'panel onboarding-panel');
  panel.append(node('h2', profile.name));
  if (profile.profile)
    appendDossierData(panel, {
      company: profile.profile.company,
      targeting: profile.profile.targeting,
      operations: profile.profile.operations,
    });
  else panel.append(node('p', 'Profil à compléter par l’entreprise.'));
  target.append(panel);
  const presentation = node('section', undefined, 'panel onboarding-panel');
  presentation.append(node('h2', 'Company Statement'));
  renderStatement(statement.statement, statement.name, presentation);
  if (statement.pdf_available) {
    const download = node('button', 'Télécharger le Company Statement PDF', 'secondary');
    download.addEventListener('click', () =>
      downloadPrivate(`/companies/${company}/statement/pdf`, 'company-statement.pdf').catch((e) =>
        notify(e.message),
      ),
    );
    presentation.append(
      node(
        'p',
        statement.mode === 'pdf'
          ? 'Présentation principale : PDF'
          : 'Présentation principale : formulaire',
      ),
      download,
    );
  }
  if (statement.statement.government_experience)
    presentation.append(
      node('h3', 'Expérience gouvernementale'),
      node('p', statement.statement.government_experience),
    );
  if (statement.statement.references?.length) {
    presentation.append(node('h3', 'Références'));
    const refs = node('div');
    appendDossierData(refs, statement.statement.references);
    presentation.append(refs);
  }
  target.append(presentation);
  const docs = node('section', undefined, 'panel onboarding-panel');
  docs.append(node('h2', 'Contrôle des justificatifs'));
  const content = node('div');
  docs.append(content);
  target.append(docs);
  await renderDocuments(content, company, true);
}
restoreSession();
