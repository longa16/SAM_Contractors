const { test, expect } = require('@playwright/test');
test('company expresses a wish and only sees application progress', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await page.getByRole('button', { name: 'Voir le dossier' }).click();
  await expect(page.locator('#screen')).toBeHidden();
  await expect(page.locator('#bid')).toBeHidden();
  await expect(page.locator('#company-discussion')).toBeHidden();
  await page.getByRole('button', { name: 'Exprimer mon intérêt', exact: true }).click();
  await expect(page.locator('.application-card .badge')).toHaveText('Demande en attente');
  await expect(page.locator('.application-validation')).toHaveCount(0);
  await expect(page.locator('.application-notes-form')).toHaveCount(0);
  await page.getByRole('button', { name: 'Profil entreprise', exact: false }).click();
  await expect(page.locator('#legal-name')).toHaveValue('Entreprise de test');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#applications-nav').click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.screenshot({ path: 'test-results/company-wish-mobile.png', fullPage: true });
  await page.locator('#logout').click();
  await expect(page.locator('#login-view')).toBeVisible();
  await expect(page.locator('#detail-content')).toBeEmpty();
  expect(errors).toEqual([]);
});

test('platform admin sees the SAM catalog without an active company', async ({ page }) => {
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#admin-view')).toBeVisible();
  await page.locator('#open-catalog').click();
  await expect(page.locator('#company-select')).toHaveValue('');
  await expect(page.locator('.card')).toHaveCount(1);
  await expect(page.locator('#recommendation')).toBeDisabled();
  await expect(page.locator('#sync-button')).toBeVisible();
  await page.getByRole('button', { name: 'Voir le dossier' }).click();
  await expect(page.locator('#detail-content')).toContainText('Catalogue SAM');
  await expect(page.locator('#screen')).toBeHidden();
  await expect(page.locator('#company-discussion')).toBeHidden();
  await page.locator('#close-detail').click();
  await page.locator('#company-select').selectOption({ label: 'Entreprise de test' });
  await expect(page.locator('#recommendation')).toBeEnabled();
  await page.getByRole('button', { name: 'Voir le dossier' }).click();
  await expect(page.locator('#screen')).toBeVisible();
  await expect(page.locator('#company-discussion')).toBeVisible();
  await page.locator('#close-detail').click();
  await page.locator('#company-select').selectOption('');
  await expect(page.locator('#recommendation')).toHaveValue('');
  await expect(page.locator('.card')).toHaveCount(1);
});

test('the catalog explains a failed SAM synchronization', async ({ page }) => {
  await page.route('**/api/sync/status*', (route) =>
    route.fulfill({
      json: {
        last_success: null,
        last_run_status: 'failed',
        stages: {},
        analysis_errors: 0,
        last_run_error: {
          reason: 'rate_limited',
          http_status: 429,
          message: 'Quota ou limite de débit SAM.gov atteint ; réessayez plus tard.',
        },
      },
    }),
  );
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#admin-view')).toBeVisible();
  await page.locator('#open-catalog').click();
  await expect(page.locator('#sync-state')).toContainText('Dernière tentative échouée');
  await expect(page.locator('#sync-state')).toContainText('Quota');
  await expect(page.locator('#sync-state')).toContainText('HTTP 429');
});

test('initial loading shows progress then becomes a manual incremental action', async ({
  page,
}) => {
  let checks = 0;
  await page.route('**/api/sync/status*', (route) => {
    const completed = ++checks > 2;
    return route.fulfill({
      json: {
        last_success: completed ? '2026-10-18T10:00:00Z' : null,
        last_run_status: completed ? 'success' : 'running',
        initial_sync: { completed, completed_days: completed ? 365 : 7, next_date: '2026-10-11' },
        stages: {},
        analysis_errors: 0,
      },
    });
  });
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#admin-view')).toBeVisible();
  await page.locator('#open-catalog').click();
  await expect(page.locator('#sync-state')).toContainText('7/365 jours');
  await expect(page.locator('#sync-button')).toBeDisabled();
  await expect(page.locator('#sync-button')).toHaveText(
    'Récupérer les nouvelles offres et les mises à jour',
    { timeout: 10000 },
  );
  await expect(page.locator('#sync-button')).toBeEnabled();
});

