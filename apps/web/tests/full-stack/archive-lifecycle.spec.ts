import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { createInterface } from 'node:readline'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

let stackProcess: ChildProcessWithoutNullStreams | null = null
let apiBaseUrl = ''

async function startDisposableStack(): Promise<void> {
  const apiRoot = path.resolve(process.cwd(), '../../apps/api')
  stackProcess = spawn('uv', ['run', '--quiet', '--no-sync', 'python', '-m', 'tests.full_stack.archive_browser_stack'], {
    cwd: apiRoot,
    env: { ...process.env, UV_CACHE_DIR: '/tmp/openship-uv-cache' },
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  const lines = createInterface({ input: stackProcess.stdout })
  let safeFailure = ''
  stackProcess.stderr.on('data', (chunk: Buffer) => {
    const match = chunk.toString().match(/ARCHIVE_STACK_ERROR:([a-z ]+):([A-Za-z]+)/)
    if (match) safeFailure = `${match[1].trim()} ${match[2]}`
  })
  apiBaseUrl = await new Promise<string>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('Timed out starting the disposable archive stack')), 180_000)
    stackProcess?.once('error', () => {
      clearTimeout(timeout)
      reject(new Error('Could not start the disposable archive stack'))
    })
    stackProcess?.once('exit', (code) => {
      clearTimeout(timeout)
      reject(new Error(`Disposable archive stack setup failed (${safeFailure || code || 'signal'})`))
    })
    lines.once('line', (line) => {
      clearTimeout(timeout)
      try {
        resolve((JSON.parse(line) as { base_url: string }).base_url)
      } catch {
        reject(new Error('Disposable archive stack returned invalid startup data'))
      }
    })
  })
}

async function stopDisposableStack(): Promise<void> {
  const process = stackProcess
  if (!process || process.exitCode !== null) return
  process.stdin.write('stop\n')
  await new Promise<void>((resolve) => process.once('exit', () => resolve()))
  stackProcess = null
}

