import { expect, test, type Page } from '@playwright/test'

const operatorToken = process.env.CONNECTOR_TEST_OPERATOR_TOKEN
const initialProjectId = process.env.CONNECTOR_TEST_PROJECT_ID

function requireOperatorSetup(): { token: string; projectId: string } {
  if (!operatorToken || !initialProjectId) {
    throw new Error('The real-stack connector test requires its disposable API fixture.')
  }
  return { token: operatorToken, projectId: initialProjectId }
}

async function openConnectorCreation(page: Page, name: string): Promise<void> {
  const addAnother = page.getByRole('button', { name: `Add another ${name}` })
  if (await addAnother.count()) {
    await addAnother.click()
    return
  }
  await page.getByRole('button', { name: `Configure ${name}` }).click()
}

test('operator configures shared search and chooses project defaults independently', async ({ page, request }) => {
  const { token, projectId } = requireOperatorSetup()
  const headers = { Authorization: `Bearer ${token}` }
  let simulatedLostResponse = false
  let creates = 0
  let statusReads = 0

  await page.addInitScript((accessToken: string) => localStorage.setItem('access_token', accessToken), token)
  await page.route('**/api/global-connectors', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.continue()
      return
    }
    creates += 1
    const submittedKey = route.request().postDataJSON().api_key as string
    const committed = await route.fetch()
    expect(committed.status()).toBe(201)
    expect((await committed.body()).includes(Buffer.from(submittedKey))).toBe(false)
    if (!simulatedLostResponse) {
      simulatedLostResponse = true
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'connector_unavailable', message: 'Connector operation failed' } }),
      })
      return
    }
    await route.fulfill({ response: committed })
  })
  await page.route('**/api/global-connectors/requests/**', async (route) => {
    statusReads += 1
    const response = await route.fetch()
    const responseBody = await response.body()
    expect(responseBody.includes(Buffer.from('sample-brave'))).toBe(false)
    await route.fulfill({ response })
  })

  await page.goto('/connectors')
  await expect(page.getByRole('heading', { name: 'Search connectors' })).toBeVisible()

  await openConnectorCreation(page, 'Brave Search')
  let dialog = page.getByRole('dialog')
  await dialog.getByLabel('Connector name').fill('Shared Brave from browser')
  const braveKey = dialog.getByLabel('Brave Search API key')
  await braveKey.fill('sample-brave')
  await dialog.getByRole('button', { name: 'Add global connector' }).click()
  await expect(braveKey).toHaveValue('')
  await expect.poll(() => statusReads).toBeGreaterThan(0)
  await expect(dialog.getByRole('status')).toHaveCount(0)
  expect(creates).toBe(1)
  await dialog.getByRole('button', { name: 'Close connector details' }).click()
  await expect(page.getByRole('button', { name: 'Configure Shared Brave from browser' })).toBeVisible()

  await openConnectorCreation(page, 'Tavily Search')
  dialog = page.getByRole('dialog')
  await dialog.getByLabel('Connector name').fill('Shared Tavily from browser')
  const tavilyKey = dialog.getByLabel('Tavily Search API key')
  await tavilyKey.fill('sample-tavily')
  const tavilyWrite = page.waitForResponse((response) =>
    response.url().includes('/api/global-connectors') &&
    response.request().method() === 'POST' &&
    response.status() === 201,
  )
  await dialog.getByRole('button', { name: 'Add global connector' }).click()
  await expect(tavilyKey).toHaveValue('')
  await tavilyWrite
  await expect(dialog.getByRole('status')).toHaveCount(0)
  expect(creates).toBe(2)
  await dialog.getByRole('button', { name: 'Close connector details' }).click()
  await expect(page.getByRole('button', { name: 'Configure Shared Tavily from browser' })).toBeVisible()

  await page.getByTestId('project-selector-trigger').click()
  await page.getByTestId('create-project-trigger').click()
  const createProject = page.getByRole('dialog', { name: 'Create project' })
  await createProject.getByLabel('New project name').fill('Shared Search Peer')
  await createProject.getByRole('button', { name: 'Create project' }).click()
  await expect(page.getByTestId('project-selector-trigger')).toContainText('Shared Search Peer')

  const projectsResponse = await request.get('/api/projects', { headers })
  expect(projectsResponse.status()).toBe(200)
  const projects = await projectsResponse.json() as Array<{ id: string; name: string }>
  const peerProject = projects.find((project) => project.name === 'Shared Search Peer')
  expect(peerProject).toBeDefined()
  expect(peerProject?.id).not.toBe(projectId)

  const globalResponse = await request.get('/api/global-connectors', { headers })
  expect(globalResponse.status()).toBe(200)
  const globalConnectors = await globalResponse.json() as Array<{ id: string; kind: string; name: string; visibility: string }>
  const brave = globalConnectors.find((connector) => connector.name === 'Shared Brave from browser')
  const tavily = globalConnectors.find((connector) => connector.name === 'Shared Tavily from browser')
  expect(brave?.visibility).toBe('global')
  expect(tavily?.visibility).toBe('global')
  expect(brave?.kind).toBe('brave')
  expect(tavily?.kind).toBe('tavily')

  await page.goto(`/projects/${projectId}/settings`)
  await expect(page.getByRole('heading', { name: 'Project Settings' })).toBeVisible()
  await expect(page.getByText('Choose a Global connector to replace this legacy Project default. Existing project access remains unchanged until you select one.')).toBeVisible()
  await page.getByLabel('Search default').selectOption(tavily!.id)
  await expect(page.getByLabel('Search default')).toHaveValue(tavily!.id)

  await page.getByTestId('project-selector-trigger').click()
  await page.getByRole('menuitemradio', { name: 'Shared Search Peer' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${peerProject!.id}/settings$`))
  await expect(page.getByRole('heading', { name: 'Project Settings' })).toBeVisible()
  await expect(page.getByLabel('Search default')).toHaveValue('')
  await page.getByLabel('Search default').selectOption(brave!.id)
  await expect(page.getByLabel('Search default')).toHaveValue(brave!.id)

  await page.getByTestId('project-selector-trigger').click()
  await page.getByRole('menuitemradio', { name: projects.find((project) => project.id === projectId)!.name }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/settings$`))
  await expect(page.getByLabel('Search default')).toHaveValue(tavily!.id)

  for (const [project, selectedService, selectedConnector] of [
    [projectId, 'tavily', tavily!.id],
    [peerProject!.id, 'brave', brave!.id],
  ] as const) {
    for (const service of ['brave', 'tavily']) {
      const saved = await request.get(`/api/projects/${project}/connector-defaults/${service}`, { headers })
      expect(saved.status()).toBe(200)
      const body = await saved.json() as { connector_id: string; revision: number }
      expect(body.connector_id).toBe(service === selectedService ? selectedConnector : null)
      expect(body.revision).toBeGreaterThan(0)
    }
  }

  await page.goto('/connectors')
  await expect(page.locator('[data-kind="brave"][data-visibility="global"]')).toContainText('Global')
  await expect(page.locator('[data-kind="tavily"][data-visibility="global"]')).toContainText('Global')
  await expect(page.locator('[data-kind="brave"][data-visibility="global"]')).toContainText('Configured')
  await expect(page.locator('[data-kind="tavily"][data-visibility="global"]')).toContainText('Configured')
  await expect(page.getByRole('heading', { name: 'Legacy Project connectors' })).toBeVisible()
  expect(await page.locator('body').innerText()).not.toContain('sample-brave')
  expect(await page.locator('body').innerText()).not.toContain('sample-tavily')
})