test('session survives reload, logout clears it, and revoked tokens require login', async ({
  page,
}) => {
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#admin-view')).toBeVisible();
  await page.locator('#open-catalog').click();
  await expect(page.locator('.card')).toHaveCount(1);
  await page.reload();
  await expect(page.locator('#workspace')).toBeVisible();
  await expect(page.locator('#identity')).toHaveText('admin@example.test');
  await expect(page.locator('.card')).toHaveCount(1);
  await page.locator('#logout').click();
  await page.reload();
  await expect(page.locator('#login-view')).toBeVisible();
  await expect(page.locator('#workspace')).toBeHidden();
  await page.evaluate(() => sessionStorage.setItem('sam-session-token', 'invalid-token'));
  await page.reload();
  await expect(page.locator('#login-view')).toBeVisible();
  await expect(page.locator('#workspace')).toBeHidden();
  expect(await page.evaluate(() => sessionStorage.getItem('sam-session-token'))).toBeNull();
});

test('company workspace has a private statement that survives saving and reload', async ({
  page,
}) => {
  await page.goto('/');
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#workspace')).toHaveClass('company-space');
  await expect(page.locator('#company-control')).toBeHidden();
  await expect(page.locator('#admin-nav')).toBeHidden();
  await expect(page.locator('#contractors-nav')).toBeHidden();
  await expect(page.locator('#sync-button')).toBeHidden();
  await page.locator('#statement-nav').click();
  await expect(page.locator('#stmt-description')).toBeVisible();
  await page
    .locator('#stmt-description')
    .fill('Une équipe de maintenance au service des bâtiments publics.');
  await page
    .locator('#stmt-achievements')
    .fill('2025 : rénovation de 12 installations, livrée dans les délais.');
  await page.locator('#stmt-contact_email').fill('contact@example.test');
  await page.getByRole('button', { name: 'Enregistrer le Company Statement' }).click();
  await expect(page.locator('#statement-saved')).toContainText('Présentation enregistrée');
  await page.reload();
  await page.locator('#statement-nav').click();
  await expect(page.locator('#stmt-achievements')).toHaveValue(
    '2025 : rénovation de 12 installations, livrée dans les délais.',
  );
  await expect(page.locator('#statement-preview')).toContainText('12 installations');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.screenshot({ path: 'test-results/company-statement-mobile.png', fullPage: true });
});

test('admin reviews contractor dossiers without contractor-only tabs', async ({ page }) => {
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#workspace')).toHaveClass('admin-space');
  await expect(page.locator('#admin-view')).toBeVisible();
  await expect(page.locator('#company-portfolio')).toContainText('Entreprise de test');
  await expect(page.locator('#statement-nav')).toBeHidden();
  await expect(page.locator('#profile-nav')).toBeHidden();
  await expect(page.locator('#contractors-nav')).toBeVisible();
  await page.getByRole('button', { name: 'Contrôler le dossier entreprise' }).click();
  await expect(page.locator('#contractors-view')).toBeVisible();
  await expect(page.locator('#contractor-review')).toContainText('Entreprise de test');
  await expect(page.locator('#contractor-review input')).toHaveCount(0);
  await page.screenshot({ path: 'test-results/admin-statement-desktop.png', fullPage: true });
});

test('company can use a PDF instead of the statement form and download it privately', async ({
  page,
}) => {
  const path = require('path');
  await page.goto('/');
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await page.locator('#statement-nav').click();
  await expect(page.locator('#statement-method-form')).toBeChecked();
  await page.locator('#statement-method-pdf').check();
  await expect(page.locator('#statement-form-panel')).toBeHidden();
  await page
    .locator('#statement-pdf-file')
    .setInputFiles(path.join(__dirname, '../../tests/fixtures/company-statement.pdf'));
  await page.getByRole('button', { name: 'Enregistrer ce PDF', exact: true }).click();
  await expect(page.locator('#statement-active-mode')).toContainText('document PDF');
  await page.reload();
  await page.locator('#statement-nav').click();
  await expect(page.locator('#statement-method-pdf')).toBeChecked();
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download-statement-pdf').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('company-statement.pdf');
  await page.locator('#statement-method-form').check();
  await expect(page.locator('#stmt-description')).toHaveValue(
    'Une équipe de maintenance au service des bâtiments publics.',
  );
  await page.getByRole('button', { name: 'Enregistrer le Company Statement' }).click();
  await expect(page.locator('#statement-active-mode')).toContainText('fiche remplie');
  await page.locator('#statement-method-pdf').check();
  await page.locator('#activate-statement-pdf').click();
  await expect(page.locator('#statement-active-mode')).toContainText('document PDF');
  await page.locator('#delete-statement-pdf').click();
  await expect(page.locator('#statement-form-panel')).toBeVisible();
});

