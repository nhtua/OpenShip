import fs from 'node:fs'
import path from 'node:path'
import { test, expect } from '@playwright/test'
import { clearApiRoutes, mockApi, setAuthToken } from './fixtures/api'

/**
 * Screenshot capture (plan Task 7.7). These write PNGs to the (gitignored)
 * SDD workspace for a human/browser-capable review BEFORE any baseline is
 * accepted. There are no committed baselines and no automatic snapshot
 * approval here — exploratory captures stay outside tracked source.
 */

const OUT = path.resolve(
  __dirname,
  '../../../../.superpowers/sdd/2026-10-03-ui-improvement/screenshots',
)

test.beforeAll(() => {
  fs.mkdirSync(OUT, { recursive: true })
})

/** Deterministic rendering: no animation/transition motion, fonts loaded. */
async function stabilize(page: import('@playwright/test').Page): Promise<void> {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  // Only inject the style tag once per page to avoid DOM accumulation.
  const injected = await page.evaluate(() =>
    document.getElementById('__pw-no-transition__') !== null,
  )
  if (!injected) {
    await page.addStyleTag({
      content:
        '*, ::before, ::after { animation: none !important; transition: none !important; }',
    })
  }
  await page.waitForFunction(() => document.fonts.check('16px "primeicons"'))
}

async function shot(name: string, page: import('@playwright/test').Page): Promise<void> {
  const file = path.join(OUT, `${name}.png`)
  await page.screenshot({ path: file, fullPage: false })
  expect(fs.existsSync(file), `${name}.png written for review`).toBe(true)
}

test('captures login and register (dark)', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.goto('/login')
  await stabilize(page)
  await shot('01-login-dark', page)

  await page.goto('/register')
  await stabilize(page)
  await shot('02-register-dark', page)
})

test('captures empty chat (dark)', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page, { conversations: [], messages: {} })
  await setAuthToken(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByText('No conversations yet').waitFor()
  await stabilize(page)
  await shot('03-chat-empty-dark', page)
})

test('captures populated chat (dark)', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByText('Staging deployed successfully.').waitFor()
  await stabilize(page)
  await shot('04-chat-populated-dark', page)
})

test('captures error state (dark)', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page, { chatStatus: 500 })
  await setAuthToken(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  const composer = page.getByLabel('Message')
  await composer.fill('Trigger failure')
  await composer.press('Enter')
  await page.getByRole('alert').waitFor()
  await stabilize(page)
  await shot('05-chat-error-dark', page)
})

test('captures collapsed sidebar (dark)', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByText('Staging deployed successfully.').waitFor()
  await page.getByRole('button', { name: 'Toggle Sidebar' }).click()
  await page
    .getByRole('button', { name: 'Expand sidebar' })
    .waitFor({ state: 'visible' })
  await stabilize(page)
  await shot('06-sidebar-collapsed-dark', page)
})

test('captures mobile drawer (dark)', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.locator('[data-testid="stream"]').waitFor()
  await page.getByRole('button', { name: 'Toggle Sidebar' }).click()
  await page.locator('[data-mobile="true"]').waitFor({ state: 'visible' })
  await stabilize(page)
  await shot('07-mobile-drawer-dark', page)
})

test('captures populated chat (light)', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByText('Staging deployed successfully.').waitFor()
  await page.getByRole('button', { name: 'Switch to light mode' }).click()
  await stabilize(page)
  await shot('08-chat-populated-light', page)
})