async function proxyApiToDisposableStack(page: Page): Promise<void> {
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/events/stream')) {
      const eventsUrl = `${apiBaseUrl}${url.pathname.slice(0, -'/stream'.length)}${url.search}`
      const deadline = Date.now() + 45_000
      let events: Array<{
        id: string
        sequence: number
        type: string
        run_id: string | null
        conversation_id: string
        payload: Record<string, unknown>
        created_at: string
      }> = []
      do {
        const response = await route.fetch({ url: eventsUrl, timeout: 10_000 })
        if (!response.ok()) {
          await route.fulfill({ response })
          return
        }
        const result = await response.json() as { events: typeof events }
        events = result.events
        if (events.some((event) => ['run.succeeded', 'run.failed', 'run_cancelled'].includes(event.type))) {
          break
        }
        if (Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 500))
      } while (Date.now() < deadline)

      const body = events.map((event) =>
        `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
      ).join('') || ': no events\n\n'
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream; charset=utf-8' },
        body,
      })
      return
    }
    const response = await route.fetch({
      url: `${apiBaseUrl}${url.pathname}${url.search}`,
      timeout: 30_000,
    })
    await route.fulfill({ response })
  })
}

async function json<T>(request: APIRequestContext, route: string, token: string, method = 'GET', data?: unknown): Promise<T> {
  const response = await request.fetch(`${apiBaseUrl}/api${route}`, {
    method,
    headers: { Authorization: `Bearer ${token}` },
    ...(data === undefined ? {} : { data }),
  })
  expect(response.ok(), `${method} ${route}: ${response.status()}`).toBeTruthy()
  return response.json() as Promise<T>
}

test.beforeAll(async () => {
  await startDisposableStack()
})

test.afterAll(async () => {
  await stopDisposableStack()
})

test('preserves conversation identity and file bytes through archive and restore', async ({ page, request }) => {
  const suffix = randomUUID().replaceAll('-', '').slice(0, 12)
  const registration = await request.post(`${apiBaseUrl}/api/auth/register`, {
    data: {
      username: `archive${suffix}`,
      email: `archive${suffix}@example.test`,
      password: randomUUID(),
    },
  })
  expect(registration.status()).toBe(201)
  const token = (await registration.json()).access_token as string
  const headers = { Authorization: `Bearer ${token}` }

  const projects = await json<Array<{ id: string }>>(request, '/projects?state=active', token)
  expect(projects).toHaveLength(1)
  const invalidProjectState = await request.get(`${apiBaseUrl}/api/projects?state=invalid`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(invalidProjectState.status()).toBe(422)
  const activeProjectId = projects[0].id
  const secondProject = await json<{ id: string }>(request, '/projects', token, 'POST', { name: 'Archive isolation project' })
  const conversation = await json<{ id: string }>(request, '/conversations', token, 'POST', {
    title: 'Archive file retention',
    project_id: activeProjectId,
  })
  const childConversation = await json<{ id: string }>(request, '/conversations', token, 'POST', {
    title: 'Independent child archive',
    project_id: secondProject.id,
  })

  const fileBytes = Buffer.from('A saved result remains byte-for-byte intact after archive.')
  const digest = createHash('sha256').update(fileBytes).digest('base64')
  const upload = await request.post(`${apiBaseUrl}/api/conversations/${conversation.id}/artifacts`, {
    headers: {
      ...headers,
      'Content-Type': 'text/plain',
      'Idempotency-Key': randomUUID(),
      Digest: `sha-256=${digest}`,
      'X-Artifact-Name': 'retained%20result.txt',
    },
    data: fileBytes,
  })
  expect(upload.status()).toBe(201)
  const artifact = await upload.json() as { id: string; sha256: string }
  expect(artifact.sha256).toBe(createHash('sha256').update(fileBytes).digest('hex'))
  const workspaceBefore = await json<{ workspace: { relative_root: string }; files: { items: Array<{ id: string; sha256: string }> } }>(
    request, `/conversations/${conversation.id}/workspace`, token,
  )
  const snapshotBefore = await json<{ messages: Array<{ id: string }>; runs: Array<{ id: string }> }>(
    request, `/conversations/${conversation.id}/snapshot`, token,
  )

  await page.addInitScript((value: string) => localStorage.setItem('access_token', value), token)
  await proxyApiToDisposableStack(page)
  await page.goto('/')
  await page.getByRole('button', { name: `Open conversation ${conversation.title}` }).click()
  const actions = page.getByRole('button', { name: `Conversation actions for ${conversation.title}` })
  await actions.click()
  await page.getByRole('menuitem', { name: 'Archive' }).click()
  await expect(page.getByTestId('archived-conversation-banner')).toContainText('saved resources are unchanged')

  const activeAfterArchive = await json<Array<{ id: string }>>(request, '/workspace/conversations?state=active', token)
  const archivedAfterArchive = await json<Array<{ id: string; archived_at: string | null }>>(
    request, '/workspace/conversations?state=archived', token,
  )
  expect(activeAfterArchive.some((item) => item.id === conversation.id)).toBe(false)
  expect(archivedAfterArchive.find((item) => item.id === conversation.id)?.archived_at).not.toBeNull()
  const retainedBytes = await request.get(`${apiBaseUrl}/api/artifacts/${artifact.id}/content`, { headers })
  expect(retainedBytes.status()).toBe(200)
  expect(await retainedBytes.body()).toEqual(fileBytes)

  await page.getByRole('button', { name: 'Conversation history actions' }).click()
  await page.getByRole('menuitem', { name: 'Archived conversations' }).click()
  const archivedDialog = page.getByRole('dialog', { name: 'Archived conversations' })
  await archivedDialog.getByRole('button', { name: `Open archived conversation ${conversation.title}` }).click()
  await expect(page.getByLabel('Message')).toBeDisabled()
  await page.getByTestId('conversation-files-trigger').click()
  await page.getByRole('button', { name: 'Preview retained result.txt' }).click()
  await expect(page.getByTestId('selected-file-content')).toContainText(fileBytes.toString('utf8'))
  await expect(page.getByRole('button', { name: 'Delete retained result.txt' })).toHaveCount(0)
  const downloadReady = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download retained result.txt' }).click()
  const download = await downloadReady
  expect(await readFile((await download.path())!)).toEqual(fileBytes)

  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Conversation files' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Restore conversation' }).click()
  await expect(page.getByTestId('archived-conversation-banner')).toHaveCount(0)
  const restoredSnapshot = await json<{ messages: Array<{ id: string }>; runs: Array<{ id: string }> }>(
    request, `/conversations/${conversation.id}/snapshot`, token,
  )
  expect(restoredSnapshot.messages.map((item) => item.id)).toEqual(snapshotBefore.messages.map((item) => item.id))
  expect(restoredSnapshot.runs.map((item) => item.id)).toEqual(snapshotBefore.runs.map((item) => item.id))
  const workspaceAfter = await json<{ workspace: { relative_root: string }; files: { items: Array<{ id: string; sha256: string }> } }>(
    request, `/conversations/${conversation.id}/workspace`, token,
  )
  expect(workspaceAfter.workspace.relative_root).toBe(workspaceBefore.workspace.relative_root)
  expect(workspaceAfter.files.items.map((item) => item.id)).toContain(artifact.id)
  expect(workspaceBefore.files.items.map((item) => item.id)).toContain(artifact.id)
  expect(workspaceAfter.files.items.find((item) => item.id === artifact.id)?.sha256).toBe(artifact.sha256)

  await page.getByLabel('Message').fill('Search for the synthetic archive lifecycle validation result.')
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  const operation = page.getByTestId('tool-operation-card').last()
  await expect(operation).toContainText('Completed', { timeout: 60_000 })
  await operation.getByRole('button', {
    name: 'Show results for synthetic automatic chat search',
  }).click()
  await expect(operation).toContainText('Synthetic Brave result')
  const continuedSnapshot = await json<{ runs: Array<{ id: string; status: string }> }>(
    request, `/conversations/${conversation.id}/snapshot`, token,
  )
  expect(continuedSnapshot.runs.length).toBeGreaterThan(snapshotBefore.runs.length)
  expect(continuedSnapshot.runs.at(-1)?.status).toBe('completed')

  const childWorkspaceBefore = await json<{ workspace: { relative_root: string } }>(
    request, `/conversations/${childConversation.id}/workspace`, token,
  )
  await json(request, `/conversations/${childConversation.id}/archive`, token, 'POST')
  await json(request, `/projects/${secondProject.id}/archive`, token, 'POST')
  await json(request, `/projects/${secondProject.id}/restore`, token, 'POST')
  const archivedAfterParentRestore = await json<Array<{
    id: string
    archived_at: string | null
    project_archived_at: string | null
  }>>(request, '/workspace/conversations?state=archived', token)
  const childAfterRestore = archivedAfterParentRestore.find((item) => item.id === childConversation.id)
  expect(childAfterRestore?.archived_at).not.toBeNull()
  expect(childAfterRestore?.project_archived_at).toBeNull()
  const childWorkspaceAfter = await json<{ workspace: { relative_root: string } }>(
    request, `/conversations/${childConversation.id}/workspace`, token,
  )
  expect(childWorkspaceAfter.workspace.relative_root).toBe(childWorkspaceBefore.workspace.relative_root)
})