test('admin validates each step while company sees read-only progress', async ({
  page,
  browser,
}) => {
  await page.goto('/');
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await page.locator('#applications-nav').click();
  await expect(page.locator('.application-card .badge')).toHaveText('Demande en attente');
  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await admin.goto('/');
  await admin.locator('#email').fill('admin@example.test');
  await admin.locator('#password').fill('browser-admin-password-123');
  await admin.getByRole('button', { name: 'Se connecter' }).click();
  await admin.locator('#admin-applications').click();
  await expect(admin.locator('.application-card')).toContainText('Entreprise de test');
  await admin.locator('.application-notes-form textarea').fill('PRIVATE internal assessment');
  await admin.getByRole('button', { name: 'Enregistrer les notes internes', exact: true }).click();
  await admin
    .locator('.application-validation textarea')
    .fill('Nous commençons l’examen de votre marché.');
  await admin
    .getByRole('button', { name: 'Accepter la demande et commencer l’examen', exact: true })
    .click();
  await expect(admin.locator('.application-card .badge')).toHaveText('Examen en cours');
  await expect(
    admin.getByRole('button', { name: 'Valider l’examen et passer à la préparation', exact: true }),
  ).toBeDisabled();
  await admin.getByRole('button', { name: 'Examiner le dossier', exact: true }).click();
  await admin.locator('#screen').click();
  await expect(admin.locator('#bid')).toBeVisible();
  await admin.locator('#bid').click();
  await admin.locator('#close-detail').click();
  await admin.locator('#refresh-applications').click();
  for (const [button, summary, stage] of [
    [
      'Valider l’examen et passer à la préparation',
      'Examen terminé : votre entreprise est éligible.',
      'Préparation du dossier',
    ],
    [
      'Valider le dossier prêt pour envoi',
      'Le dossier a été préparé et vérifié.',
      'Prêt pour envoi',
    ],
  ]) {
    if (stage === 'Prêt pour envoi') await completeDossierInBrowser(admin);
    await admin.locator('.application-validation textarea').fill(summary);
    await admin.getByRole('button', { name: button, exact: true }).click();
    await expect(admin.locator('.application-card .badge')).toHaveText(stage);
  }
  await admin
    .locator('.application-validation textarea')
    .fill('Le dossier a été transmis sur le portail acheteur.');
  await admin
    .getByLabel('Référence de l’envoi effectué (accusé, portail ou email)')
    .fill('PRIVATE receipt ABC123');
  await admin
    .getByRole('button', { name: 'Confirmer l’envoi et passer au suivi', exact: true })
    .click();
  await expect(admin.locator('.application-card .badge')).toHaveText('Envoyé · En suivi');
  await page.locator('#refresh-applications').click();
  await expect(page.locator('.application-card .badge')).toHaveText('Envoyé · En suivi');
  await expect(page.locator('.application-history')).toContainText('Le dossier a été transmis');
  await expect(page.locator('#application-list')).not.toContainText('PRIVATE');
  await expect(page.locator('.application-validation')).toHaveCount(0);
  await admin
    .locator('.application-validation textarea')
    .fill('L’acheteur a attribué ce marché à votre entreprise.');
  await admin.locator('.application-validation select').selectOption('won');
  await admin.getByRole('button', { name: 'Publier le résultat', exact: true }).click();
  await page.locator('#refresh-applications').click();
  await expect(page.locator('.application-card .badge')).toHaveText('Marché gagné');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.screenshot({ path: 'test-results/application-progress-mobile.png', fullPage: true });
  await adminContext.close();
});

