import { createHash, randomUUID } from 'node:crypto'
import { expect, test } from '@playwright/test'

test('shows exact saved conversation files through the real API and filesystem store', async ({ page, request }) => {
  const suffix = randomUUID().replaceAll('-', '').slice(0, 12)
  const registration = await request.post('/api/auth/register', {
    data: {
      username: `files${suffix}`,
      email: `files${suffix}@example.test`,
      password: randomUUID(),
    },
  })
  expect(registration.status()).toBe(201)
  const token = (await registration.json()).access_token as string
  const headers = { Authorization: `Bearer ${token}` }

  const conversationResponse = await request.post('/api/conversations', {
    headers,
    data: { title: 'Persistent files' },
  })
  expect(conversationResponse.status()).toBe(201)
  const conversationId = (await conversationResponse.json()).id as string
  const content = 'A retained result survives a conversation reload.'
  const digest = createHash('sha256').update(content).digest('base64')
  const upload = await request.post(`/api/conversations/${conversationId}/artifacts`, {
    headers: {
      ...headers,
      'Content-Type': 'text/plain',
      'Idempotency-Key': randomUUID(),
      Digest: `sha-256=${digest}`,
      'X-Artifact-Name': 'persistent%20result.txt',
    },
    data: content,
  })
  expect(upload.status()).toBe(201)
  const artifact = await upload.json()

  await page.addInitScript((value: string) => localStorage.setItem('access_token', value), token)
  await page.goto('/')
  await page.getByRole('button', { name: 'Open conversation Persistent files' }).click()
  await page.getByTestId('conversation-files-trigger').click()
  const row = page.locator(`[data-artifact-id="${artifact.id}"]`)
  await expect(row).toContainText('persistent result.txt')
  await page.getByRole('button', { name: 'Preview persistent result.txt' }).click()
  await expect(page.getByTestId('selected-file-content')).toContainText(content)
  await expect(page.getByTestId('selected-file-content')).toContainText(artifact.sha256)

  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download persistent result.txt' }).click()
  expect((await download).suggestedFilename()).toBe('persistent result.txt')

  page.on('dialog', (dialog) => dialog.accept())
  await page.getByRole('button', { name: 'Delete persistent result.txt' }).click()
  await expect(row).toContainText('Deleted')

  const otherConversation = await request.post('/api/conversations', {
    headers,
    data: { title: 'Separate files' },
  })
  expect(otherConversation.status()).toBe(201)
  const otherId = (await otherConversation.json()).id as string
  await page.reload()
  await page.getByRole('button', { name: 'Open conversation Separate files' }).click()
  await page.getByTestId('conversation-files-trigger').click()
  await expect(page.getByText('No saved files in this conversation.')).toBeVisible()
  await expect(page.locator(`[data-artifact-id="${artifact.id}"]`)).toHaveCount(0)
  expect(otherId).not.toBe(conversationId)
})
