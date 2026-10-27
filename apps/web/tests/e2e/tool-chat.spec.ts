import { expect, test } from '@playwright/test'
import {
  clearApiRoutes,
  durableEvent,
  E2E_PROFILES,
  E2E_RUN_COMPLETED,
  E2E_RUN_QUEUED,
  mockApi,
  mockDurableApi,
  setAuthToken,
} from './fixtures/api'

test('automatically invokes the authorized search tools from the composer without attachments', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const operation = {
    id: 'operation-auto-search',
    run_id: 'run-auto-search',
    conversation_id: 'c1',
    logical_call_id: 'model:0:call:0',
    capability_id: 'tavily_web_search',
    capability_version: '1.0.0',
    manifest_digest: 'b'.repeat(64),
    status: 'succeeded',
    error_code: null,
    error: null,
    query: 'nearest star system',
    result: {
      query: 'nearest star system',
      observed_at: '2026-10-20T12:00:01Z',
      items: [{ title: 'Proxima Centauri', url: 'https://science.example.test/proxima', snippet: 'The nearest star system.', rank: 1 }],
      truncated: false,
    },
    artifacts: [],
    attempts: [],
    created_at: '2026-01-01T00:00:03Z',
    updated_at: '2026-01-01T00:00:03Z',
    finished_at: '2026-01-01T00:00:03Z',
  }
  const profiles = {
    ...E2E_PROFILES,
    profiles: E2E_PROFILES.profiles.map((profile) => ({
      ...profile,
      capabilities: { ...profile.capabilities, tool_calls: profile.profile_id === 'primary-model' },
    })),
  }
  const capture = await mockDurableApi(page, {
    profiles,
    submittedRun: { ...E2E_RUN_QUEUED, id: 'run-auto-search', kind: 'chat', graph_version: 2 },
    runReads: [{ ...E2E_RUN_COMPLETED, id: 'run-auto-search', kind: 'chat', graph_version: 2, operations: [operation] }],
    operations: [operation],
    assistantContent: 'Proxima Centauri is the closest star system to the Sun.',
    streamEvents: [
      durableEvent(1, 'tool.completed', { run_id: 'run-auto-search', payload: { operation_id: operation.id } }),
      durableEvent(2, 'run.succeeded', { run_id: 'run-auto-search' }),
    ],
  })
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Open conversation Deploy pipeline' }).click()
  await expect(page.getByTestId('automatic-tool-chip')).toBeVisible()

  await page.getByLabel('Message').fill('What is the nearest star system?')
  await page.getByRole('button', { name: 'Send', exact: true }).click()

  const card = page.getByTestId('tool-operation-card')
  await expect(card).toContainText('Tavily Search')
  await expect(card).toContainText('nearest star system')
  await expect(card).toContainText('Completed')
  await page.getByRole('button', { name: 'Show results for nearest star system' }).click()
  await expect(card.getByTestId('operation-provenance')).toContainText('tavily_web_search@1.0.0')
  await expect(card.getByTestId('operation-provenance')).toContainText(operation.id)
  await expect(page.getByText('Proxima Centauri is the closest star system to the Sun.')).toBeVisible()
  await expect(page.getByTestId('attached-tool')).toHaveCount(0)

  expect(capture.submitted).toHaveLength(1)
  expect(capture.submitted[0].payload).toMatchObject({
    content: 'What is the nearest star system?',
    enable_tools: true,
    provider_profile_id: null,
    fallback_profile_ids: [],
  })
  expect(capture.submitted[0].payload).not.toHaveProperty('artifact_ids')
  expect(capture.submittedTools).toHaveLength(0)

  const stream = page.getByTestId('stream')
  const content = await stream.textContent()
  expect(content!.indexOf('What is the nearest star system?')).toBeLessThan(content!.indexOf('Tavily Search'))
  expect(content!.indexOf('Tavily Search')).toBeLessThan(content!.indexOf('Proxima Centauri is the closest star system'))
})
