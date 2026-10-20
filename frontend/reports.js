'use strict';
let reportHistoryPage = 1;
function reportQuery() {
  const params = new URLSearchParams();
  if (state.company) params.set('company_id', state.company);
  if ($('report-start').value) params.set('start', $('report-start').value);
  if ($('report-end').value) params.set('end', $('report-end').value);
  return params;
}
function reportTable(headers, rows) {
  if (!rows.length) return node('p', 'Aucune donnée pour cette sélection.', 'muted');
  const wrapper = node('div', undefined, 'table-scroll');
  const table = node('table');
  const head = node('thead');
  const header = node('tr');
  for (const text of headers) {
    const cell = node('th', text);
    cell.scope = 'col';
    header.append(cell);
  }
  head.append(header);
  table.append(head);
  const body = node('tbody');
  for (const row of rows) {
    const tr = node('tr');
    row.forEach((text) => tr.append(node('td', String(text))));
    body.append(tr);
  }
  table.append(body);
  wrapper.append(table);
  return wrapper;
}
async function loadReports() {
  const company = state.company;
  const admin = state.user?.role === 'admin';
  const params = reportQuery();
  const [stats, history] = await Promise.all([
    api('/reports/statistics?' + params, {}, false),
    api('/reports/history?' + params + '&page=' + reportHistoryPage, {}, false),
  ]);
  if (company !== state.company || !state.user) return;
  $('report-period-basis').textContent = stats.period_basis;
  $('report-metrics').replaceChildren(
    ...[
      ['Candidatures', stats.total],
      ['En cours', stats.in_progress],
      ['Marchés gagnés', stats.won],
      ['Non retenues', stats.lost],
      ['Taux de réussite', stats.success_rate === null ? '—' : stats.success_rate + ' %'],
    ].map(([label, value]) => {
      const metric = node('div', undefined, 'metric');
      metric.append(node('span', label), node('strong', String(value)));
      return metric;
    }),
  );
  const bars = node('div', undefined, 'pipeline-bars');
  for (const [stage, label] of Object.entries(applicationStages)) {
    const row = node('div', undefined, 'pipeline-bar');
    const meter = node('meter');
    meter.min = 0;
    meter.max = Math.max(stats.total, 1);
    meter.value = stats.stages[stage] || 0;
    meter.setAttribute('aria-label', label);
    row.append(node('span', label), meter, node('strong', String(stats.stages[stage] || 0)));
    bars.append(row);
  }
  $('report-stages').replaceChildren(bars);
  $('report-months').replaceChildren(
    reportTable(
      ['Mois', 'Jalon validé', 'Passages'],
      stats.monthly_events.map((row) => [row.month, applicationStages[row.stage], row.count]),
    ),
  );
  $('report-companies-panel').hidden = !admin;
  $('report-companies').replaceChildren(
    admin
      ? reportTable(
          ['Entreprise', 'Dossiers'],
          stats.companies.map((row) => [row.name, row.count]),
        )
      : node('span'),
  );
  $('report-history-title').textContent = admin
    ? 'Journal des actions de la plateforme'
    : 'Historique de l’accompagnement';
  $('report-history').replaceChildren();
  for (const item of history.items) {
    const row = node('article', undefined, 'history-row');
    row.append(
      node('p', new Date(item.date).toLocaleString('fr-FR'), 'muted'),
      node('h3', item.action),
    );
    if (admin) {
      row.append(
        node('p', item.entity_type + ' · ' + item.entity_id + ' · acteur ' + item.actor, 'muted'),
      );
      if (item.before || item.after || item.meta) {
        const details = node('details');
        details.append(
          node('summary', 'Voir les détails'),
          node(
            'pre',
            JSON.stringify({ avant: item.before, après: item.after, contexte: item.meta }, null, 2),
          ),
        );
        row.append(details);
      }
    } else row.append(node('p', item.title), node('p', item.summary));
    $('report-history').append(row);
  }
  if (!history.items.length)
    $('report-history').append(node('p', 'Aucun événement dans cette sélection.', 'muted'));
  $('history-page').textContent = 'Page ' + reportHistoryPage;
  $('history-previous').disabled = reportHistoryPage === 1;
  $('history-next').disabled = !history.has_next;
}
bind('report-filters', 'submit', async () => {
  reportHistoryPage = 1;
  await loadReports();
});
bind('history-previous', 'click', async () => {
  reportHistoryPage--;
  await loadReports();
});
bind('history-next', 'click', async () => {
  reportHistoryPage++;
  await loadReports();
});
bind('export-applications', 'click', async () =>
  downloadPrivate('/reports/export/applications?' + reportQuery(), 'candidatures.csv'),
);
bind('export-history', 'click', async () =>
  downloadPrivate('/reports/export/history?' + reportQuery(), 'historique.csv'),
);
