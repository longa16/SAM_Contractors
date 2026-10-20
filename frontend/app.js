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
}
function switchView(view) {
  if (view === 'admin' && state.user?.role !== 'admin') return;
  if (['profile', 'statement', 'applications'].includes(view) && !state.company) return;
  for (const name of ['opportunities', 'profile', 'statement', 'applications', 'admin'])
    $(name + '-view').hidden = name !== view;
  document
    .querySelectorAll('[data-view]')
    .forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $('page-title').textContent = {
    opportunities: state.company ? 'Vos opportunités' : 'Catalogue SAM',
    profile: 'Profil entreprise',
    admin: 'Supervision de la plateforme',
    statement: 'Company Statement',
    applications: 'Suivi des candidatures',
  }[view];
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
  $('applications-nav').disabled = global;
  $('user-form').querySelector('button[type="submit"]').disabled = global;
  if (
    global &&
    (!$('profile-view').hidden || !$('statement-view').hidden || !$('applications-view').hidden)
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
      ['Catalogue filtré', data.total],
      ...(state.company
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
    state.company
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
  $('company-discussion').hidden = !state.company;
  const decide =
    Boolean(state.company) && ['admin', 'business_developer'].includes(state.user.role);
  $('screen').hidden = !decide;
  $('track-application').hidden = !decide;
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
  for (const input of $('profile-form').elements)
    input.disabled = state.user.role === 'ops_manager';
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
  $('admin-nav').hidden = $('sync-button').hidden = !admin;
  $('company-control').hidden = !admin;
  $('space-label').textContent = admin ? 'ADMINISTRATION' : 'ESPACE ENTREPRISE';
  $('nav-label').textContent = admin ? 'SUPERVISION' : 'VOTRE DÉVELOPPEMENT';
  $('opportunities-nav').textContent = admin ? '▦ Catalogue SAM' : '▦ Opportunités';
  $('space-note').textContent = admin
    ? 'Supervisez le catalogue et accompagnez chaque entreprise dans son espace privé.'
    : 'Vos opportunités, votre savoir-faire, vos décisions. Un espace dédié à votre équipe.';
  $('company-welcome').hidden = admin;
  $('sync-state').hidden = !admin;
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
  notify('Espace créé. Complétez le profil et ajoutez les utilisateurs de cette entreprise.');
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
function renderStatement(data, name) {
  const preview = $('statement-preview');
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
  if (!Object.values(data).some(Boolean))
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
  renderStatement(data.statement, data.name);
  showStatementMethod(data.mode || 'form');
  renderPdfState(data.pdf_available, data.mode);
}
bind('statement-form', 'submit', async () => {
  const company = state.company;
  const statement = Object.fromEntries(
    statementFields.map((field) => [field, $('stmt-' + field).value.trim()]),
  );
  const data = await api(
    `/companies/${company}/statement`,
    { method: 'PUT', body: JSON.stringify(statement) },
    false,
  );
  if (company !== state.company) return;
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
      const button = node('button', 'Consulter le Company Statement', 'secondary');
      button.addEventListener('click', async () => {
        try {
          state.company = company.id;
          $('company-select').value = String(company.id);
          state.page = 1;
          updateScope();
          switchView('statement');
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
  interested: 'À étudier',
  preparing: 'En préparation',
  submitted: 'Déposée',
  won: 'Gagnée',
  lost: 'Non retenue',
  withdrawn: 'Retirée',
};
const applicationTransitions = {
  interested: ['preparing', 'withdrawn'],
  preparing: ['interested', 'submitted', 'withdrawn'],
  submitted: ['won', 'lost', 'withdrawn'],
  won: [],
  lost: [],
  withdrawn: ['interested', 'preparing'],
};
async function loadApplications() {
  const company = state.company;
  $('application-list').replaceChildren();
  $('applications-count').textContent = 'Chargement des candidatures…';
  const data = await api(
    `/companies/${company}/applications?page=${state.applicationsPage}`,
    {},
    false,
  );
  if (company !== state.company) return;
  $('applications-count').textContent = data.total + ' candidature(s) suivie(s)';
  $('applications-page').textContent = 'Page ' + state.applicationsPage;
  $('applications-previous').disabled = state.applicationsPage === 1;
  $('applications-next').disabled = state.applicationsPage * 20 >= data.total;
  for (const item of data.items) {
    const card = node('article', undefined, 'panel application-card');
    card.append(
      node('span', applicationStages[item.stage], 'badge'),
      node('h3', item.title),
      node('p', deadlineText(item.deadline)),
      node(
        'p',
        item.decision === 'bid' ? 'Décision : Bid validé' : 'Décision Bid à valider avant dépôt',
        'muted',
      ),
    );
    if (item.submitted_at)
      card.append(
        node(
          'p',
          'Dépôt déclaré le ' + new Date(item.submitted_at).toLocaleString('fr-FR'),
          'muted',
        ),
      );
    const open = node('button', 'Ouvrir le dossier', 'secondary');
    open.addEventListener('click', () =>
      openDetail(item.notice_id).catch((e) => notify(e.message)),
    );
    card.append(open);
    if (['admin', 'business_developer'].includes(state.user.role)) {
      const form = node('form', undefined, 'application-edit');
      const label = node('label', 'Étape de candidature');
      const select = node('select');
      for (const stage of [item.stage, ...applicationTransitions[item.stage]]) {
        const option = node('option', applicationStages[stage]);
        option.value = stage;
        option.disabled = ['submitted', 'won', 'lost'].includes(stage) && item.decision !== 'bid';
        select.append(option);
      }
      label.append(select);
      const noteLabel = node('label', 'Notes de suivi');
      const notes = node('textarea');
      notes.value = item.notes;
      notes.maxLength = 5000;
      noteLabel.append(notes);
      const save = node('button', 'Enregistrer le suivi');
      save.type = 'submit';
      form.append(label, noteLabel, save);
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        save.disabled = true;
        try {
          await api(
            `/companies/${company}/applications/${encodeURIComponent(item.notice_id)}`,
            { method: 'PUT', body: JSON.stringify({ stage: select.value, notes: notes.value }) },
            false,
          );
          if (state.company === company) await loadApplications();
        } catch (error) {
          notify(error.message);
        } finally {
          save.disabled = false;
        }
      });
      card.append(form);
    } else if (item.notes) card.append(node('p', item.notes));
    $('application-list').append(card);
  }
  if (!data.items.length)
    $('application-list').append(
      node(
        'p',
        'Aucune candidature suivie. Ouvrez une opportunité puis cliquez sur « Suivre cette candidature ». Les décisions Bid apparaissent aussi automatiquement.',
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
restoreSession();
