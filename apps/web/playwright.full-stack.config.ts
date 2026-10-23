import { defineConfig } from '@playwright/test'

const externalApp = process.env.OPENSHIP_FULL_STACK_URL
const localWebURL = 'http://127.0.0.1:4173'

export default defineConfig({
  testDir: './tests/full-stack',
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: [['list']],
  timeout: 90_000,
  use: {
    baseURL: externalApp || localWebURL,
    locale: 'en-US',
    timezoneId: 'UTC',
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  ...(externalApp
    ? {}
    : {
        webServer: {
          command: 'pnpm dev --host 127.0.0.1 --port 4173 --strictPort',
          url: localWebURL,
          reuseExistingServer: false,
          timeout: 180_000,
          env: {
            VITE_API_PROXY_TARGET:
              process.env.OPENSHIP_FULL_STACK_API_URL || 'http://127.0.0.1:8000',
          },
        },
      }),
})