test('contractor completes onboarding and admin reviews its private documents', async ({
  page,
  browser,
}) => {
  const path = require('path');
  await page.goto('/');
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#admin-nav')).toBeHidden();
  await expect(page.locator('#contractors-nav')).toBeHidden();
  await page.locator('#profile-nav').click();
  await expect(page.locator('#legal-name')).toHaveValue('Entreprise de test');
  await page.locator('#company-address').fill('120 Test Street');
  await page.locator('#company-city').fill('Richmond');
  await page.locator('#company-uei').fill('TEST12345678');
  await page.locator('#company-service_categories').fill('Maintenance, Plumbing');
  await page.locator('#op-employee_count').fill('18');
  await page.locator('#op-availability').selectOption('limited');
  await page.locator('#op-available_from').fill('2027-01-10');
  await page.locator('#op-workers_comp_applicable').selectOption('true');
  await page.locator('#op-licenses_applicable').selectOption('true');
  await page
    .getByRole('button', { name: 'Ajouter : Licences professionnelles', exact: true })
    .click();
  await page.locator('#profile-licenses [data-property="name"]').fill('Trade license');
  await page.locator('#profile-licenses [data-property="issuer"]').fill('Test authority');
  await page.locator('#profile-licenses [data-property="expires_on"]').fill('2027-12-31');
  await page.getByRole('button', { name: 'Ajouter : Tarifs', exact: true }).click();
  await page.locator('#profile-rates [data-property="service"]').fill('Maintenance');
  await page.locator('#profile-rates [data-property="amount_usd"]').fill('42.5');
  await page.locator('#save-profile').click();
  await expect(page.locator('#notice')).toContainText('Profil enregistré');
  await page.reload();
  await page.locator('#profile-nav').click();
  await expect(page.locator('#company-city')).toHaveValue('Richmond');
  await expect(page.locator('#profile-rates [data-property="amount_usd"]')).toHaveValue('42.5');
  const upload = page.locator('.document-upload-form');
  await upload.getByLabel('Type de justificatif').selectOption('w9');
  await upload
    .getByLabel('Intitulé (précisez la licence ou certification concernée)')
    .fill('W-9 signé — Test');
  await upload
    .getByLabel('Justificatif PDF')
    .setInputFiles(path.join(__dirname, '../../tests/fixtures/company-statement.pdf'));
  await upload.getByRole('button', { name: 'Déposer le justificatif' }).click();
  await expect(page.locator('.document-card')).toContainText('À contrôler');
  await expect(page.locator('.document-review-form')).toHaveCount(0);
  await page.locator('#statement-nav').click();
  await page
    .locator('#government-experience')
    .fill('Maintenance for municipal buildings since 2022.');
  await page.getByRole('button', { name: 'Ajouter : Références de projets', exact: true }).click();
  await page.locator('#statement-references [data-property="customer"]').fill('Test City');
  await page.locator('#statement-references [data-property="project"]').fill('Civic facilities');
  await page.locator('#statement-references [data-property="government"]').check();
  await page.getByRole('button', { name: 'Enregistrer les références et l’expérience' }).click();
  await expect(page.locator('#experience-status')).toContainText('enregistrées');
  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await admin.goto('/');
  await admin.locator('#email').fill('admin@example.test');
  await admin.locator('#password').fill('browser-admin-password-123');
  await admin.getByRole('button', { name: 'Se connecter' }).click();
  await expect(admin.locator('#profile-nav')).toBeHidden();
  await expect(admin.locator('#statement-nav')).toBeHidden();
  await admin.getByRole('button', { name: 'Contrôler le dossier entreprise' }).click();
  await expect(admin.locator('#contractor-review')).toContainText('Richmond');
  await expect(admin.locator('#contractor-review')).toContainText('Test City');
  await expect(admin.locator('.document-upload-form')).toHaveCount(0);
  await admin.locator('.document-review-form textarea').fill('Pièce vérifiée pour ce test.');
  await admin.getByRole('button', { name: 'Valider le contrôle' }).click();
  await expect(admin.locator('.document-card')).toContainText('Accepté par l’admin');
  await page.locator('#profile-nav').click();
  await expect(page.locator('.document-card')).toContainText('Accepté par l’admin');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.screenshot({ path: 'test-results/contractor-onboarding-mobile.png', fullPage: true });
  await adminContext.close();
});

