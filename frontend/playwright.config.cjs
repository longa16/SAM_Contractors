const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './e2e',
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8765',
    viewport: { width: 1440, height: 1000 },
    launchOptions: process.env.CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.CHROMIUM_EXECUTABLE }
      : {},
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: '../.venv/bin/python ../tests/e2e/server.py',
    url: 'http://127.0.0.1:8765/healthz',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
