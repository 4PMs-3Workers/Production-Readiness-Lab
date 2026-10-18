import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  globalTeardown: fileURLToPath(
    new URL('../../tests/frontend/api-e2e/global-teardown.ts', import.meta.url),
  ),
  testDir: fileURLToPath(new URL('../../tests/frontend/api-e2e', import.meta.url)),
  outputDir: fileURLToPath(new URL('../../test-results/frontend-api', import.meta.url)),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 45_000,
  reporter: [
    ['list'],
    [
      'html',
      {
        outputFolder: fileURLToPath(
          new URL('../../playwright-report/frontend-api', import.meta.url),
        ),
        open: 'never',
      },
    ],
  ],
  use: {
    baseURL: 'http://127.0.0.1:3101',
    trace: 'off',
    screenshot: 'off',
    timezoneId: 'America/Los_Angeles',
  },
  projects: [
    { name: 'api-desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'api-mobile', use: { ...devices['Pixel 7'] } },
  ],
  webServer: [
    {
      command: `${process.env.FRONTEND_E2E_PYTHON ?? 'python'} tools/scripts/frontend_e2e_backend.py serve`,
      cwd: fileURLToPath(new URL('../..', import.meta.url)),
      env: { PYTHONPATH: 'src/backend' },
      url: 'http://127.0.0.1:8101/health/live',
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: 'node ../../node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3101',
      cwd: fileURLToPath(new URL('.', import.meta.url)),
      url: 'http://127.0.0.1:3101/login',
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