async function completeDossierInBrowser(admin) {
  const path = require('path');
  await admin
    .getByRole('button', { name: 'Ouvrir le dossier de candidature', exact: true })
    .click();
  await expect(admin.locator('#dossier-view')).toBeVisible();
  await expect(admin.locator('#dossier-upload')).toBeVisible();
  let revision = Number(await admin.locator('#dossier-content').getAttribute('data-revision'));
  await admin.locator('#dossier-upload').getByLabel('Catégorie').selectOption('prepared');
  await admin
    .locator('#dossier-upload')
    .getByLabel('Titre du document')
    .fill('Dossier final — test');
  await admin
    .locator('#dossier-upload input[type="file"]')
    .setInputFiles(path.join(__dirname, '../../tests/fixtures/company-statement.pdf'));
  await admin.getByRole('button', { name: 'Enregistrer le document', exact: true }).click();
  await expect(admin.locator('#dossier-content')).toHaveAttribute(
    'data-revision',
    String(++revision),
  );
  const codes = await admin
    .locator('.dossier-check')
    .evaluateAll((forms) => forms.map((form) => form.dataset.code));
  for (const code of codes) {
    const form = admin.locator(`.dossier-check[data-code="${code}"]`);
    await form
      .getByLabel('Résultat du contrôle')
      .selectOption(code === 'contractor_documents' ? 'waived' : 'validated');
    await form
      .getByLabel('Justification / éléments vérifiés')
      .fill(
        code === 'contractor_documents'
          ? 'La consultation de test ne demande pas de justificatif séparé.'
          : 'Exigences vérifiées page 2, signatures et montants contrôlés.',
      );
    await form.getByRole('button', { name: 'Enregistrer ce contrôle', exact: true }).click();
    await expect(admin.locator('#dossier-content')).toHaveAttribute(
      'data-revision',
      String(++revision),
    );
  }
  await admin
    .locator('#dossier-approve select')
    .selectOption({ label: 'Dossier final — test · v1' });
  await admin
    .locator('#dossier-approve textarea')
    .fill('Contrôle final terminé, dossier conforme à la consultation de test.');
  await admin
    .getByRole('button', { name: 'Valider le contrôle final du dossier', exact: true })
    .click();
  await expect(admin.locator('#dossier-content')).toHaveAttribute(
    'data-revision',
    String(++revision),
  );
  await expect(admin.locator('#dossier-content')).toContainText('Contrôle final validé');
  const downloadPromise = admin.waitForEvent('download');
  await admin
    .getByRole('button', { name: 'Télécharger l’archive interne validée (ZIP)', exact: true })
    .click();
  expect((await downloadPromise).suggestedFilename()).toMatch(/\.zip$/);
  await admin.screenshot({ path: 'test-results/dossier-final-review-desktop.png', fullPage: true });
  await admin.locator('#back-applications').click();
  await expect(admin.locator('.application-validation')).toBeVisible();
}

