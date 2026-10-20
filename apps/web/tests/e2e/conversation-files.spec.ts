import { expect, test } from '@playwright/test'
import {
  clearApiRoutes,
  mockApi,
  setAuthToken,
} from './fixtures/api'

const workspace = {
  id: 'workspace-c1',
  conversation_id: 'c1',
  project_id: 'project-1',
  schema_version: 1,
  relative_root: 'projects/p/conversations/c1',
  storage_id: 'storage-1',
  status: 'available',
  created_at: '2026-10-20T12:00:00Z',
  quota: {
    committed_bytes: 1234,
    committed_count: 2,
    reserved_bytes: 0,
    reserved_count: 0,
    max_bytes: 10000,
    max_count: 20,
  },
}

function file(id: string) {
  return {
    id,
    sha256: id.padEnd(64, 'a').slice(0, 64),
    size_bytes: 512,
    media_type: 'text/plain',
    conversation_id: 'c1',
    project_id: 'project-1',
    name: 'same-name.txt',
    source: 'upload',
    status: 'ready',
    created_at: '2026-10-20T12:00:00Z',
    mutability: 'immutable',
  }
}

test('lists, reads, downloads and handles pinned deletion for exact conversation snapshots', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const fileReads: string[] = []
  await page.route('**/api/conversations/c1/workspace*', async (route) => {
    const cursor = new URL(route.request().url()).searchParams.get('cursor')
    const items = cursor ? [file('snapshot-2')] : [file('snapshot-1')]
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        workspace,
        files: { items, next_cursor: cursor ? null : 'next-page' },
      }),
    })
  })
  await page.route('**/api/conversations/c1/workspace/files/*', async (route) => {
    const artifactId = new URL(route.request().url()).pathname.split('/').at(-1)!
    fileReads.push(artifactId)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        workspace_id: 'workspace-c1',
        artifact_id: artifactId,
        version_id: artifactId,
        sha256: artifactId.padEnd(64, 'a').slice(0, 64),
        media_type: 'text/plain',
        size_bytes: 512,
        offset: 0,
        content: '<script>saved text</script>',
        next_offset: null,
      }),
    })
  })
  await page.route('**/api/artifacts/snapshot-1/content', (route) =>
    route.fulfill({ status: 200, contentType: 'text/plain', body: 'saved text' }),
  )
  await page.route('**/api/artifacts/snapshot-1', async (route) => {
    if (route.request().method() === 'DELETE') {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: {
            error: {
              code: 'artifact_is_pinned',
              message: 'Artifact is selected by an active run',
            },
          },
        }),
      })
      return
    }
    await route.continue()
  })
  page.on('dialog', (dialog) => dialog.accept())
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).click()

  const toggle = page.getByTestId('conversation-files-toggle')
  await expect(toggle).toContainText('Conversation files')
  await toggle.click()
  await expect(page.locator('[data-testid="conversation-file"]')).toHaveCount(1)
  await page.getByRole('button', { name: 'Load more files' }).click()
  await expect(page.locator('[data-testid="conversation-file"]')).toHaveCount(2)
  await expect(page.locator('[data-artifact-id="snapshot-1"]')).toContainText('same-name.txt')
  await expect(page.locator('[data-artifact-id="snapshot-2"]')).toContainText('same-name.txt')

  await page.getByRole('button', { name: 'Read same-name.txt version snapshot-1' }).click()
  const preview = page.getByTestId('selected-file-content')
  await expect(preview).toContainText('<script>saved text</script>')
  await expect(preview.locator('script')).toHaveCount(0)
  expect(fileReads).toEqual(['snapshot-1'])

  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download same-name.txt version snapshot-1' }).click()
  expect((await download).suggestedFilename()).toBe('same-name.txt')

  await page.getByRole('button', { name: 'Delete same-name.txt version snapshot-1' }).click()
  await expect(page.getByRole('alert').last()).toContainText('active operation')
  await expect(page.locator('[data-artifact-id="snapshot-1"]')).toBeVisible()
  await expect(page.getByTestId('conversation-files')).not.toContainText('/home/')
})
