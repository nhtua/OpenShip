import { expect, test, type Page } from '@playwright/test'
import {
  E2E_CONVERSATIONS,
  E2E_PROFILES,
  clearApiRoutes,
  mockApi,
  mockDurableApi,
  setAuthToken,
} from '../e2e/fixtures/api'

const operatorToken = process.env.CONNECTOR_TEST_OPERATOR_TOKEN
const initialProjectId = process.env.CONNECTOR_TEST_PROJECT_ID

function requireOperatorSetup(): { token: string; projectId: string } {
  if (!operatorToken || !initialProjectId) {
    throw new Error('The real-stack connector test requires its disposable API fixture.')
  }
  return { token: operatorToken, projectId: initialProjectId }
}

async function openConnectorCreation(page: Page, name: string): Promise<void> {
  const createCard = page.getByRole('button', { name: `Configure ${name}`, exact: true })
  if (await createCard.count()) {
    await createCard.click()
    return
  }

  const kind = name === 'Brave Search' ? 'brave' : 'tavily'
  await page.locator(`[data-kind="${kind}"][data-visibility="global"] button`).first().click()
  await page.getByRole('dialog').getByRole('button', { name: `Add another ${name}` }).click()
}

test('installation operator changes built-in tool availability without a restart', async ({ page, request }) => {
  const { token } = requireOperatorSetup()
  const headers = { Authorization: `Bearer ${token}` }
  await page.addInitScript((accessToken: string) => localStorage.setItem('access_token', accessToken), token)
  await page.goto('/settings/global')
  await expect(page.getByRole('heading', { name: 'Built-in tool availability' })).toBeVisible()

  const initialResponse = await request.get('/api/global-tool-policy', { headers })
  expect(initialResponse.status()).toBe(200)
  const initialPolicy = await initialResponse.json() as { tools: Array<{ id: string; enabled: boolean }> }
  expect(initialPolicy.tools.find((tool) => tool.id === 'aws_read_catalog')?.enabled).toBe(false)
  expect(initialPolicy.tools.find((tool) => tool.id === 'aws_read')?.enabled).toBe(false)

  const awsReadToggle = page.getByTestId('tool-policy-toggle-aws_read')
  await expect(awsReadToggle).not.toBeChecked()
  const catalogEnabled = page.waitForResponse((response) =>
    response.url().includes('/api/global-tool-policy/aws_read_catalog') &&
    response.request().method() === 'PUT' && response.status() === 200,
  )
  const readerEnabled = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/api/global-tool-policy/aws_read') &&
    response.request().method() === 'PUT' && response.status() === 200,
  )
  await awsReadToggle.check()
  await Promise.all([catalogEnabled, readerEnabled])
  await expect(awsReadToggle).toBeChecked()
  const enabledResponse = await request.get('/api/global-tool-policy', { headers })
  expect(enabledResponse.status()).toBe(200)
  const enabledPolicy = await enabledResponse.json() as { tools: Array<{ id: string; enabled: boolean }> }
  expect(enabledPolicy.tools.find((tool) => tool.id === 'aws_read_catalog')?.enabled).toBe(true)
  expect(enabledPolicy.tools.find((tool) => tool.id === 'aws_read')?.enabled).toBe(true)

  const catalogDisabled = page.waitForResponse((response) =>
    response.url().includes('/api/global-tool-policy/aws_read_catalog') &&
    response.request().method() === 'PUT' && response.status() === 200,
  )
  const readerDisabled = page.waitForResponse((response) =>
    new URL(response.url()).pathname.endsWith('/api/global-tool-policy/aws_read') &&
    response.request().method() === 'PUT' && response.status() === 200,
  )
  await awsReadToggle.uncheck()
  await Promise.all([catalogDisabled, readerDisabled])
  await expect(awsReadToggle).not.toBeChecked()
  const disabledResponse = await request.get('/api/global-tool-policy', { headers })
  expect(disabledResponse.status()).toBe(200)
  const disabledPolicy = await disabledResponse.json() as { tools: Array<{ id: string; enabled: boolean }> }
  expect(disabledPolicy.tools.find((tool) => tool.id === 'aws_read_catalog')?.enabled).toBe(false)
  expect(disabledPolicy.tools.find((tool) => tool.id === 'aws_read')?.enabled).toBe(false)
})

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
  await expect(page.getByRole('heading', { name: 'Global search connectors', exact: true })).toBeVisible()

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
  const searchDefault = page.getByRole('combobox', { name: 'Search default' })
  await searchDefault.selectOption(tavily!.id)
  await expect(searchDefault).toHaveValue(tavily!.id)

  await page.getByTestId('project-selector-trigger').click()
  await page.getByRole('menuitemradio', { name: 'Shared Search Peer' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${peerProject!.id}/settings$`))
  await expect(page.getByRole('heading', { name: 'Project Settings' })).toBeVisible()
  await expect(searchDefault).toHaveValue('')
  await searchDefault.selectOption(brave!.id)
  await expect(searchDefault).toHaveValue(brave!.id)

  await page.getByTestId('project-selector-trigger').click()
  await page.getByRole('menuitemradio', { name: projects.find((project) => project.id === projectId)!.name }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/settings$`))
  await expect(searchDefault).toHaveValue(tavily!.id)

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
  await dialog.getByRole('button', { name: /Add (global )?connector/ }).click()
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
  await page.locator('[data-kind="tavily"][data-visibility="global"] button').first().click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('button', { name: 'Save' })).toBeDisabled()
  expect(creates).toBe(0)
})