test('admin invites a member, manages access and member chooses and resets passwords', async ({
  page,
  browser,
}) => {
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await page.locator('#company-select').selectOption({ label: 'Entreprise de test' });
  await page.locator('#users-nav').click();
  await page.locator('#new-email').fill('invited-browser@example.test');
  await page.locator('#new-role').selectOption('ops_manager');
  await page.getByRole('button', { name: 'Envoyer l’invitation', exact: true }).click();
  await expect(page.locator('#mail-mode-info')).toContainText('aucun email réel');
  const invitedMail = page
    .locator('.mail-card')
    .filter({ hasText: 'invited-browser@example.test' })
    .first();
  await expect(invitedMail).toContainText('Test local');
  const invitationLink = await invitedMail
    .getByRole('link', { name: 'Ouvrir le lien de test' })
    .getAttribute('href');
  const memberContext = await browser.newContext();
  const member = await memberContext.newPage();
  await member.goto(invitationLink);
  await expect(member.locator('#access-view')).toBeVisible();
  expect(new URL(member.url()).hash).toBe('');
  await member.locator('#access-password').fill('invited-browser-password-123');
  await member.locator('#access-confirm').fill('invited-browser-password-123');
  await member.getByRole('button', { name: 'Enregistrer mon mot de passe', exact: true }).click();
  await expect(member.locator('#login-view')).toBeVisible();
  await member.locator('#email').fill('invited-browser@example.test');
  await member.locator('#password').fill('invited-browser-password-123');
  await member.getByRole('button', { name: 'Se connecter' }).click();
  await expect(member.locator('#users-nav')).toBeHidden();
  await expect(member.locator('#identity')).toHaveText('invited-browser@example.test');
  await page.locator('#users-nav').click();
  const card = page.locator('.member-card').filter({ hasText: 'invited-browser@example.test' });
  await card.locator('.member-edit input[type="checkbox"]').uncheck();
  await card.getByRole('button', { name: 'Enregistrer les accès', exact: true }).click();
  await expect(card).toContainText('Accès désactivé');
  await member.reload();
  await expect(member.locator('#login-view')).toBeVisible();
  await card.locator('.member-edit input[type="checkbox"]').check();
  await card.getByRole('button', { name: 'Enregistrer les accès', exact: true }).click();
  await expect(card).toContainText('Accès actif');
  await member.locator('#email').fill('invited-browser@example.test');
  await member.locator('#password').fill('invited-browser-password-123');
  await member.getByRole('button', { name: 'Se connecter' }).click();
  await member.locator('#account-nav').click();
  await member.locator('#current-password').fill('invited-browser-password-123');
  await member.locator('#new-account-password').fill('changed-browser-password-123');
  await member.locator('#confirm-account-password').fill('changed-browser-password-123');
  await member.getByRole('button', { name: 'Modifier le mot de passe', exact: true }).click();
  await expect(member.locator('#login-view')).toBeVisible();
  await member.locator('#forgot-password').click();
  await member.locator('#forgot-email').fill('invited-browser@example.test');
  await member.getByRole('button', { name: 'Demander un lien', exact: true }).click();
  await expect(member.locator('#forgot-result')).toContainText('Si un compte actif');
  await page.locator('#users-nav').click();
  const resetMail = page
    .locator('.mail-card')
    .filter({ hasText: 'invited-browser@example.test' })
    .filter({ hasText: 'Réinitialiser' })
    .first();
  await expect(resetMail.getByRole('link')).toBeVisible();
  await member.goto(await resetMail.getByRole('link').getAttribute('href'));
  await member.locator('#access-password').fill('reset-browser-password-123');
  await member.locator('#access-confirm').fill('reset-browser-password-123');
  await member.getByRole('button', { name: 'Enregistrer mon mot de passe', exact: true }).click();
  await expect(member.locator('#login-view')).toBeVisible();
  await member.locator('#email').fill('invited-browser@example.test');
  await member.locator('#password').fill('reset-browser-password-123');
  await member.getByRole('button', { name: 'Se connecter' }).click();
  await expect(member.locator('#workspace')).toBeVisible();
  await page.screenshot({ path: 'test-results/member-management-desktop.png', fullPage: true });
  await memberContext.close();
});

test('dashboards show outcomes and role-scoped history with CSV exports', async ({
  page,
  browser,
}) => {
  await page.goto('/');
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await page.locator('#reports-nav').click();
  await expect(page.locator('#report-metrics')).toContainText('100 %');
  await expect(page.locator('#report-history')).toContainText('Marché gagné');
  await expect(page.locator('#report-history')).not.toContainText('PRIVATE');
  await expect(page.locator('#report-companies-panel')).toBeHidden();
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#export-applications').click();
  expect((await downloadPromise).suggestedFilename()).toBe('candidatures.csv');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.screenshot({ path: 'test-results/reports-contractor-mobile.png', fullPage: true });
  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await admin.goto('/');
  await admin.locator('#email').fill('admin@example.test');
  await admin.locator('#password').fill('browser-admin-password-123');
  await admin.getByRole('button', { name: 'Se connecter' }).click();
  await admin.locator('#reports-nav').click();
  await expect(admin.locator('#report-companies-panel')).toBeVisible();
  await expect(admin.locator('#report-history-title')).toHaveText(
    'Journal des actions de la plateforme',
  );
  await admin.locator('#report-end').fill('2020-01-01');
  await admin.getByRole('button', { name: 'Actualiser les résultats', exact: true }).click();
  await expect(admin.locator('#report-history')).toContainText('Aucun événement');
  await adminContext.close();
});

