import { defineConfig } from '@playwright/test'

/**
 * Browser verification (plan Task 7.1): the real Vite application on a fixed
 * localhost port with deterministic /api fixtures. Dev server by default;
 * `UI_PREVIEW=1` runs the production build instead. Server reuse is disabled
 * so a stale server can never mask changed code.
 */
const isPreview = process.env.UI_PREVIEW === '1'

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: [['list']],
  timeout: 60_000,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    locale: 'en-US',
    timezoneId: 'UTC',
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    command: isPreview
      ? 'pnpm preview --host 127.0.0.1 --port 4173 --strictPort'
      : 'E2E=1 pnpm dev --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
