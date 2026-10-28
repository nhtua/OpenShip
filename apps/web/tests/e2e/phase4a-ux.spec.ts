import fs from 'node:fs'
import path from 'node:path'
import { expect, test } from '@playwright/test'
import {
  clearApiRoutes,
  durableEvent,
  E2E_RUN_COMPLETED,
  mockApi,
  mockDurableApi,
  setAuthToken,
} from './fixtures/api'

const OUT = path.resolve(
  __dirname,
  '../../../../.superpowers/sdd/2026-10-19-phase4-execution-and-conversation-workspace/screenshots',
)

function searchOperation(
  id: string,
  status: string,
  query: string,
  seconds: number,
  details: Record<string, unknown> = {},
) {
  const capabilityId = id.includes('tavily') ? 'tavily_web_search' : 'brave_web_search'
  return {
    id,
    run_id: 'run-ux-search',
    conversation_id: 'c1',
    logical_call_id: id,
    capability_id: capabilityId,
    capability_version: '1.0.0',
    manifest_digest: capabilityId === 'brave_web_search' ? 'a'.repeat(64) : 'b'.repeat(64),
    status,
    error_code: null,
    error: (details.error as { code: string; message: string } | undefined) ?? null,
    ...(typeof details.error_code === 'string' ? { error_code: details.error_code } : {}),
    query,
    result: status === 'succeeded' ? {
      query,
      observed_at: `2026-10-20T12:00:${String(seconds).padStart(2, '0')}Z`,
      items: [],
      truncated: false,
      ...details,
    } : null,
    artifacts: [],
    attempts: [],
    created_at: `2026-10-20T12:00:${String(seconds).padStart(2, '0')}Z`,
    updated_at: `2026-10-20T12:00:${String(seconds).padStart(2, '0')}Z`,
    finished_at: ['failed', 'cancelled', 'succeeded'].includes(status)
      ? `2026-10-20T12:00:${String(seconds).padStart(2, '0')}Z`
      : null,
  }
}

async function capture(name: string, page: import('@playwright/test').Page) {
  await page.screenshot({ path: path.join(OUT, name), fullPage: false })
}

test('captures the revised composer and compact tool timeline for visual review', async ({ page }) => {
  fs.mkdirSync(OUT, { recursive: true })
  await clearApiRoutes(page)
  await mockApi(page)
  const longSnippet = 'Terraform modules can package infrastructure resources into reusable configurations. '.repeat(4)
  const operations = [
    searchOperation('brave-success', 'succeeded', 'terraform module registry', 10, {
      items: [
        { title: 'Terraform module registry', url: 'https://docs.example.test/registry', snippet: longSnippet, rank: 1 },
        { title: 'Terraform module sources', url: 'https://learn.example.test/sources', snippet: 'How module sources are selected.', rank: 2 },
      ],
    }),
    searchOperation('tavily-empty', 'succeeded', 'no results for this phrase', 11),
    searchOperation('brave-queued', 'admitted', 'queued search example', 12),
    searchOperation('tavily-running', 'running', 'running search example', 13),
    searchOperation('brave-failed', 'failed', 'failed search example', 14, {
      error_code: 'provider_timeout',
      error: { code: 'provider_timeout', message: 'The search service did not respond within the allowed time.' },
    }),
    searchOperation('tavily-cancelled', 'cancelled', 'cancelled search example', 15),
  ]
  await mockDurableApi(page, {
    operations,
    runReads: [{ ...E2E_RUN_COMPLETED, id: 'run-ux-search', kind: 'tool' }],
    submittedToolRun: { ...E2E_RUN_COMPLETED, id: 'run-ux-search', kind: 'tool' },
    streamEvents: [
      durableEvent(1, 'tool.started', { run_id: 'run-ux-search' }),
      durableEvent(2, 'tool.completed', { run_id: 'run-ux-search' }),
      durableEvent(3, 'run.succeeded', { run_id: 'run-ux-search' }),
    ],
    baseMessages: {
      c1: [
        { id: 'ux-user', role: 'user', content: 'Find current Terraform module guidance.', conversation_id: 'c1', created_at: '2026-10-20T12:00:00Z' },
        { id: 'ux-assistant', role: 'assistant', content: 'I can search the web and compare useful sources.', conversation_id: 'c1', created_at: '2026-10-20T12:00:20Z' },
      ],
    },
  })
  await setAuthToken(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await page.getByRole('button', { name: 'Open conversation Deploy pipeline' }).click()
  await expect(page.getByText('I can search the web and compare useful sources.')).toBeVisible()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)

  await page.getByTestId('model-trigger').click()
  await expect(page.getByTestId('model-trigger')).toContainText('gpt-6-luna')
  await capture('task9a-desktop-model-menu.png', page)
  await page.keyboard.press('Escape')

  await page.getByRole('button', { name: 'Add tool' }).click()
  await capture('task9a-desktop-tool-menu.png', page)
  await page.getByTestId('tool-choice').filter({ hasText: 'Brave Search' }).click()
  await page.setViewportSize({ width: 375, height: 812 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await capture('task9a-mobile-attached-tool.png', page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByLabel('Message').fill('terraform module registry')
  await page.getByRole('button', { name: 'Search', exact: true }).click()

  const firstCard = page.getByTestId('tool-operation-card').first()
  await expect(firstCard).toContainText('2 results')
  const emptyToggle = page.getByRole('button', { name: 'Show results for no results for this phrase' })
  await expect(emptyToggle).toContainText('0 results')
  await expect(page.getByText('The search service did not respond within the allowed time.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Cancel Tavily Search' })).toBeVisible()
  await capture('task9a-desktop-collapsed.png', page)

  await emptyToggle.click()
  await expect(page.getByText('No results returned.')).toBeVisible()
  await capture('task9a-desktop-empty-results.png', page)
  await page.getByRole('button', { name: 'Hide results for no results for this phrase' }).click()
  await page.getByRole('button', { name: 'Show results for terraform module registry' }).click()
  await expect(firstCard.getByRole('link', { name: 'Terraform module registry' })).toBeVisible()
  await capture('task9a-desktop-expanded.png', page)
  await page.getByRole('button', { name: 'Switch to light mode' }).click()
  await capture('task9a-desktop-light.png', page)

  await page.setViewportSize({ width: 375, height: 812 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await capture('task9a-mobile-expanded.png', page)
  await page.getByRole('button', { name: 'Hide results for terraform module registry' }).click()
  await capture('task9a-mobile-collapsed.png', page)
  await page.getByRole('button', { name: 'Add tool' }).click()
  await capture('task9a-mobile-tool-menu.png', page)
  await page.keyboard.press('Escape')
  await page.getByTestId('model-trigger').click()
  await capture('task9a-mobile-model-menu.png', page)
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Switch to dark mode' }).click()
  await page.getByRole('button', { name: 'Show results for terraform module registry' }).click()
  await capture('task9a-mobile-dark-expanded.png', page)
  await page.getByRole('button', { name: 'Hide results for terraform module registry' }).click()
  await capture('task9a-mobile-dark-collapsed.png', page)
  await page.getByRole('button', { name: 'Add tool' }).click()
  await capture('task9a-mobile-dark-tool-menu.png', page)
  await page.keyboard.press('Escape')
  await page.getByTestId('model-trigger').click()
  await capture('task9a-mobile-dark-model-menu.png', page)
})
