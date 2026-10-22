import { defineConfig, devices } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const baseURL = process.env.PRL_COMPOSE_BASE_URL;
if (!baseURL) throw new Error('PRL_COMPOSE_BASE_URL must point at the Compose gateway.');

export default defineConfig({
  testDir: fileURLToPath(new URL('../../tests/frontend/compose', import.meta.url)),
  outputDir: fileURLToPath(
    new URL('../../build/deploy/compose-playwright/results', import.meta.url),
  ),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  forbidOnly: true,
  reporter: 'list',
  use: {
    baseURL,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [{ name: 'compose-chromium', use: { ...devices['Desktop Chrome'] } }],
});