test('AWS connector reads use direct targets and automatic discovery without resource bindings', async ({ page }) => {
  const { token, projectId } = requireOperatorSetup()
  await clearApiRoutes(page)
  await mockApi(page, {
    conversations: E2E_CONVERSATIONS.map((conversation) => ({ ...conversation, project_id: projectId })),
  })
  await setAuthToken(page, token)

  const awsConnector = {
    id: 'aws-project-1', kind: 'aws', visibility: 'project', name: 'Production AWS',
    status: 'configured', revision: 1, is_default: false,
  }
  const configuration = {
    schema_version: 1, kind: 'aws', region: 'us-west-2',
    expected_account_id: '123456789012',
    basic_role_arn: 'arn:aws:iam::123456789012:role/OpenShipRead',
  }
  const projectConnectors: Record<string, unknown>[] = []
  let credentialRequest: Record<string, unknown> | null = null
  let resourceSetupRequests = 0

  await page.route('**/api/projects*', (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'GET' && url.pathname === '/api/projects') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: projectId, name: 'AWS fixture project', archived_at: null }]),
      })
    }
    return route.fallback()
  })
  await page.route(`**/api/projects/${projectId}/connectors`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(projectConnectors) })
      return
    }
    await route.continue()
  })
  await page.route(`**/api/projects/${projectId}/connectors/${awsConnector.id}`, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ...awsConnector, configuration }),
  }))
  await page.route(`**/api/projects/${projectId}/connector-defaults/*`, (route) => {
    const service = new URL(route.request().url()).pathname.split('/').at(-1)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ service, connector_id: null, revision: 0 }),
    })
  })
  await page.route(`**/api/projects/${projectId}/aws-connectors`, async (route) => {
    credentialRequest = route.request().postDataJSON() as Record<string, unknown>
    const { auth: _auth, ...safeConnector } = credentialRequest as Record<string, unknown> & { auth: unknown }
    projectConnectors.push(awsConnector)
    await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ...safeConnector, ...awsConnector }) })
  })
  await page.route(/\/api\/projects\/[^/]+\/(environments|resources)(\/|\?|$)/, async (route) => {
    resourceSetupRequests += 1
    await route.fulfill({ status: 410, contentType: 'application/json', body: JSON.stringify({ detail: 'retired' }) })
  })
  const storageCapability = {
    id: 'aws_s3_bucket_storage', version: '1.0.0', manifest_digest: 'c'.repeat(64),
    input_schema: {}, output_schema: {}, effect: 'read_only_external' as const,
    enabled: true, available: true, unavailable_reason: null,
  }
  const metadataCapability = {
    ...storageCapability,
    id: 'aws_s3_bucket_metadata', manifest_digest: 'd'.repeat(64),
  }
  const awsReadCatalogCapability = {
    ...storageCapability,
    id: 'aws_read_catalog', manifest_digest: 'e'.repeat(64),
    effect: 'read_only_local' as const,
  }
  const awsReadCapability = {
    ...storageCapability,
    id: 'aws_read', manifest_digest: 'f'.repeat(64),
  }
  const toolProfiles = {
    ...E2E_PROFILES,
    profiles: E2E_PROFILES.profiles.map((profile) => profile.profile_id === 'primary-model'
      ? { ...profile, capabilities: { tool_calls: true, parallel_tool_calls: false } }
      : profile),
  }
  const queued = await mockDurableApi(page, {
    capabilities: [storageCapability, metadataCapability, awsReadCatalogCapability, awsReadCapability],
    profiles: toolProfiles,
    submittedToolRun: {
      id: 'run-s3-observation', conversation_id: 'c1', status: 'completed', created_at: '', attempt: 0,
      kind: 'tool', graph_version: 2, model: null, usage: null, error: null, provider_attempts: [],
    },
  })
  await page.route(`**/api/projects/${projectId}/capabilities`, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([
      {
        ...storageCapability,
        permitted: true,
        required_connector_kinds: ['aws'],
        connector_readiness: { aws: 'ready' },
      },
      {
        ...metadataCapability,
        permitted: true,
        required_connector_kinds: ['aws'],
        connector_readiness: { aws: 'ready' },
      },
      {
        ...awsReadCatalogCapability,
        permitted: true,
        required_connector_kinds: ['aws'],
        connector_readiness: { aws: 'ready' },
      },
      {
        ...awsReadCapability,
        permitted: true,
        required_connector_kinds: ['aws'],
        connector_readiness: { aws: 'ready' },
      },
    ]),
  }))

  await page.goto('/connectors')
  await expect(page.locator('main h1').last()).toHaveText('Connectors')
  await expect(page.getByTestId('project-selector-trigger')).toContainText('AWS fixture project')
  await page.getByRole('button', { name: 'Add project AWS Connector' }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByLabel('Connector name').fill('Production AWS')
  await dialog.getByLabel('AWS region').fill('us-west-2')
  await dialog.getByLabel('Expected AWS account ID').fill('123456789012')
  await dialog.getByLabel('Basic read role ARN').fill(configuration.basic_role_arn)
  await dialog.getByLabel('Bootstrap access key ID').fill('AKIAE2ESYNTHETIC')
  await dialog.getByLabel('Bootstrap secret access key').fill('synthetic-aws-source-secret')
  const setupButton = dialog.getByRole('button', { name: 'Set up AWS connector' })
  await expect(setupButton).toBeEnabled()
  await setupButton.click()

  await expect.poll(() => credentialRequest !== null).toBe(true)
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('button', { name: 'Configure Production AWS' })).toContainText('Configured')
  expect(credentialRequest).toMatchObject({ name: 'Production AWS', configuration })
  expect((credentialRequest?.auth as Record<string, string>).access_key_id).toBe('AKIAE2ESYNTHETIC')
  expect(JSON.stringify(credentialRequest)).toContain('synthetic-aws-source-secret')
  expect(await page.locator('body').innerText()).not.toContain('AKIAE2ESYNTHETIC')
  expect(await page.locator('body').innerText()).not.toContain('synthetic-aws-source-secret')

  await page.goto('/')
  await page.getByRole('button', { name: 'Open conversation Deploy pipeline' }).click()
  await page.getByRole('button', { name: 'Add tool' }).click()
  await page.getByTestId('tool-choice').filter({ hasText: 'S3 bucket storage' }).click()
  await page.getByLabel('S3 bucket name').fill('production-data')
  await page.getByLabel('AWS region (optional)').fill('us-west-2')
  await page.getByRole('button', { name: 'Measure' }).click()

  await expect.poll(() => queued.submittedTools.length).toBe(1)
  expect(queued.submittedTools[0].payload.capability.id).toBe('aws_s3_bucket_storage')
  expect(queued.submittedTools[0].payload.arguments).toEqual({
    connector_id: awsConnector.id,
    bucket_name: 'production-data',
    region: 'us-west-2',
  })
  expect(queued.submittedTools[0].payload.connector_selections).toEqual({ aws: awsConnector.id })

  await page.getByRole('button', { name: 'Add tool' }).click()
  await page.getByTestId('tool-choice').filter({ hasText: 'S3 bucket metadata' }).click()
  await page.getByLabel('S3 bucket name').fill('production-data')
  await page.getByRole('button', { name: 'Inspect', exact: true }).click()
  await expect.poll(() => queued.submittedTools.length).toBe(2)
  expect(queued.submittedTools[1].payload.capability.id).toBe('aws_s3_bucket_metadata')
  expect(queued.submittedTools[1].payload.arguments).toEqual({
    connector_id: awsConnector.id,
    bucket_name: 'production-data',
  })
  expect(queued.submittedTools[1].payload.connector_selections).toEqual({ aws: awsConnector.id })

  await page.goto('/conversation/c1')
  const automaticTools = page.getByTestId('automatic-tool-mode')
  await page.getByRole('button', { name: 'Add tool' }).click()
  await expect(automaticTools).toBeEnabled()
  await expect(automaticTools).toHaveAttribute('aria-checked', 'true')
  await page.keyboard.press('Escape')
  await page.getByLabel('Message').fill('Discover S3 buckets and report which ones this role can inspect.')
  await page.getByRole('button', { name: 'Send' }).click()
  await expect.poll(() => queued.submitted.length).toBe(1)
  expect(queued.submitted[0].payload).toMatchObject({
    enable_tools: true,
  })
  expect(queued.submitted[0].payload).not.toHaveProperty('resource_binding_selections')
  expect(resourceSetupRequests).toBe(0)
})
