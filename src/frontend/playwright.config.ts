import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  testDir: fileURLToPath(new URL('../../tests/frontend/e2e', import.meta.url)),
  outputDir: fileURLToPath(new URL('../../test-results/frontend', import.meta.url)),
  forbidOnly: Boolean(process.env.CI),
  failOnFlakyTests: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI
    ? [
        [
          fileURLToPath(new URL('../../tests/frontend/safe-reporter.ts', import.meta.url)),
          {
            outputFile: fileURLToPath(
              new URL('../../playwright-report/frontend-safe/summary.json', import.meta.url),
            ),
          },
        ],
      ]
    : [
        ['list'],
        [
          'html',
          {
            outputFolder: fileURLToPath(
              new URL('../../playwright-report/frontend', import.meta.url),
            ),
            open: 'never',
          },
        ],
      ],
  use: {
    baseURL: 'http://127.0.0.1:3100',
    trace: process.env.CI ? 'off' : 'retain-on-failure',
    screenshot: process.env.CI ? 'off' : 'only-on-failure',
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-chromium', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: 'node ../../node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3100',
    cwd: fileURLToPath(new URL('.', import.meta.url)),
    url: 'http://127.0.0.1:3100/login',
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: process.env.CI ? 'ignore' : 'pipe',
  },
});
