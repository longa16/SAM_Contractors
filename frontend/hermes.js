'use strict';
let hermesPollTimer;
let hermesPage = 1;
let hermesScope;
function clearHermesPolling() {
  clearTimeout(hermesPollTimer);
}
const hermesStatuses = {
  queued: 'En attente',
  running: 'Examen en cours',
  succeeded: 'Rapport disponible',
  failed: 'Examen échoué',
  cancelled: 'Examen annulé',
};
const hermesRecommendations = {
  pursue: 'Candidature à envisager',
  review: 'Vérifications complémentaires',
  decline: 'Candidature déconseillée',
};
function hermesEndpoint(scope) {
  return `/companies/${scope.company}/applications/${encodeURIComponent(scope.notice)}/agent-examinations`;
}
function hermesAction(label, action, secondary = false) {
  const button = node('button', label, secondary ? 'secondary' : '');
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      await action();
    } catch (error) {
      notify(error.message);
    } finally {
      button.disabled = false;
    }
  });
  return button;
}
function hermesList(target, title, items) {
  if (!items.length) return;
  target.append(node('h4', title));
  const list = node('ul');
  for (const item of items) list.append(node('li', item));
  target.append(list);
}
function hermesReport(target, item) {
  const report = item.report;
  if (!report) return;
  const sources = new Map(item.sources.map((s) => [s.id, s.title]));
  target.append(
    node('h4', hermesRecommendations[report.recommendation]),
    node('p', report.summary),
  );
  hermesList(target, 'Motifs de la recommandation', report.rationale);
  for (const [title, findings] of [
    ['Exigences de l’appel d’offres', report.requirements],
    ['Correspondance avec l’entreprise', report.eligibility],
  ]) {
    if (!findings.length) continue;
    const details = node('details');
    details.append(node('summary', title));
    for (const finding of findings) {
      details.append(node('h4', finding.requirement || finding.criterion));
      if (finding.status)
        details.append(
          node(
            'p',
            { meets: 'Correspondance identifiée', gap: 'Écart identifié', unknown: 'À vérifier' }[
              finding.status
            ] +
              ' : ' +
              finding.explanation,
          ),
        );
      if (Object.hasOwn(finding, 'mandatory'))
        details.append(
          node(
            'p',
            finding.mandatory === true
              ? 'Exigence obligatoire'
              : finding.mandatory === false
                ? 'Exigence facultative'
                : 'Caractère obligatoire à vérifier',
            'muted',
          ),
        );
      for (const citation of finding.citations) {
        details.append(
          node('blockquote', citation.quote),
          node(
            'p',
            (sources.get(citation.source_id) || citation.source_id) + ' · ' + citation.source_id,
            'muted',
          ),
        );
      }
    }
    target.append(details);
  }
  for (const [title, items] of [
    ['Pièces manquantes ou à vérifier', report.missing_documents],
    ['Questions à poser à l’entreprise', report.questions],
    ['Prochaines actions proposées', report.next_actions],
    ['Limites de l’examen', report.limitations],
  ])
    hermesList(target, title, items);
}
async function loadHermesPanel(target, scope, stage) {
  clearHermesPolling();
  if (hermesScope !== scope) {
    hermesScope = scope;
    hermesPage = 1;
  }
  const endpoint = hermesEndpoint(scope);
  const data = await api(endpoint + '?page=' + hermesPage, {}, false);
  if (scope !== state.dossier || state.user?.role !== 'admin' || !target.isConnected) return;
  target.replaceChildren(node('h2', 'Examen avec Hermes'));
  target.append(
    node(
      'p',
      'Hermes propose un rapport interne à partir de l’avis, du profil, du Company Statement et des PDF de consultation joints. Contrôlez les conclusions avant de prendre votre décision Bid/No-Bid. Les étapes et les envois restent à votre main.',
      'muted',
    ),
  );
  if (!data.enabled)
    target.append(
      node(
        'p',
        data.configuration?.message ||
          'Le moteur Hermes doit être installé et activé sur le serveur.',
      ),
    );
  const workerStopped = data.enabled && data.worker_running === false;
  if (workerStopped)
    target.append(
      node(
        'p',
        'Hermes est configuré mais son traitement n’a pas démarré. Redémarrez le serveur après activation d’Hermes.',
        'badge',
      ),
    );
  const active = data.items.some((item) => ['queued', 'running'].includes(item.status));
  const refresh = () => loadHermesPanel(target, scope, stage);
  if (stage === 'examining') {
    const launch = hermesAction('Lancer l’examen avec Hermes', async () => {
      const run = await api(endpoint, { method: 'POST' }, false);
      notify(`Demande d’examen ${run.id} enregistrée. Le suivi s’actualise automatiquement.`);
      hermesPage = 1;
      await refresh();
    });
    launch.id = 'hermes-launch';
    launch.disabled = !data.enabled || workerStopped || active;
    target.append(launch);
  } else
    target.append(
      node(
        'p',
        'L’examen se lance à l’étape « Examen en cours », après acceptation de la demande ou initiation de la candidature.',
      ),
    );
  target.append(hermesAction('Actualiser les examens', refresh, true));
  if (!data.items.length)
    target.append(
      node(
        'p',
        stage === 'examining' && data.enabled && !workerStopped
          ? 'Aucun examen lancé. Cliquez sur « Lancer l’examen avec Hermes » pour démarrer l’analyse.'
          : 'Aucun examen lancé pour cette candidature.',
        'muted',
      ),
    );
  for (const item of data.items) {
    const card = node('article', undefined, 'document-card hermes-run');
    card.dataset.runId = item.id;
    card.append(
      node('h3', `Examen ${item.id} · ${hermesStatuses[item.status]}`),
      node('p', new Date(item.created_at).toLocaleString('fr-FR') + ' · ' + item.model, 'muted'),
    );
    if (item.stale)
      card.append(
        node(
          'p',
          'Les données de l’offre ou de l’entreprise ont changé. Lancez un nouvel examen avant validation.',
          'badge',
        ),
      );
    if (item.error) card.append(node('p', item.error));
    if (item.status === 'running' && item.started_at) {
      const elapsed = Math.max(
        0,
        Math.floor((Date.now() - new Date(item.started_at).getTime()) / 1000),
      );
      card.append(
        node(
          'p',
          `Traitement démarré depuis ${elapsed} s. Délai maximal : ${data.timeout_seconds || 240} s. Le rapport apparaîtra ici.`,
          'muted',
        ),
      );
    }
    if (item.status === 'queued')
      card.append(
        node(
          'p',
          'La demande est enregistrée et attend son traitement. Le suivi s’actualise automatiquement.',
          'muted',
        ),
      );
    hermesList(card, 'Sources manquantes ou lecture partielle', item.warnings);
    hermesReport(card, item);
    if (item.report) {
      card.append(
        node('p', item.tool_calls.length + ' consultation(s) de sources enregistrée(s).', 'muted'),
      );
      card.append(
        hermesAction(
          'Télécharger le rapport JSON',
          () => downloadPrivate(endpoint + `/${item.id}/report`, `examen-${item.id}.json`),
          true,
        ),
      );
    }
    if (['queued', 'running'].includes(item.status))
      card.append(
        hermesAction(
          'Annuler cet examen',
          async () => {
            await api(endpoint + `/${item.id}/cancel`, { method: 'POST' }, false);
            await refresh();
          },
          true,
        ),
      );
    if (item.review_decision)
      card.append(
        node(
          'p',
          (item.review_decision === 'accepted'
            ? 'Rapport validé par l’admin : '
            : 'Rapport refusé par l’admin : ') + item.review_note,
        ),
      );
    if (
      item.status === 'succeeded' &&
      !item.stale &&
      !item.review_decision &&
      stage === 'examining'
    ) {
      const form = node('form', undefined, 'hermes-review-form');
      const note = dossierField(form, 'Conclusion du contrôle humain (interne)', 'textarea', true);
      note.maxLength = 2000;
      for (const [decision, label] of [
        ['accepted', 'Valider le rapport d’examen'],
        ['rejected', 'Refuser ce rapport'],
      ]) {
        const button = node('button', label, decision === 'rejected' ? 'secondary' : '');
        button.type = 'submit';
        button.value = decision;
        form.append(button);
      }
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (!note.value.trim()) {
          note.value = '';
          note.reportValidity();
          return;
        }
        const buttons = form.querySelectorAll('button');
        buttons.forEach((button) => (button.disabled = true));
        try {
          await api(
            endpoint + `/${item.id}/decision`,
            {
              method: 'POST',
              body: JSON.stringify({ decision: event.submitter.value, note: note.value }),
            },
            false,
          );
          await refresh();
          notify(
            'Contrôle du rapport enregistré. La décision Bid/No-Bid et la validation de l’étape restent à effectuer.',
          );
        } catch (error) {
          notify(error.message);
          buttons.forEach((button) => (button.disabled = false));
        }
      });
      card.append(form);
    }
    target.append(card);
  }
  const navigation = node('div', undefined, 'pagination');
  const previous = hermesAction(
    'Examens précédents',
    async () => {
      hermesPage--;
      await refresh();
    },
    true,
  );
  previous.disabled = hermesPage === 1;
  const next = hermesAction(
    'Examens suivants',
    async () => {
      hermesPage++;
      await refresh();
    },
    true,
  );
  next.disabled = hermesPage * 10 >= data.total;
  navigation.append(previous, node('span', 'Page ' + hermesPage), next);
  target.append(navigation);
  if (active && !$('dossier-view').hidden)
    hermesPollTimer = setTimeout(() => refresh().catch((e) => notify(e.message)), 2500);
}
