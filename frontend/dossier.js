'use strict';
const dossierKinds = {
  solicitation: 'Consultation / appel d’offres',
  contractor: 'Pièces du contractor',
  prepared: 'Dossier préparé',
  supporting: 'Annexes de travail',
};
function openDossier(company, notice) {
  if (state.user?.role !== 'admin') return;
  state.company = company;
  $('company-select').value = String(company);
  state.dossier = { company, notice };
  updateScope();
  switchView('dossier');
}
function dossierEndpoint() {
  const scope = state.dossier;
  if (!scope) throw new Error('Ouvrez un dossier depuis une candidature.');
  return `/companies/${scope.company}/applications/${encodeURIComponent(scope.notice)}/dossier`;
}
function dossierField(form, label, type = 'text', required = false) {
  const wrapper = node('label', label);
  const input = node(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
  if (!['textarea', 'select'].includes(type)) input.type = type;
  input.required = required;
  wrapper.append(input);
  form.append(wrapper);
  return input;
}
function dossierOption(select, value, label) {
  const option = node('option', label);
  option.value = value;
  select.append(option);
  return option;
}
function dossierSubmit(form, label, action) {
  const save = node('button', label);
  save.type = 'submit';
  form.append(save);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      await action();
      await loadDossier();
    } catch (error) {
      notify(error.message);
    } finally {
      save.disabled = false;
    }
  });
  return save;
}
async function loadDossier() {
  const scope = state.dossier;
  const target = $('dossier-content');
  target.replaceChildren();
  if (!scope) {
    target.append(node('p', 'Sélectionnez une candidature pour ouvrir son dossier.', 'panel'));
    return;
  }
  const endpoint = dossierEndpoint();
  const data = await api(endpoint, {}, false);
  if (scope !== state.dossier || state.company !== scope.company || state.user?.role !== 'admin')
    return;
  target.dataset.revision = String(data.revision);
  $('dossier-title').textContent = data.title;
  const latest = new Map();
  for (const doc of data.documents) if (!doc.archived) latest.set(doc.document_key, doc);
  const current = [...latest.values()];
  const status = node('section', undefined, 'panel dossier-panel');
  status.append(
    node(
      'h2',
      !data.editable
        ? 'Dossier conservé après envoi ou clôture'
        : data.approved
          ? 'Contrôle final validé'
          : 'Contrôle final à terminer',
    ),
    node('p', 'Révision du dossier : ' + data.revision, 'muted'),
  );
  if (data.editable && !data.approved && data.approvals.length)
    status.append(
      node(
        'p',
        'Une validation antérieure est conservée. Le contenu ou ses conditions ont changé : contrôlez à nouveau le dossier.',
      ),
    );
  if (data.issues.length) {
    const issues = node('ul');
    issues.id = 'dossier-issues';
    for (const issue of data.issues) issues.append(node('li', issue));
    status.append(issues);
  } else if (!data.approved)
    status.append(
      node(
        'p',
        'Les contrôles sont renseignés. Validez explicitement le dossier final ci-dessous.',
      ),
    );
  if (!data.editable)
    status.append(node('p', 'Ce dossier est conservé en lecture après envoi ou clôture.'));
  target.append(status);
  const sources = node('section', undefined, 'panel dossier-panel');
  sources.append(node('h2', 'Documents de consultation'));
  const sam = node('a', 'Consulter l’avis sur SAM.gov');
  sam.href = 'https://sam.gov/opp/' + encodeURIComponent(scope.notice) + '/view';
  sam.target = '_blank';
  sam.rel = 'noopener noreferrer';
  sources.append(sam);
  for (const raw of data.resource_links || []) {
    if (typeof raw !== 'string') continue;
    try {
      const url = new URL(raw);
      if (!['https:', 'http:'].includes(url.protocol)) continue;
      const link = node('a', 'Ouvrir une pièce publiée par l’acheteur');
      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      sources.append(node('p'), link);
    } catch {
      /* Invalid external source is not a link. */
    }
  }
  sources.append(
    node(
      'p',
      'Téléchargez les pièces utiles puis déposez-les ci-dessous. Si aucune pièce n’est publiée, examinez la description et documentez le contrôle dans la checklist.',
      'muted',
    ),
  );
  target.append(sources);
  const files = node('section', undefined, 'panel dossier-panel');
  files.append(node('h2', 'Documents et versions'));
  for (const kind of Object.keys(dossierKinds)) {
    const docs = data.documents.filter((doc) => doc.kind === kind);
    if (!docs.length) continue;
    const group = node('div');
    group.append(node('h3', dossierKinds[kind]));
    for (const doc of docs) {
      const row = node('article', undefined, 'document-card');
      const isLatest = latest.get(doc.document_key)?.id === doc.id;
      row.append(
        node('strong', doc.title + ' · v' + doc.version),
        node(
          'p',
          doc.archived
            ? 'Archivé — conservé dans l’historique'
            : isLatest
              ? 'Version active'
              : 'Version antérieure',
          'muted',
        ),
      );
      if (doc.expires_on) row.append(node('p', 'Expiration : ' + doc.expires_on, 'muted'));
      const download = node('button', 'Télécharger cette version', 'secondary');
      download.addEventListener('click', () =>
        downloadPrivate(
          endpoint + `/documents/${doc.id}/pdf`,
          `dossier-${doc.id}-v${doc.version}.pdf`,
        ).catch((e) => notify(e.message)),
      );
      row.append(download);
      if (data.editable && isLatest) {
        const archive = node('button', 'Archiver ce document', 'secondary');
        archive.addEventListener('click', async () => {
          archive.disabled = true;
          try {
            await api(
              endpoint + `/documents/${doc.id}/archive?expected_revision=${data.revision}`,
              { method: 'POST' },
              false,
            );
            await loadDossier();
          } catch (error) {
            notify(error.message);
            archive.disabled = false;
          }
        });
        row.append(archive);
      }
      group.append(row);
    }
    files.append(group);
  }
  if (!data.documents.length)
    files.append(node('p', 'Aucun document ajouté pour cette candidature.', 'muted'));
  if (data.editable) {
    const form = node('form', undefined, 'dossier-form');
    form.id = 'dossier-upload';
    form.append(node('h3', 'Ajouter un PDF ou une nouvelle version'));
    const previous = dossierField(form, 'Document à versionner', 'select');
    dossierOption(previous, '', 'Nouveau document');
    for (const doc of current.filter((d) => d.kind !== 'contractor'))
      dossierOption(previous, doc.id, doc.title + ' · v' + doc.version);
    const kind = dossierField(form, 'Catégorie', 'select');
    for (const [value, label] of Object.entries(dossierKinds))
      if (value !== 'contractor') dossierOption(kind, value, label);
    const title = dossierField(form, 'Titre du document', 'text', true);
    title.maxLength = 200;
    previous.addEventListener('change', () => {
      const doc = current.find((d) => String(d.id) === previous.value);
      kind.disabled = Boolean(doc);
      if (doc) {
        kind.value = doc.kind;
        title.value = doc.title;
      }
    });
    const file = dossierField(form, 'Fichier PDF (5 Mo maximum)', 'file', true);
    file.accept = '.pdf,application/pdf';
    dossierSubmit(form, 'Enregistrer le document', async () => {
      const pdf = file.files[0];
      if (!pdf || pdf.size > 5 * 1024 * 1024)
        throw new Error('Sélectionnez un PDF de 5 Mo maximum.');
      const params = new URLSearchParams({
        kind: kind.value,
        title: title.value,
        expected_revision: data.revision,
      });
      if (previous.value) params.set('previous_document_id', previous.value);
      await api(
        endpoint + '/documents?' + params,
        { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: pdf },
        false,
      );
    });
    files.append(form);
    const imports = node('form', undefined, 'dossier-form');
    imports.id = 'dossier-import';
    imports.append(
      node('h3', 'Copier une pièce du contractor'),
      node(
        'p',
        'Seules les pièces acceptées et non expirées sont importables. La copie reste dans ce dossier si l’original est ensuite retiré.',
        'muted',
      ),
    );
    const source = dossierField(imports, 'Justificatif de l’entreprise', 'select', true);
    dossierOption(source, '', 'Sélectionner un justificatif');
    for (const doc of data.company_documents) {
      const option = dossierOption(
        source,
        doc.id,
        doc.title + (doc.accepted ? '' : ' — contrôle ou renouvellement nécessaire'),
      );
      option.disabled = !doc.accepted;
    }
    const replacement = dossierField(imports, 'Pièce à mettre à jour', 'select');
    dossierOption(replacement, '', 'Ajouter une nouvelle pièce');
    for (const doc of current.filter((d) => d.kind === 'contractor'))
      dossierOption(replacement, doc.id, doc.title + ' · v' + doc.version);
    dossierSubmit(imports, 'Importer la pièce dans le dossier', async () => {
      await api(
        endpoint + '/import',
        {
          method: 'POST',
          body: JSON.stringify({
            expected_revision: data.revision,
            source_document_id: Number(source.value),
            previous_document_id: replacement.value ? Number(replacement.value) : null,
          }),
        },
        false,
      );
    });
    files.append(imports);
    if (data.statement_pdf_available) {
      const statement = node('button', 'Copier le Company Statement PDF', 'secondary');
      statement.addEventListener('click', async () => {
        statement.disabled = true;
        try {
          await api(
            endpoint + '/import-statement?expected_revision=' + data.revision,
            { method: 'POST' },
            false,
          );
          await loadDossier();
        } catch (error) {
          notify(error.message);
          statement.disabled = false;
        }
      });
      files.append(statement);
    }
  }
  target.append(files);
  const checklist = node('section', undefined, 'panel dossier-panel');
  checklist.append(
    node('h2', 'Checklist avant envoi'),
    node(
      'p',
      'Confirmez chaque contrôle avec une référence (page, pièce, signature, montant). Une non-applicabilité doit être justifiée. Ajoutez les exigences propres à cet appel d’offres.',
      'muted',
    ),
  );
  for (const item of data.checklist) {
    const form = node('form', undefined, 'dossier-check');
    form.dataset.code = item.code;
    form.append(node('h3', item.label + (item.required ? ' · Obligatoire' : ' · Facultatif')));
    const status = dossierField(form, 'Résultat du contrôle', 'select');
    for (const [value, label] of [
      ['pending', 'À vérifier'],
      ['validated', 'Vérifié'],
      ['waived', 'Non applicable — justification requise'],
    ])
      dossierOption(status, value, label);
    status.value = item.status;
    const note = dossierField(form, 'Justification / éléments vérifiés', 'textarea');
    note.value = item.note;
    note.maxLength = 2000;
    note.required = status.value !== 'pending';
    status.addEventListener('change', () => (note.required = status.value !== 'pending'));
    if (item.checked_at)
      form.append(
        node('p', 'Contrôlé le ' + new Date(item.checked_at).toLocaleString('fr-FR'), 'muted'),
      );
    if (data.editable)
      dossierSubmit(form, 'Enregistrer ce contrôle', async () => {
        await api(
          endpoint + `/checklist/${item.id}`,
          {
            method: 'PATCH',
            body: JSON.stringify({
              expected_revision: data.revision,
              status: status.value,
              note: note.value,
            }),
          },
          false,
        );
      });
    else {
      status.disabled = true;
      note.readOnly = true;
    }
    checklist.append(form);
  }
  if (data.editable) {
    const extra = node('form', undefined, 'dossier-form');
    extra.id = 'dossier-add-check';
    const label = dossierField(extra, 'Exigence spécifique à ce marché', 'text', true);
    label.maxLength = 300;
    const required = dossierField(extra, 'Contrôle obligatoire', 'checkbox');
    required.checked = true;
    dossierSubmit(extra, 'Ajouter ce contrôle', async () => {
      await api(
        endpoint + '/checklist',
        {
          method: 'POST',
          body: JSON.stringify({
            expected_revision: data.revision,
            label: label.value,
            required: required.checked,
          }),
        },
        false,
      );
    });
    checklist.append(extra);
  }
  target.append(checklist);
  const final = node('section', undefined, 'panel dossier-panel');
  final.append(
    node('h2', 'Validation finale et archives'),
    node(
      'p',
      'L’archive interne contient les pièces de travail et le compte rendu. Pour l’acheteur, utilisez la version finale sélectionnée et les seules pièces demandées.',
      'muted',
    ),
  );
  if (data.editable) {
    const form = node('form', undefined, 'dossier-form');
    form.id = 'dossier-approve';
    const artifact = dossierField(form, 'Version du dossier préparé à valider', 'select', true);
    dossierOption(artifact, '', 'Sélectionner la version finale');
    for (const doc of current.filter((d) => d.kind === 'prepared'))
      dossierOption(artifact, doc.id, doc.title + ' · v' + doc.version);
    const note = dossierField(form, 'Conclusion du contrôle final', 'textarea', true);
    note.maxLength = 2000;
    dossierSubmit(form, 'Valider le contrôle final du dossier', async () => {
      await api(
        endpoint + '/approve',
        {
          method: 'POST',
          body: JSON.stringify({
            expected_revision: data.revision,
            prepared_document_id: Number(artifact.value),
            note: note.value,
          }),
        },
        false,
      );
    });
    if (!['preparing', 'ready'].includes(data.stage)) {
      form.querySelector('button').disabled = true;
      form.append(
        node(
          'p',
          'Passez à la préparation après examen et décision Bid pour valider le dossier final.',
          'muted',
        ),
      );
    }
    final.append(form);
  }
  for (const approval of data.approvals) {
    const history = node('article', undefined, 'document-card');
    history.append(
      node('h3', 'Validation de la révision ' + approval.revision),
      node('p', new Date(approval.created_at).toLocaleString('fr-FR') + ' · ' + approval.note),
    );
    const download = node('button', 'Télécharger l’archive interne validée (ZIP)', 'secondary');
    download.addEventListener('click', () =>
      downloadPrivate(
        endpoint + `/approvals/${approval.id}/package`,
        `candidature-validation-${approval.id}.zip`,
      ).catch((e) => notify(e.message)),
    );
    history.append(download);
    final.append(history);
  }
  target.append(final);
}
bind('back-applications', 'click', () => switchView('applications'));
