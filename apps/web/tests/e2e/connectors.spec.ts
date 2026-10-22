import { expect, test } from '@playwright/test'
import { clearApiRoutes, mockApi, mockDurableApi, setAuthToken } from './fixtures/api'

test('an uncertain connector write clears the key, checks status, and never resends it', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)

  const rows = [
    { id: 'global-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave', status: 'configured', revision: 1, is_default: true },
    { id: 'project-tavily', kind: 'tavily', visibility: 'project', name: 'Project Tavily', status: 'configured', revision: 1, is_default: true },
  ]
  const writes: Record<string, unknown>[] = []
  const idempotencyKeys: string[] = []
  let statusReads = 0

  await page.route('**/api/projects/project-e2e/connectors', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) })
      return
    }
    writes.push(route.request().postDataJSON() as Record<string, unknown>)
    idempotencyKeys.push(route.request().headers()['idempotency-key'] ?? '')
    // Model a server that may have committed before the response was lost.
    rows.push({ id: 'created-brave', kind: 'brave', visibility: 'project', name: 'Project Brave', status: 'configured', revision: 1, is_default: false })
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'connector_unavailable', message: 'Connector operation failed' } }) })
  })
  await page.route('**/api/projects/project-e2e/connector-requests/*', async (route) => {
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

  await page.goto('/settings/connectors')
  await expect(page.getByRole('heading', { name: 'Search connectors' })).toBeVisible()
  await expect(page.getByText('Global connector management')).toHaveCount(0)
  await page.getByLabel('Brave Search project connector name').fill('Project Brave')
  const key = page.getByLabel('Brave Search API key')
  await key.fill('synthetic-connector-key-sentinel')
  await page.getByRole('button', { name: 'Add project connector' }).first().click()

  await expect(key).toHaveValue('')
  await expect(page.locator('[data-testid="connector-credential-form"] [role="status"]')).toContainText('still processing')
  await expect.poll(() => writes.length).toBe(1)
  expect(writes[0].api_key).toBe('synthetic-connector-key-sentinel')
  expect(idempotencyKeys[0]).toMatch(/^[0-9a-f-]{36}$/i)

  await page.getByTestId('check-connector-request').first().click()
  await expect.poll(() => statusReads).toBe(2)
  await expect(page.getByText('Project Brave', { exact: true })).toBeVisible()
  expect(writes).toHaveLength(1)
  expect(await page.locator('body').innerText()).not.toContain('synthetic-connector-key-sentinel')
})

test('the global management panel appears only when the protected global list allows it', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await setAuthToken(page)
  await page.route('**/api/global-connectors', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([
      { id: 'global-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave', status: 'configured', revision: 2, is_default: false },
    ]),
  }))

  await page.goto('/settings/connectors')

  await expect(page.getByRole('heading', { name: 'Global connector management' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Revoke globally' })).toBeVisible()
  await expect(page.getByText('every project using it')).toBeVisible()
})

test('direct search submits the selected project connector instead of silently switching visibility', async ({ page }) => {
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