test('a delayed admin report cannot populate a new contractor session', async ({ page }) => {
  let release;
  const waiting = new Promise((resolve) => {
    release = resolve;
  });
  let ready;
  const captured = new Promise((resolve) => {
    ready = resolve;
  });
  let first = true;
  await page.route('**/api/reports/history*', async (route) => {
    if (!first) {
      await route.continue();
      return;
    }
    first = false;
    ready();
    await waiting;
    await route.fulfill({
      json: {
        page: 1,
        has_next: false,
        items: [
          {
            id: 1,
            date: '2026-10-20T10:00:00Z',
            action: 'PRIVATE ADMIN EVENT',
            entity_type: 'user',
            entity_id: '1',
            actor: '1',
            after: { note: 'PRIVATE ADMIN DATA' },
          },
        ],
      },
    });
  });
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await page.locator('#company-select').selectOption({ label: 'Entreprise de test' });
  await page.locator('#reports-nav').click();
  await captured;
  await page.locator('#logout').click();
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('#workspace')).toBeVisible();
  release();
  await page.locator('#reports-nav').click();
  await expect(page.locator('#report-history')).toContainText('Marché gagné');
  await expect(page.locator('#report-history')).not.toContainText('PRIVATE ADMIN');
  await expect(page.locator('#report-companies-panel')).toBeHidden();
});

async function prepareWorkflowCompany(request, name, email) {
  const login = await request.post('/api/auth/login', {
    data: { email: 'admin@example.test', password: 'browser-admin-password-123' },
  });
  const headers = { Authorization: 'Bearer ' + (await login.json()).access_token };
  const profile = (await (await request.get('/api/companies/1/profile', { headers })).json())
    .profile;
  const response = await request.post('/api/companies', { headers, data: { name, profile } });
  expect(response.status()).toBe(201);
  const company = await response.json();
  const member = await request.post('/api/users', {
    headers,
    data: {
      email,
      password: 'workflow-browser-password-123',
      role: 'business_developer',
      company_id: company.id,
    },
  });
  expect(member.status()).toBe(201);
  return company;
}

async function loginWorkflowPage(page, email, password) {
  await page.goto('/');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click();
  await expect(page.locator('#workspace')).toBeVisible();
}

test('admin explicitly refuses a contractor request and contractor sees the reason', async ({
  page,
  browser,
  request,
}) => {
  const company = await prepareWorkflowCompany(
    request,
    'Contractor demande refusée',
    'refusal-browser@example.test',
  );
  await loginWorkflowPage(page, 'refusal-browser@example.test', 'workflow-browser-password-123');
  await page.getByRole('button', { name: 'Voir le dossier →', exact: true }).click();
  await page.getByRole('button', { name: 'Exprimer mon intérêt', exact: true }).click();
  await expect(page.locator('.application-card .badge')).toHaveText('Demande en attente');
  const context = await browser.newContext();
  const admin = await context.newPage();
  await loginWorkflowPage(admin, 'admin@example.test', 'browser-admin-password-123');
  await admin.locator('#company-select').selectOption(String(company.id));
  await admin.locator('#applications-nav').click();
  await expect(
    admin.getByRole('button', { name: 'Accepter la demande et commencer l’examen', exact: true }),
  ).toBeEnabled();
  await admin
    .locator('.application-validation textarea')
    .fill('Nous ne pouvons pas prendre en charge cette demande dans les délais.');
  await admin.getByRole('button', { name: 'Refuser la demande', exact: true }).click();
  await expect(admin.locator('.application-card .badge')).toHaveText('Candidature refusée');
  await expect(admin.locator('.application-validation')).toHaveCount(0);
  await page.locator('#refresh-applications').click();
  await expect(page.locator('.application-card .badge')).toHaveText('Candidature refusée');
  await expect(page.locator('.application-history')).toContainText(
    'Nous ne pouvons pas prendre en charge',
  );
  await expect(page.locator('.application-validation')).toHaveCount(0);
  await page.reload();
  await page.locator('#applications-nav').click();
  await expect(page.locator('.application-card .badge')).toHaveText('Candidature refusée');
  await context.close();
});