test('a definite origin rejection clears the key without polling or resubmission', async ({ page }) => {
  const { token } = requireOperatorSetup()
  let creates = 0
  let statusReads = 0
  await page.addInitScript((accessToken: string) => localStorage.setItem('access_token', accessToken), token)
  await page.route('**/api/global-connectors', async (route) => {
    if (route.request().method() === 'POST') {
      creates += 1
      const submittedKey = route.request().postDataJSON().api_key as string
      const rejection = await route.fetch({
        headers: { ...route.request().headers(), origin: 'https://untrusted.example' },
      })
      expect(rejection.status()).toBe(403)
      expect((await rejection.body()).includes(Buffer.from(submittedKey))).toBe(false)
      await route.fulfill({ response: rejection })
      return
    }
    await route.continue()
  })
  await page.route('**/api/global-connectors/requests/**', async (route) => {
    statusReads += 1
    await route.continue()
  })

  await page.goto('/connectors')
  await openConnectorCreation(page, 'Tavily Search')
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Connector name').fill('Rejected origin test')
  const key = dialog.getByLabel('Tavily Search API key')
  await key.fill('sample-rejected')
  await dialog.getByRole('button', { name: 'Add global connector' }).click()
  await expect(key).toHaveValue('')
  await expect(dialog.getByRole('status')).toContainText('This browser origin is not allowed to configure connectors')
  await expect(dialog.getByTestId('check-connector-request')).toHaveCount(0)
  expect(creates).toBe(1)
  expect(statusReads).toBe(0)
  expect(await page.locator('body').innerText()).not.toContain('sample-rejected')
})

test('unprovisioned connector setup explains a missing HMAC file and blocks writes', async ({ page }) => {
  const { token } = requireOperatorSetup()
  let creates = 0
  await page.addInitScript((accessToken: string) => localStorage.setItem('access_token', accessToken), token)
  await page.route('**/api/global-connectors', async (route) => {
    if (route.request().method() === 'POST') creates += 1
    await route.continue()
  })

  await page.goto('/connectors')
  await expect(page.getByText(/idempotency HMAC key file is missing or invalid/)).toBeVisible()
  await page.getByRole('button', { name: 'Configure Shared Brave from browser' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Rotate API key' })).toBeDisabled()
  expect(creates).toBe(0)
})
