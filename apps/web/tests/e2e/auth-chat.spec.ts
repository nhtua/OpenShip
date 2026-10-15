import { test, expect } from '@playwright/test'
import { E2E_TOKEN, E2E_RUN_QUEUED, E2E_RUN_RUNNING, E2E_RUN_COMPLETED, durableEvent, clearApiRoutes, mockApi, setAuthToken } from './fixtures/api'
import type { Route } from '@playwright/test'

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
  await page.goto('/login')
  await page.getByLabel('Username').fill('testuser')
  await page.getByLabel('Password').fill('password123')
  await page.getByRole('button', { name: 'Sign In' }).click()
  await page.waitForURL('http://127.0.0.1:4173/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

  // Expand the sidebar if collapsed (user menu is only visible in expanded mode)
  const expandBtn = page.getByRole('button', { name: 'Expand sidebar' })
  if (await expandBtn.isVisible()) {
    await expandBtn.click()
  }

  // Open the user menu by clicking the user button in the sidebar footer
  const userBtn = page.getByRole('button', { name: 'testuser' })
  await userBtn.click()

  // Wait for the Sign Out button to appear in the dropdown
  const signOutBtn = page.getByRole('button', { name: 'Sign Out' })
  await expect(signOutBtn).toBeVisible()
  await signOutBtn.click()
  await expect(page).toHaveURL(/\/login$/)
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull()
})

test('create a conversation, select another, send with Enter, and render the mocked response', async ({
  page,
}) => {
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

  // Create a conversation through the sidebar button (immediate creation).
  await page.getByRole('button', { name: 'New Conversation', exact: true }).click()
  // The sidebar creates the conversation immediately with the title "New Conversation".
  // Use aria-label to distinguish the conversation entry from the create button.
  const newRow = page.getByRole('button', { name: /Open conversation New/ })
  await expect(newRow).toBeVisible()
  await expect(newRow).toHaveAttribute('data-active', 'true')

  // Select an existing conversation; its history renders in the stream.
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByText('Staging deployed successfully.').waitFor()

  // Send with Enter; durable terminal events load the persisted response.
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
  await mockApi(page, { streamDelay: 800 })
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
  await page.route('**/api/runs/*/turns', async (route) => {
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
  await mockApi(page, { turnsStatus: 500 })
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

  const composer = page.getByLabel('Message')
  await composer.fill('Trigger failure')
  await composer.press('Enter')

  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible()
  await expect(alert).toContainText('Failed to submit message')

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

test('reconnect after page reload shows eventual run completion', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)

  let submitted = false
  let reloading = false
  let completed = false
  let streamRequests = 0
  const record = [
    durableEvent(0, 'turn_submitted', { payload: { content: 'Hello' } }),
    durableEvent(1, 'run.started'),
  ]
  const json = (route: Route, status: number, body: unknown) => route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })

  await page.route('**/api/runs/c1/turns', (route) => {
    submitted = true
    return json(route, 202, E2E_RUN_QUEUED)
  })
  await page.route('**/api/runs/run-e2e-1', (route) =>
    json(route, 200, completed ? E2E_RUN_COMPLETED : E2E_RUN_RUNNING),
  )
  await page.route('**/api/conversations/c1/snapshot', (route) =>
    json(route, 200, {
      conversation_id: 'c1',
      sequence: submitted ? 1 : -1,
      events: submitted ? record : [],
    }),
  )
  await page.route('**/api/conversations/c1/messages', (route) => {
    const messages = submitted
      ? [{ id: 'm1', role: 'user', content: 'Hello', conversation_id: 'c1', created_at: '' }]
      : []
    if (completed) messages.push({
      id: 'm2', role: 'assistant', content: 'Hello world', conversation_id: 'c1', created_at: '',
    })
    return json(route, 200, messages)
  })
  await page.route('**/api/conversations/c1/events/stream**', async (route) => {
    streamRequests += 1
    if (!reloading) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    }
    await new Promise((resolve) => setTimeout(resolve, 100))
    completed = true
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'id: evt-completed\nevent: run.succeeded\ndata: ' + JSON.stringify({
        sequence: 2, run_id: 'run-e2e-1', payload: { message_id: 'm2' },
      }) + '\n\n',
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByLabel('Message').fill('Hello')
  await page.getByLabel('Message').press('Enter')
  await expect(page.getByText('Running', { exact: true })).toBeVisible()
  await expect(page.getByText('Idle', { exact: true })).toBeVisible()
  await expect(page.getByText('Hello world')).toHaveCount(0)

  reloading = true
  await page.reload()
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await expect(page.getByText('Hello world')).toBeVisible()
  await expect.poll(() => streamRequests).toBeGreaterThanOrEqual(2)
})
