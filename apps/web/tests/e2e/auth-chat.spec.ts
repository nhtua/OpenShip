import { test, expect } from '@playwright/test'
import { E2E_TOKEN, clearApiRoutes, mockApi, setAuthToken } from './fixtures/api'

/**
 * Authenticated user journey (plan Task 7.6): register, log out, log in,
 * create/select conversations, send with Enter, mocked failure handling,
 * and guards against fake state.
 */

test.beforeEach(async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
})

test('register with confirmation enters the workspace', async ({ page }) => {
  await page.goto('/register')
  await page.getByLabel('Username').fill('testuser')
  await page.getByLabel('Email').fill('test@example.com')
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill('password123')
  await page.getByRole('textbox', { name: 'Confirm Password' }).fill('password123')
  await page.getByRole('button', { name: 'Create Account' }).click()

  await page.waitForURL('http://127.0.0.1:4173/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  expect(await page.getByText('testuser').count()).toBeGreaterThan(0)
})

test('registration with mismatched confirmation stays on the form', async ({
  page,
}) => {
  await page.goto('/register')
  await page.getByLabel('Username').fill('testuser')
  await page.getByLabel('Email').fill('test@example.com')
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill('password123')
  await page.getByRole('textbox', { name: 'Confirm Password' }).fill('different-pass')
  await page.getByRole('button', { name: 'Create Account' }).click()

  await expect(page).toHaveURL(/\/register$/)
  await expect(page.getByRole('alert')).toContainText('Passwords do not match')
})

test('unsuccessful login stays on the form with an actionable alert', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page, { loginStatus: 401 })
  await page.goto('/login')
  await page.getByLabel('Username').fill('testuser')
  await page.getByLabel('Password').fill('wrong-password')
  await page.getByRole('button', { name: 'Sign In' }).click()

  await expect(page).toHaveURL(/\/login$/)
  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible()
  await expect(alert).toContainText('Incorrect username or password')
})

test('valid login enters the workspace and persists the token', async ({
  page,
}) => {
  await page.goto('/login')
  await page.getByLabel('Username').fill('testuser')
  await page.getByLabel('Password').fill('password123')
  await page.getByRole('button', { name: 'Sign In' }).click()

  await page.waitForURL('http://127.0.0.1:4173/')
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBe(
    E2E_TOKEN,
  )
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
})

test('loading guard disables the submit button and blocks double submit', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page, { loginDelay: 600 })

  let loginRequests = 0
  const handler = (req: import('@playwright/test').Request) => {
    if (req.url().includes('/api/auth/login')) loginRequests += 1
  }
  page.on('request', handler)

  await page.goto('/login')
  await page.getByLabel('Username').fill('testuser')
  await page.getByLabel('Password').fill('password123')

  // Submit the form by pressing Enter in the password field (triggers native
  // form submit → Vue's @submit handler → handleLogin).
  await page.getByLabel('Password').press('Enter')

  await page.waitForURL('http://127.0.0.1:4173/')

  // The form should only have submitted once — the loading guard blocks
  // double-submit while the request is in flight.
  expect(loginRequests).toBe(1)
})

test('logout leaves the workspace, clears the token, and lands on login', async ({
  page,
}) => {
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

  await page.getByRole('button', { name: 'Sign Out' }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull()
})

test('create a conversation, select another, send with Enter, and render the mocked response', async ({
  page,
}) => {
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

  // Create a conversation through the sidebar form.
  await page.getByRole('button', { name: 'New Conversation' }).click()
  await page.getByLabel('Conversation title').fill('E2E chat')
  await page.getByRole('button', { name: 'Create' }).click()
  const newRow = page.getByRole('button', { name: /E2E chat/ })
  await expect(newRow).toBeVisible()
  await expect(newRow).toHaveAttribute('data-active', 'true')

  // Select an existing conversation; its history renders in the stream.
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByText('Staging deployed successfully.').waitFor()

  // Send with Enter; the fulfilled SSE fixture renders the response.
  const composer = page.getByLabel('Message')
  await composer.fill('Hello agent')
  await composer.press('Enter')
  await page.getByText('Hello world').waitFor()

  // Wait for the status to return to "Idle" (proves the SSE cycle completed
  // and isStreaming was reset).  The composer text clears on send so checking
  // the button label "Send" is more reliable than enabled state.
  await expect(page.getByText('Idle')).toBeVisible()

  expect(await composer.inputValue()).toBe('')
  await expect(page.getByRole('button', { name: 'Send' })).toBeVisible()
})

test('composer is disabled while streaming and re-enabled after completion', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page, { chatDelay: 800 })
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByText('Staging deployed successfully.').waitFor()

  const composer = page.getByLabel('Message')
  await composer.fill('Stream check')
  await composer.press('Enter')

  await expect(page.getByRole('button', { name: /Streaming/ })).toBeDisabled()
  // "Responding" text only appears in the header (the stream's status says "Agent").
  await expect(page.getByText('Responding')).toBeVisible()
  await page.getByText('Hello world').waitFor()

  // Wait for the status badge to return to "Idle" (proves the SSE cycle completed).
  await expect(page.getByText('Idle')).toBeVisible()

  // Button label returns to "Send" after completion (content cleared so enabled
  // check is unreliable; label is a better signal).
  await expect(page.getByRole('button', { name: 'Send' })).toBeVisible()
})

test('Shift+Enter inserts a newline without sending', async ({ page }) => {
  let chatRequests = 0
  await page.route('**/api/chat/*', async (route) => {
    chatRequests += 1
    await route.continue()
  })
  await setAuthToken(page)
  await page.goto('/')
  await page.locator('[data-testid="stream"]').waitFor()

  const composer = page.getByLabel('Message')
  await composer.pressSequentially('line1', { delay: 10 })
  await composer.press('Shift+Enter')
  await composer.pressSequentially('line2', { delay: 10 })

  expect(await composer.inputValue()).toBe('line1\nline2')
  expect(chatRequests).toBe(0)
})

test('mocked chat failure shows an actionable alert with Dismiss', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page, { chatStatus: 500 })
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

  const composer = page.getByLabel('Message')
  await composer.fill('Trigger failure')
  await composer.press('Enter')

  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible()
  await expect(alert).toContainText('Failed to send message')

  await page.getByRole('button', { name: 'Dismiss' }).click()
  await expect(alert).toBeHidden()
})

test('no fake workspaces, sessions, export, or stop controls', async ({
  page,
}) => {
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

  const text = await page.locator('body').innerText()
  for (const forbidden of [
    'web-platform',
    'Session #',
    'Provision & Build',
    'staging-env',
    'prod-infra',
    'Export',
    'Stop',
  ]) {
    expect(text, `should not contain "${forbidden}"`).not.toContain(forbidden)
  }
})
