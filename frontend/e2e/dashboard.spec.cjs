const { test, expect } = require('@playwright/test');
test('a client reviews an opportunity and decides Bid in its private workspace', async ({
  page,
}) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await page.locator('#email').fill('reviewer@example.test');
  await page.locator('#password').fill('browser-test-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
  await expect(page.locator('.card')).toHaveCount(1);
  await page.getByRole('button', { name: 'Voir le dossier' }).click();
  await page.getByRole('button', { name: 'Analyser pour mon entreprise' }).click();
  await expect(page.locator('#bid')).toBeVisible();
  await page.locator('#bid').click();
  await expect(page.getByText('Statut : bid', { exact: true })).toBeVisible();
  await page.locator('#comment').fill('Vérification du dossier par notre équipe.');
  await page.getByRole('button', { name: 'Publier le commentaire' }).click();
  await expect(page.locator('.comment')).toContainText('Vérification du dossier');
  await page.locator('#close-detail').click();
  await expect(page.locator('.card')).toContainText('Bid validé');
  await page.screenshot({ path: 'test-results/dashboard-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Profil entreprise', exact: false }).click();
  await expect(page.locator('#legal-name')).toHaveValue('Entreprise de test');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Opportunités', exact: false }).click();
  await expect(page.locator('.card')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
  await page.screenshot({ path: 'test-results/dashboard-mobile.png', fullPage: true });
  await page.locator('#logout').click();
  await expect(page.locator('#login-view')).toBeVisible();
  await expect(page.locator('#detail-content')).toBeEmpty();
  await expect(page.locator('#comments')).toBeEmpty();
  expect(errors).toEqual([]);
});

test('platform admin sees the SAM catalog without an active company', async ({ page }) => {
  await page.goto('/');
  await page.locator('#email').fill('admin@example.test');
  await page.locator('#password').fill('browser-admin-password-123');
  await page.getByRole('button', { name: 'Se connecter' }).click();
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
  await expect(page.locator('#sync-state')).toContainText('Dernière tentative échouée');
  await expect(page.locator('#sync-state')).toContainText('Quota');
  await expect(page.locator('#sync-state')).toContainText('HTTP 429');
});

test('initial loading shows progress then becomes a manual incremental action', async ({
  page,
}) => {
  let checks = 0;
  await page.route('**/api/sync/status*', (route) => {
    const completed = ++checks > 1;
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
