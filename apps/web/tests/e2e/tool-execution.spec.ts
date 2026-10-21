import { expect, test } from '@playwright/test'
import {
  clearApiRoutes,
  durableEvent,
  E2E_RUN_QUEUED,
  mockDurableApi,
  mockApi,
  setAuthToken,
} from './fixtures/api'

test('submits a manual search, renders its retained result, and downloads the snapshot', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const artifact = {
    id: 'artifact-search-1',
    sha256: 'c'.repeat(64),
    size_bytes: 512,
    media_type: 'application/json',
    conversation_id: 'c1',
    project_id: 'project-1',
    name: 'search-results.json',
    source: 'tavily_web_search',
    status: 'ready',
    created_at: '2026-10-20T12:00:00Z',
    mutability: 'immutable',
  }
  const capabilities = [
    {
      id: 'brave_web_search', version: '1.0.0', manifest_digest: 'a'.repeat(64),
      input_schema: {}, output_schema: {}, effect: 'read_only_external',
      enabled: true, available: false, unavailable_reason: 'missing_credentials' as const,
    },
    {
      id: 'tavily_web_search', version: '1.0.0', manifest_digest: 'b'.repeat(64),
      input_schema: {}, output_schema: {}, effect: 'read_only_external',
      enabled: true, available: true, unavailable_reason: null,
    },
  ]
  const operation = {
    id: 'operation-search-1',
    run_id: 'run-search-1',
    conversation_id: 'c1',
    logical_call_id: 'manual-search-1',
    capability_id: 'tavily_web_search',
    capability_version: '1.0.0',
    manifest_digest: 'b'.repeat(64),
    status: 'succeeded',
    error_code: null,
    error: null,
    query: 'terraform module registry',
    result: {
      operation_id: 'operation-search-1',
      observed_at: '2026-10-20T12:00:00Z',
      query: 'terraform module registry',
      items: [
        {
          title: 'Terraform module registry',
          url: 'https://docs.example.test/registry',
          snippet: 'Browse modules published for Terraform.',
          rank: 1,
        },
      ],
      truncated: false,
    },
    artifacts: [artifact],
    attempts: [],
    created_at: '2026-10-20T12:00:00Z',
    updated_at: '2026-10-20T12:00:00Z',
    finished_at: '2026-10-20T12:00:00Z',
  }
  const capture = await mockDurableApi(page, {
    capabilities,
    toolsStatus: 202,
    submittedToolRun: {
      ...E2E_RUN_QUEUED,
      id: 'run-search-1',
      kind: 'tool',
      graph_version: 1,
    },
    operations: [operation],
    streamEvents: [
      durableEvent(1, 'tool.started', { run_id: 'run-search-1', payload: { operation_id: 'operation-search-1' } }),
      durableEvent(2, 'tool.completed', { run_id: 'run-search-1', payload: { operation_id: 'operation-search-1', status: 'succeeded' } }),
      durableEvent(3, 'run.succeeded', { run_id: 'run-search-1' }),
    ],
  })
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()

  await page.getByRole('button', { name: 'Add tool' }).click()
  await page.getByTestId('tool-choice').filter({ hasText: 'Tavily Search' }).click()
  await page.getByLabel('Message').fill('terraform module registry')
  await page.getByRole('button', { name: 'Search', exact: true }).click()

  const operationCard = page.getByTestId('tool-operation-card')
  await expect(operationCard).toContainText('Tavily Search')
  await expect(operationCard).toContainText('Completed')
  await expect(operationCard).toContainText('1 result')
  await page.getByRole('button', { name: 'Show results for terraform module registry' }).click()
  await expect(operationCard).toContainText('Searched Oct 20')
  await expect(operationCard.getByRole('link', { name: 'Terraform module registry' })).toHaveAttribute(
    'href',
    'https://docs.example.test/registry',
  )
  expect(capture.submittedTools).toHaveLength(1)
  expect(capture.submittedTools[0].conversation_id).toBe('c1')
  expect(capture.submittedTools[0].payload).toMatchObject({
    capability: {
      id: 'tavily_web_search',
      version: '1.0.0',
      manifest_digest: 'b'.repeat(64),
    },
    arguments: { query: 'terraform module registry', max_results: 5 },
  })
  expect(capture.submittedTools[0].payload).toHaveProperty('client_request_id')

  const download = page.waitForEvent('download')
  await operationCard.getByRole('button', { name: 'Download search-results.json' }).click()
  expect((await download).suggestedFilename()).toBe('search-results.json')
})
