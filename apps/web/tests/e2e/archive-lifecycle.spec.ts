import { expect, test } from '@playwright/test'
import { clearApiRoutes, E2E_CONVERSATIONS, E2E_MESSAGES, mockApi, setAuthToken } from './fixtures/api'

const savedText = 'The archived file remains available.'

async function prepareArchivePage(page: import('@playwright/test').Page): Promise<void> {
  await clearApiRoutes(page)
  await mockApi(page, {
    conversations: [E2E_CONVERSATIONS[0]],
    messages: { c1: E2E_MESSAGES.c1 },
  })
  await page.route('**/api/conversations/c1/workspace*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      workspace: {
        id: 'workspace-c1', conversation_id: 'c1', project_id: 'project-e2e', schema_version: 1,
        relative_root: 'projects/project-e2e/conversations/c1', storage_id: 'storage-e2e',
        status: 'available', created_at: '2026-01-01T00:00:00Z',
        quota: { committed_bytes: savedText.length, committed_count: 1, reserved_bytes: 0, reserved_count: 0, max_bytes: 10000, max_count: 20 },
      },
      files: { items: [{
        id: 'archive-file', sha256: 'b'.repeat(64), size_bytes: savedText.length, media_type: 'text/plain',
        conversation_id: 'c1', project_id: 'project-e2e', name: 'retained.txt', source: 'upload',
        status: 'ready', created_at: '2026-01-01T00:00:00Z', mutability: 'immutable',
      }], next_cursor: null },
    }),
  }))
  await page.route('**/api/conversations/c1/workspace/files/archive-file*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      workspace_id: 'workspace-c1', artifact_id: 'archive-file', version_id: 'archive-file',
      sha256: 'b'.repeat(64), media_type: 'text/plain', size_bytes: savedText.length,
      offset: 0, content: savedText, next_offset: null,
    }),
  }))
  await page.route('**/api/artifacts/archive-file/content', (route) =>
    route.fulfill({ status: 200, contentType: 'text/plain', body: savedText }),
  )
  await setAuthToken(page)
}

test('archives through contextual menus and restores from empty history', async ({ page }) => {
  await prepareArchivePage(page)
  let failFirstArchive = true
  await page.route('**/api/conversations/c1/archive', async (route) => {
    if (failFirstArchive) {
      failFirstArchive = false
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'The conversation could not be archived. Reload and try again.' }),
      })
      return
    }
    await route.fallback()
  })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Conversation actions for Deploy pipeline' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Archive conversation/ })).toHaveCount(0)

  const rowActions = page.getByRole('button', { name: 'Conversation actions for Deploy pipeline' })
  await rowActions.focus()
  await rowActions.click()
  await expect(page.getByRole('menuitem', { name: 'Archive' })).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: 'Archive' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(rowActions).toBeFocused()
  await expect(page).toHaveURL('/')

  await rowActions.click()
  await page.getByRole('menuitem', { name: 'Archive' }).click()
  await expect(page.getByRole('alert')).toContainText('could not be archived')
  await expect(page.getByRole('button', { name: 'Open conversation Deploy pipeline' })).toBeVisible()

  await page.getByRole('button', { name: 'Conversation actions for Deploy pipeline' }).click()
  await page.getByRole('menuitem', { name: 'Archive' }).click()
  await expect(page.getByText('No conversations yet')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Conversation actions for Deploy pipeline' })).toHaveCount(0)

  await page.getByRole('button', { name: 'Conversation history actions' }).click()
  await page.getByRole('menuitem', { name: 'Archived conversations' }).click()
  const dialog = page.getByRole('dialog', { name: 'Archived conversations' })
  await expect(dialog).toContainText('Deploy pipeline')
  await dialog.getByRole('button', { name: 'Open archived conversation Deploy pipeline' }).click()
  await expect(page.getByTestId('archived-conversation-banner')).toContainText('saved resources are unchanged')
  await expect(page.getByLabel('Message')).toBeDisabled()

  await page.getByTestId('conversation-files-trigger').click()
  await page.getByRole('button', { name: 'Preview retained.txt' }).click()
  await expect(page.getByTestId('selected-file-content')).toContainText(savedText)
  await expect(page.getByRole('button', { name: 'Delete retained.txt' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Close', exact: true }).click()

  await page.getByRole('button', { name: 'Restore conversation' }).click()
  await expect(page.getByTestId('archived-conversation-banner')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Open conversation Deploy pipeline' })).toBeVisible()
  await expect(page.getByLabel('Message')).toBeEnabled()
})

test('keyboard and touch can recover archived conversations', async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
  })
  const page = await context.newPage()
  try {
    await prepareArchivePage(page)
    await page.goto('/')
    await page.getByRole('button', { name: 'Toggle Sidebar' }).tap()
    const rowActions = page.getByRole('button', { name: 'Conversation actions for Deploy pipeline' })
    await rowActions.tap()
    await page.getByRole('menuitem', { name: 'Archive' }).tap()
    await expect(page.getByText('No conversations yet')).toBeVisible()

    const historyActions = page.getByRole('button', { name: 'Conversation history actions' })
    await historyActions.tap()
    await page.getByRole('menuitem', { name: 'Archived conversations' }).tap()
    const dialog = page.getByRole('dialog', { name: 'Archived conversations' })
    await expect(dialog.getByRole('button', { name: 'Restore conversation Deploy pipeline' })).toBeVisible()
    await dialog.getByRole('button', { name: 'Restore conversation Deploy pipeline' }).tap()
    await expect(dialog).toContainText('No archived conversations.')
    await dialog.getByRole('button', { name: 'Close archived conversations' }).tap()
    await expect(page.getByRole('button', { name: 'Open conversation Deploy pipeline' })).toBeVisible()
  } finally {
    await context.close()
  }
})

test('keeps conversation actions visible on touch devices at tablet widths', async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 900, height: 844 },
    hasTouch: true,
  })
  const page = await context.newPage()
  try {
    await prepareArchivePage(page)
    await page.goto('/')
    const rowActions = page.getByRole('button', { name: 'Conversation actions for Deploy pipeline' })
    await expect(rowActions).toHaveCSS('opacity', '1')
    await rowActions.tap()
    await expect(page.getByRole('menuitem', { name: 'Archive' })).toBeVisible()
  } finally {
    await context.close()
  }
})