test('admin initiates a candidature without a request and No-Bid closes its tracking', async ({
  page,
  browser,
  request,
}) => {
  const company = await prepareWorkflowCompany(
    request,
    'Contractor initiative admin',
    'initiated-browser@example.test',
  );
  await loginWorkflowPage(page, 'admin@example.test', 'browser-admin-password-123');
  await page.locator('#open-catalog').click();
  await page.locator('#company-select').selectOption(String(company.id));
  await page.getByRole('button', { name: 'Voir le dossier →', exact: true }).click();
  await page.getByRole('button', { name: 'Initier une candidature', exact: true }).click();
  await expect(page.locator('.application-card .badge')).toHaveText('Examen en cours');
  await expect(page.locator('.application-card')).toContainText(
    'Candidature initiée par votre accompagnateur',
  );
  await expect(page.getByRole('button', { name: 'Refuser la demande', exact: true })).toHaveCount(
    0,
  );
  const context = await browser.newContext();
  const member = await context.newPage();
  await loginWorkflowPage(
    member,
    'initiated-browser@example.test',
    'workflow-browser-password-123',
  );
  await member.locator('#applications-nav').click();
  await expect(member.locator('.application-card .badge')).toHaveText('Examen en cours');
  await expect(member.locator('.application-history')).toContainText('a sélectionné cette offre');
  await page.getByRole('button', { name: 'Examiner le dossier', exact: true }).click();
  await page.locator('#screen').click();
  await page.locator('#no-bid').click();
  await page
    .locator('#reason')
    .fill('Les conditions de ce marché ne conviennent pas à votre entreprise.');
  await page.locator('#reason-form button').click();
  await page.locator('#close-detail').click();
  await page.locator('#refresh-applications').click();
  await expect(page.locator('.application-card .badge')).toHaveText('Candidature refusée');
  await member.locator('#refresh-applications').click();
  await expect(member.locator('.application-card .badge')).toHaveText('Candidature refusée');
  await expect(member.locator('.application-history')).toContainText('Les conditions de ce marché');
  await expect(member.locator('.application-validation')).toHaveCount(0);
  await member.locator('#reports-nav').click();
  await expect(member.locator('.pipeline-bars')).toContainText('Candidature refusée');
  await context.close();
});

test('admin runs Hermes examination and reviews its sources before a separate Bid decision', async ({
  page,
  request,
}) => {
  const company = await prepareWorkflowCompany(
    request,
    'Contractor examen Hermes',
    'hermes-browser@example.test',
  );
  await loginWorkflowPage(page, 'admin@example.test', 'browser-admin-password-123');
  await page.locator('#open-catalog').click();
  await page.locator('#company-select').selectOption(String(company.id));
  await page.getByRole('button', { name: 'Voir le dossier →', exact: true }).click();
  await page.getByRole('button', { name: 'Initier une candidature', exact: true }).click();
  await page.getByRole('button', { name: 'Ouvrir le dossier de candidature', exact: true }).click();
  await expect(page.locator('#hermes-launch')).toBeEnabled();
  await page.locator('#hermes-launch').click();
  await expect(page.locator('.hermes-run')).toContainText('Rapport disponible', { timeout: 12000 });
  await expect(page.locator('.hermes-run')).toContainText('Examen d’une consultation fictive.');
  await page
    .locator('.hermes-run summary')
    .filter({ hasText: 'Exigences de l’appel d’offres' })
    .click();
  await expect(page.locator('.hermes-run blockquote')).toHaveText('Technical services required.');
  await expect(page.locator('.hermes-run')).toContainText('Avis SAM et description');
  const token = await page.evaluate(() => sessionStorage.getItem('sam-session-token'));
  const headers = { Authorization: 'Bearer ' + token };
  const path = `/api/opportunities/browser-opportunity?company_id=${company.id}`;
  expect((await (await request.get(path, { headers })).json()).status).toBe('new');
  await page
    .locator('.hermes-review-form textarea')
    .fill('Sources contrôlées et conclusions relues par l’admin.');
  await page.getByRole('button', { name: 'Valider le rapport d’examen', exact: true }).click();
  await expect(page.locator('.hermes-run')).toContainText('Rapport validé par l’admin');
  expect((await (await request.get(path, { headers })).json()).status).toBe('screening');
  await page.screenshot({ path: 'test-results/hermes-review-desktop.png', fullPage: true });
  await page.locator('#back-applications').click();
  await expect(page.locator('.application-card .badge')).toHaveText('Examen en cours');
  await page.getByRole('button', { name: 'Examiner le dossier', exact: true }).click();
  await expect(page.locator('#bid')).toBeVisible();
  await page.locator('#bid').click();
  await page.locator('#close-detail').click();
  await page.locator('#refresh-applications').click();
  await expect(
    page.getByRole('button', { name: 'Valider l’examen et passer à la préparation', exact: true }),
  ).toBeEnabled();
});
