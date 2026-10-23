import { expect, test } from '@playwright/test'
import { clearApiRoutes, mockApi, mockDurableApi, setAuthToken } from './fixtures/api'

test('an uncertain connector write clears the key, checks status, and never resends it', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)

  const rows: Record<string, unknown>[] = []
  const writes: Record<string, unknown>[] = []
  const idempotencyKeys: string[] = []
  let statusReads = 0

  await page.route('**/api/connectors/setup-context', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ can_manage_global: true, credential_store_ready: true, unavailable_reason: null }),
  }))
  await page.route('**/api/projects/project-e2e/connectors', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
      return
    }
  })
  await page.route('**/api/global-connectors', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) })
      return
    }
    writes.push(route.request().postDataJSON() as Record<string, unknown>)
    idempotencyKeys.push(route.request().headers()['idempotency-key'] ?? '')
    // Model a server that may have committed before the response was lost.
    rows.push({ id: 'created-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave', status: 'configured', revision: 1, is_default: false })
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'connector_unavailable', message: 'Connector operation failed' } }) })
  })
  await page.route('**/api/global-connectors/requests/*', async (route) => {
    statusReads += 1
    const status = statusReads === 1 ? 'processing' : 'complete'
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(status === 'processing'
        ? { status }
        : { status, result: { id: 'created-brave', name: 'Project Brave' } }),
    })
  })

  await page.goto('/connectors')
  await expect(page.getByRole('heading', { name: 'Search connectors' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Global search connectors' })).toBeVisible()
  const card = page.getByRole('button', { name: 'Configure Brave Search' })
  await card.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByLabel('Connector name').fill('Shared Brave')
  const key = page.getByLabel('Brave Search API key')
  await key.fill('sample')
  await dialog.getByRole('button', { name: 'Add global connector' }).click()

  await expect(key).toHaveValue('')
  await expect(page.locator('[data-testid="connector-credential-form"] [role="status"]')).toContainText('still processing')
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0].api_key).toBe('sample')
  expect(writes[0].visibility).toBeUndefined()
  expect(writes[0].kind).toBe('brave')
  expect(idempotencyKeys[0]).toMatch(/^[0-9a-f-]{36}$/i)

  await page.getByTestId('check-connector-request').first().click()
  await expect.poll(() => statusReads).toBe(2)
  await expect(page.getByText('Shared Brave', { exact: true })).toBeVisible()
  expect(writes).toHaveLength(1)
  expect(await page.locator('body').innerText()).not.toContain('sample')
})

test('the operator sees global instance controls only after setup context allows management', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)
  await page.route('**/api/connectors/setup-context', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ can_manage_global: true, credential_store_ready: true, unavailable_reason: null }),
  }))
  await page.route('**/api/global-connectors', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([
      { id: 'global-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave', status: 'configured', revision: 2, is_default: false },
    ]),
  }))

  await page.goto('/connectors')

  await expect(page.getByRole('heading', { name: 'Global search connectors' })).toBeVisible()
  await page.getByRole('button', { name: 'Configure Shared Brave' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Revoke' })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Check status' })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Add another Brave Search' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Add another Brave Search' })).toHaveCount(1)
  await expect(dialog.getByText('Shared by eligible projects.')).toBeVisible()
})

test('connector cards open by keyboard and return focus when the responsive dialog closes', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto('/connectors')

  const card = page.getByRole('button', { name: 'Configure Shared Brave' })
  await card.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('installation operator')
  await expect(dialog.locator('input[type="password"]')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(card).toBeFocused()
})

test('direct search submits a selected legacy Project connector under its original visibility', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await page.route('**/api/projects/project-e2e/connectors', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([
      { id: 'global-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave', status: 'configured', revision: 1, is_default: true },
      { id: 'project-brave', kind: 'brave', visibility: 'project', name: 'Project Brave', status: 'configured', revision: 1, is_default: false },
      { id: 'project-tavily', kind: 'tavily', visibility: 'project', name: 'Project Tavily', status: 'configured', revision: 1, is_default: true },
    ]),
  }))
  const asked = await mockDurableApi(page, {
    toolsStatus: 202,
    submittedToolRun: { id: 'run-selected-connector', conversation_id: 'c1', status: 'queued', created_at: '', attempt: 0, kind: 'tool', graph_version: 2 },
  })
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  await page.getByRole('button', { name: 'Add tool' }).click()
  await page.getByTestId('tool-choice').filter({ hasText: 'Brave Search' }).click()
  await page.getByTestId('connector-selection').selectOption('project-brave')
  await page.getByLabel('Message').fill('search using the project connector')
  await page.getByRole('button', { name: 'Search', exact: true }).click()

  await expect.poll(() => asked.submittedTools.length).toBe(1)
  expect(asked.submittedTools[0].payload.connector_selections).toEqual({ brave: 'project-brave' })
})
