import type { Page, Route } from '@playwright/test'

/**
 * Deterministic /api fixtures (plan Task 7.2). Every response is local and
 * fixed; no real credentials or model calls are involved.
 */

export interface E2EConversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface E2EMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  conversation_id: string | null
  created_at: string
}

export const E2E_USER = {
  id: 'u1',
  username: 'testuser',
  email: 'test@example.com',
}

export const E2E_TOKEN = 'e2e-test-token-not-a-real-credential'

export const E2E_CONVERSATIONS: E2EConversation[] = [
  {
    id: 'c1',
    title: 'Deploy pipeline',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T10:00:00Z',
  },
  {
    id: 'c2',
    title: 'Scale up web',
    created_at: '2026-01-02T00:00:00Z',
    updated_at: '2026-01-02T10:00:00Z',
  },
]

export const E2E_MESSAGES: Record<string, E2EMessage[]> = {
  c1: [
    {
      id: 'm1',
      role: 'user',
      content: 'Deploy staging',
      conversation_id: 'c1',
      created_at: '2026-01-01T09:00:00Z',
    },
    {
      id: 'm2',
      role: 'assistant',
      content: 'Staging deployed successfully.',
      conversation_id: 'c1',
      created_at: '2026-01-01T09:00:05Z',
    },
  ],
  c2: [
    {
      id: 'm3',
      role: 'user',
      content: 'Scale web to 3 replicas',
      conversation_id: 'c2',
      created_at: '2026-01-02T09:00:00Z',
    },
    {
      id: 'm4',
      role: 'assistant',
      content: 'Scaled web to 3 replicas.',
      conversation_id: 'c2',
      created_at: '2026-01-02T09:00:05Z',
    },
  ],
}

/** Many-conversation variant: `count` rows, the first with a 300-char title. */
export function makeConversations(count: number): E2EConversation[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `conv-${i + 1}`,
    title: i === 0 ? 'A'.repeat(300) : `Conversation ${i + 1}`,
    created_at: `2026-01-01T00:00:0${i % 10}Z`,
    updated_at: '2026-01-01T10:00:00Z',
  }))
}

/** Many-message variant: `count` rows, the last a 2000-char unbroken string. */
export function makeMessages(
  count: number,
  conversationId = 'conv-1',
): E2EMessage[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `msg-${i + 1}`,
    role: i % 2 === 0 ? 'user' : 'assistant',
    content: i === count - 1 ? 'B'.repeat(2000) : `Message ${i + 1}`,
    conversation_id: conversationId,
    created_at: `2026-01-01T00:0${i % 10}:${String(i % 60).padStart(2, '0')}Z`,
  }))
}

/**
 * Fulfilled SSE fixture: chunk + complete events in canonical
 * `data: {json}\n\n` framing. A fulfilled body verifies final rendering;
 * progressive streaming timing is covered by the component/store unit tests
 * with a controlled ReadableStream (plan Task 7.2).
 */
function sseBody(chunks: string[], messageId: string): string {
  const events = chunks.map(
    (chunk) => `data: ${JSON.stringify({ type: 'chunk', content: chunk })}\n\n`,
  )
  events.push(`data: ${JSON.stringify({ type: 'complete', message_id: messageId })}\n\n`)
  return events.join('')
}

export interface MockApiOptions {
  loginStatus?: number
  registerStatus?: number
  conversationsStatus?: number
  conversationMessagesStatus?: number
  chatStatus?: number
  /** Artificial latency (ms) before the login response resolves. */
  loginDelay?: number
  /** Artificial latency (ms) before the chat SSE response resolves. */
  chatDelay?: number
  sseChunks?: string[]
  sseMessageId?: string
  conversations?: E2EConversation[]
  messages?: Record<string, E2EMessage[]>
}

/**
 * Removes all previously registered route handlers so that calling
 * `mockApi()` from `beforeEach` does not accumulate handlers across tests.
 */
export async function clearApiRoutes(page: Page): Promise<void> {
  await page.unrouteAll({ behavior: 'ignoreMissing' })
}

export async function mockApi(
  page: Page,
  options: MockApiOptions = {},
): Promise<void> {
  const {
    loginStatus = 200,
    registerStatus = 200,
    conversationsStatus = 200,
    conversationMessagesStatus = 200,
    chatStatus = 200,
    loginDelay = 0,
    chatDelay = 0,
    sseChunks = ['He', 'llo', ' world'],
    sseMessageId = 'msg-900',
    conversations = E2E_CONVERSATIONS,
    messages = E2E_MESSAGES,
  } = options

  let sentMessages: { conversation_id: string; content: string }[] = []

  const json = (route: Route, status: number, body: unknown): Promise<void> =>
    route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })

  await page.route('**/api/auth/login', async (route) => {
    if (loginDelay > 0) await new Promise((r) => setTimeout(r, loginDelay))
    if (loginStatus !== 200) {
      await json(route, loginStatus, { detail: 'Incorrect username or password' })
    } else {
      await json(route, 200, { access_token: E2E_TOKEN, user: E2E_USER })
    }
  })

  await page.route('**/api/auth/register', (route) =>
    registerStatus !== 200
      ? json(route, registerStatus, { detail: 'Registration failed' })
      : json(route, 200, { access_token: E2E_TOKEN, user: E2E_USER }),
  )

  await page.route('**/api/auth/logout', (route) => json(route, 200, {}))

  await page.route('**/api/workspace/conversations', (route) =>
    conversationsStatus !== 200
      ? json(route, conversationsStatus, { detail: 'Server exploded' })
      : json(route, 200, conversations),
  )

  await page.route('**/api/conversations', (route) => {
    if (route.request().method() !== 'POST') {
      return json(route, 405, { detail: 'Method not allowed' })
    }
    const body = (route.request().postDataJSON() ?? {}) as { title?: string }
    return json(route, 201, {
      id: 'c-new',
      title: body.title || 'New Conversation',
      created_at: '2026-01-03T00:00:00Z',
      updated_at: '2026-01-03T00:00:00Z',
    })
  })

  await page.route('**/api/conversations/*/messages', (route) => {
    const url = route.request().url()
    const id = decodeURIComponent(
      url.split('/conversations/')[1]?.split('/')[0] ?? '',
    )
    console.log('FIXTURE: messages requested for', id, 'sentMessages:', sentMessages.length)
    if (conversationMessagesStatus !== 200) {
      return json(route, conversationMessagesStatus, {
        detail: 'Conversation not found',
      })
    }
    // If a message was sent to this conversation, include the assistant response
    const sent = sentMessages.find((s) => s.conversation_id === id)
    if (sent) {
      const msgs = messages[id] ? [...messages[id]] : []
      msgs.push({
        id: 'msg-user-e2e',
        role: 'user',
        content: sent.content,
        conversation_id: id,
        created_at: new Date().toISOString(),
      })
      msgs.push({
        id: 'msg-assistant-1',
        role: 'assistant',
        content: 'Hello world',
        conversation_id: id,
        created_at: new Date().toISOString(),
      })
      console.log('FIXTURE: returning messages with assistant response')
      return json(route, 200, msgs)
    }
    console.log('FIXTURE: returning base messages')
    return json(route, 200, messages[id] ?? [])
  })

  await page.route('**/api/chat/*/messages', async (route) => {
    if (chatDelay > 0) await new Promise((r) => setTimeout(r, chatDelay))
    if (chatStatus !== 200) {
      return json(route, chatStatus, { detail: 'Chat backend unavailable' })
    }
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody(sseChunks, sseMessageId),
    })
  })

  // Durable run command endpoint (return error to force legacy fallback)
  await page.route('**/api/runs/*/turns', (route) => {
    return json(route, 500, { error: { code: 'server_error', message: 'Durable runs unavailable' } })
  })

  // Durable event snapshot endpoint
  await page.route('**/api/conversations/*/snapshot', (route) => {
    return json(route, 200, {
      conversation_id: 'c1',
      sequence: -1,
      events: [],
    })
  })

  // Durable event stream endpoint (emit run.succeeded after a delay)
  await page.route('**/api/conversations/*/events/stream', async (route) => {
    console.log('FIXTURE: event stream requested')
    // Send a run.succeeded event after a brief delay to simulate completion
    await new Promise((r) => setTimeout(r, 500))
    console.log('FIXTURE: sending run.succeeded event')
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: {"id":"evt-succ-1","sequence":1,"type":"run.succeeded","payload":{"message_id":"msg-assistant-1"}}\n\n',
    })
  })
}

/**
 * Sets a test token before the application boots so the router guard allows
 * protected routes. The token is a fixture value, never a real credential.
 */
export async function setAuthToken(
  page: Page,
  token: string = E2E_TOKEN,
): Promise<void> {
  await page.addInitScript((t) => {
    localStorage.setItem('access_token', t)
  }, token)
}

/* ----------------------------------------------------------------------- */
/* Durable-run fixtures (plan Task 10)                                      */
/* ----------------------------------------------------------------------- */

/**
 * The endpoints the portability surface actually uses: a catalog, a submitted
 * turn, a run read, a cancellation, a snapshot, and an event stream. Every
 * response is local and fixed; no provider is contacted and no fixture names
 * a credential.
 *
 * These are registered after `mockApi` rather than inside it, which is what
 * makes them win: Playwright keeps the most recently added handler for a
 * matching URL. A test that wants the durable path opts in, and every other
 * test keeps seeing the legacy fixture it was written against.
 */

export interface E2EProfileItem {
  profile_id: string
  label: string
  adapter: string
  model: string
  destination_label: string | null
  capabilities: Record<string, unknown>
  available: boolean
  reason: string | null
  explanation: string
  allowed_fallback_profile_ids: string[]
}

export interface E2EProfileCatalog {
  default_profile_id: string
  profiles: E2EProfileItem[]
}

export interface E2ERunModelSummary {
  profile_id: string
  label: string
  adapter: string
  requested_model: string
  actual_model: string | null
  destination_label: string | null
  fallback_profile_ids: string[]
  recorded: boolean
}

export interface E2EUsageSummary {
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cached_input_tokens: number | null
  reasoning_tokens: number | null
  source: string | null
}

export interface E2EErrorSummary {
  code: string
  message: string
  retry_after_seconds: number | null
}

export interface E2EAttemptSummary {
  ordinal: number
  profile_id: string
  adapter: string
  requested_model: string
  actual_model: string | null
  destination_label: string | null
  outcome: string
  error_code: string | null
  usage: E2EUsageSummary | null
  started_at: string | null
  ended_at: string | null
}

export interface E2ERun {
  id: string
  conversation_id: string
  status:
    | 'created'
    | 'queued'
    | 'running'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'timed_out'
    | 'stale'
  created_at: string
  attempt: number
  model: E2ERunModelSummary | null
  usage: E2EUsageSummary | null
  error: E2EErrorSummary | null
  provider_attempts: E2EAttemptSummary[]
}

export interface E2EDurableEvent {
  id: string
  conversation_id: string
  run_id: string | null
  sequence: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface E2ESnapshot {
  conversation_id: string
  sequence: number
  events: E2EDurableEvent[]
}

/**
 * What one installation offers. Two it can serve and one it cannot: the
 * unavailable one carries the reason a reader acts on. No fixture here names
 * an endpoint URL, an adapter's host, or a credential.
 */
export const E2E_PROFILES: E2EProfileCatalog = {
  default_profile_id: 'primary-model',
  profiles: [
    {
      profile_id: 'primary-model',
      label: 'Primary model',
      adapter: 'openai',
      model: 'primary-fixture',
      destination_label: 'api.primary.test',
      capabilities: {},
      available: true,
      reason: null,
      explanation: '',
      allowed_fallback_profile_ids: ['backup-model'],
    },
    {
      profile_id: 'backup-model',
      label: 'Backup model',
      adapter: 'openai',
      model: 'backup-fixture',
      destination_label: 'api.backup.test',
      capabilities: {},
      available: true,
      reason: null,
      explanation: '',
      allowed_fallback_profile_ids: [],
    },
    // A different adapter and a different kind of destination. The browser
    // never learns what an adapter is, so a card that reads the same here as
    // it does for the first one is the point: nothing in the browser is
    // OpenAI-shaped.
    {
      profile_id: 'local-model',
      label: 'Local model',
      adapter: 'openai_compatible',
      model: 'local-fixture',
      destination_label: 'localhost:11434',
      capabilities: {},
      available: true,
      reason: null,
      explanation: '',
      allowed_fallback_profile_ids: [],
    },
    {
      profile_id: 'needs-a-key',
      label: 'Needs a key',
      adapter: 'openai',
      model: 'unconfigured-fixture',
      destination_label: null,
      capabilities: {},
      available: false,
      reason: 'credential_missing',
      explanation: 'Not available in this installation',
      allowed_fallback_profile_ids: [],
    },
    // Unavailable for a different reason: this build has no adapter for it.
    // The reason is stated the same way, because the reason is what a reader
    // acts on and the reason is never a guess.
    {
      profile_id: 'no-adapter',
      label: 'No adapter yet',
      adapter: 'anthropic',
      model: 'unshipped-fixture',
      destination_label: null,
      capabilities: {},
      available: false,
      reason: 'adapter_unavailable',
      explanation: 'This build has no adapter for it',
      allowed_fallback_profile_ids: [],
    },
  ],
}

const PRIMARY_MODEL: E2ERunModelSummary = {
  profile_id: 'primary-model',
  label: 'Primary model',
  adapter: 'openai',
  requested_model: 'primary-fixture',
  actual_model: null,
  destination_label: 'api.primary.test',
  fallback_profile_ids: [],
  recorded: true,
}

export const BACKUP_MODEL: E2ERunModelSummary = {
  ...PRIMARY_MODEL,
  profile_id: 'backup-model',
  label: 'Backup model',
  requested_model: 'backup-fixture',
  destination_label: 'api.backup.test',
}

// The same shape for a different adapter and destination. Nothing here is
// OpenAI-shaped: the browser reads a label, a model, and a destination, and
// the card that renders this is the card that renders the first.
export const LOCAL_MODEL: E2ERunModelSummary = {
  profile_id: 'local-model',
  label: 'Local model',
  adapter: 'openai_compatible',
  requested_model: 'local-fixture',
  actual_model: null,
  destination_label: 'localhost:11434',
  fallback_profile_ids: [],
  recorded: true,
}

const PRIMARY_USAGE: E2EUsageSummary = {
  input_tokens: 11,
  output_tokens: 7,
  total_tokens: 18,
  cached_input_tokens: null,
  reasoning_tokens: null,
  source: 'provider',
}

export const E2E_RUN_QUEUED: E2ERun = {
  id: 'run-e2e-1',
  conversation_id: 'c1',
  status: 'queued',
  created_at: '2026-01-01T00:00:00Z',
  attempt: 0,
  model: null,
  usage: null,
  error: null,
  provider_attempts: [],
}

export const E2E_RUN_COMPLETED: E2ERun = {
  ...E2E_RUN_QUEUED,
  status: 'completed',
  model: PRIMARY_MODEL,
  usage: PRIMARY_USAGE,
  provider_attempts: [
    {
      ordinal: 1,
      profile_id: 'primary-model',
      adapter: 'openai',
      requested_model: 'primary-fixture',
      actual_model: null,
      destination_label: 'api.primary.test',
      outcome: 'completed',
      error_code: null,
      usage: PRIMARY_USAGE,
      started_at: '2026-01-01T00:00:01Z',
      ended_at: '2026-01-01T00:00:04Z',
    },
  ],
}

export const E2E_RUN_FAILED: E2ERun = {
  ...E2E_RUN_QUEUED,
  status: 'failed',
  model: PRIMARY_MODEL,
  usage: null,
  error: {
    code: 'rate_limited',
    message:
      'The model provider is busy right now. Try again in a moment.',
    retry_after_seconds: 30,
  },
  provider_attempts: [
    {
      ordinal: 1,
      profile_id: 'primary-model',
      adapter: 'openai',
      requested_model: 'primary-fixture',
      actual_model: null,
      destination_label: 'api.primary.test',
      outcome: 'failed',
      error_code: 'rate_limited',
      usage: null,
      started_at: '2026-01-01T00:00:01Z',
      ended_at: '2026-01-01T00:00:04Z',
    },
  ],
}

export const E2E_RUN_CANCELLED: E2ERun = {
  ...E2E_RUN_QUEUED,
  status: 'cancelled',
  model: PRIMARY_MODEL,
  usage: null,
  error: null,
  provider_attempts: [],
}

/**
 * A run that reached a provider and is still going. This is the state a
 * person acts on when they stop a turn: the run has an id and no end, so
 * the browser has something to cancel and has not invented an answer.
 */
export const E2E_RUN_RUNNING: E2ERun = {
  ...E2E_RUN_QUEUED,
  status: 'running',
  model: PRIMARY_MODEL,
  usage: null,
  error: null,
  provider_attempts: [],
}

/**
 * A turn answered by a different adapter, at a destination of a different
 * kind. It reports no counts, which is the other half of the claim: the
 * card says "Not reported" rather than estimating, whatever the adapter.
 */
export const E2E_RUN_LOCAL_COMPLETED: E2ERun = {
  ...E2E_RUN_QUEUED,
  status: 'completed',
  model: LOCAL_MODEL,
  usage: null,
  error: null,
  provider_attempts: [
    {
      ordinal: 1,
      profile_id: 'local-model',
      adapter: 'openai_compatible',
      requested_model: 'local-fixture',
      actual_model: null,
      destination_label: 'localhost:11434',
      outcome: 'completed',
      error_code: null,
      usage: null,
      started_at: '2026-01-01T00:00:01Z',
      ended_at: '2026-01-01T00:00:04Z',
    },
  ],
}

/**
 * A run read that names the profile a turn was answered by. A test that
 * wants to know whether a browser choice reached the server reads what this
 * returns, because the run read is the only place the browser learns it.
 */
export function runReadNaming(model: E2ERunModelSummary): E2ERun {
  return {
    ...E2E_RUN_COMPLETED,
    model,
    provider_attempts: [
      {
        ordinal: 1,
        profile_id: model.profile_id,
        adapter: model.adapter,
        requested_model: model.requested_model,
        actual_model: model.actual_model,
        destination_label: model.destination_label,
        outcome: 'completed',
        error_code: null,
        usage: PRIMARY_USAGE,
        started_at: '2026-01-01T00:00:01Z',
        ended_at: '2026-01-01T00:00:04Z',
      },
    ],
  }
}

/** One SSE frame in the framing the store reads: `id:`/`event:`/`data:`. */
function durableFrame(event: E2EDurableEvent): string {
  const data: Record<string, unknown> = {
    id: event.id,
    sequence: event.sequence,
    run_id: event.run_id,
    payload: event.payload,
  }
  return (
    `id: ${event.id}\n` +
    `event: ${event.type}\n` +
    `data: ${JSON.stringify(data)}\n\n`
  )
}

/** One event of a conversation's record, so a test can name a whole stream. */
export function durableEvent(
  sequence: number,
  type: string,
  options: { id?: string; conversation_id?: string; run_id?: string | null; payload?: Record<string, unknown> } = {},
): E2EDurableEvent {
  return {
    id: options.id ?? `evt-${sequence}`,
    conversation_id: options.conversation_id ?? 'c1',
    run_id: options.run_id ?? 'run-e2e-1',
    sequence,
    type,
    payload: options.payload ?? {},
    created_at: '2026-01-01T00:00:00Z',
  }
}

export interface DurableApiOptions {
  profilesStatus?: number
  profiles?: E2EProfileCatalog
  turnsStatus?: number
  submittedRun?: E2ERun
  /** What each successive run read reports; the last one repeats. */
  runReads?: E2ERun[]
  cancelStatus?: number
  cancelledRun?: E2ERun
  /** What the messages endpoint says exists before a turn is sent. */
  baseMessages?: Record<string, E2EMessage[]>
  /** What a turn that completed leaves behind. */
  assistantContent?: string
  snapshotEvents?: E2EDurableEvent[]
  streamEvents?: E2EDurableEvent[]
  streamStatus?: number
}

export async function mockDurableApi(
  page: Page,
  options: DurableApiOptions = {},
): Promise<{
  submitted: { conversation_id: string; payload: Record<string, unknown> }[]
  stream: string[]
  cancelled: string[]
}> {
  const {
    profilesStatus = 200,
    profiles = E2E_PROFILES,
    turnsStatus = 201,
    submittedRun = E2E_RUN_QUEUED,
    runReads = [E2E_RUN_COMPLETED],
    cancelStatus = 200,
    cancelledRun = E2E_RUN_CANCELLED,
    baseMessages = {},
    assistantContent = 'Answered by the fixture.',
    snapshotEvents = [],
    streamEvents = [],
    streamStatus = 200,
  } = options

  // What the browser actually asked for. A test that wants to know whether a
  // choice reached the request reads this, rather than inferring it from what
  // the card later shows.
  const capture = {
    submitted: [] as { conversation_id: string; payload: Record<string, unknown> }[],
    stream: [] as string[],
    // Which runs a browser asked to stop. A cancellation a test claims has to
    // be one the browser actually made, of this run.
    cancelled: [] as string[],
  }

  // The end this installation's turn reached. Only a completed one leaves an
  // answer behind — a failed or cancelled turn leaves its own end and nothing
  // else, which is what the messages endpoint has to agree with.
  const terminalReached = (): string | null => {
    const events = [...snapshotEvents, ...streamEvents]
    for (const type of ['run.succeeded', 'run.failed', 'run_cancelled']) {
      if (events.some((event) => event.type === type)) return type
    }
    return null
  }

  const json = (route: Route, status: number, body: unknown): void => {
    route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  }

  const conversationFrom = (url: string, marker: string): string =>
    decodeURIComponent(url.split(marker)[1]?.split('/')[0] ?? '')

  await page.route('**/api/model-profiles', (route) =>
    profilesStatus !== 200
      ? json(route, profilesStatus, { detail: 'Model catalog unavailable' })
      : json(route, 200, profiles),
  )

  // The run read. Registered before the command routes below, which is what
  // lets those win: Playwright keeps the most recently added handler.
  let readIndex = 0
  await page.route('**/api/runs/*', async (route) => {
    const read = runReads[Math.min(readIndex, runReads.length - 1)]
    readIndex += 1
    json(route, 200, read)
  })

  await page.route('**/api/conversations/*/messages', (route) => {
    const conversationId = conversationFrom(route.request().url(), '/conversations/')
    const list = [...(baseMessages[conversationId] ?? [])]
    for (const { conversation_id, payload } of capture.submitted) {
      if (conversation_id !== conversationId) continue
      list.push({
        id: `msg-user-${payload.client_request_id}`,
        role: 'user',
        content: String(payload.content ?? ''),
        conversation_id,
        created_at: '2026-01-01T00:00:00Z',
      })
      // Only a turn that completed leaves an answer. A failed or cancelled
      // one leaves its own end, and the messages endpoint says so too.
      if (terminalReached() === 'run.succeeded') {
        list.push({
          id: 'msg-assistant-fixture',
          role: 'assistant',
          content: assistantContent,
          conversation_id,
          created_at: '2026-01-01T00:00:05Z',
        })
      }
    }
    json(route, 200, list)
  })

  // The record only exists once a turn has been submitted: before that the
  // conversation has nothing to replay, and after a reload the same record
  // is what rebuilds the state. Serving it early would let the snapshot
  // answer for a turn that has not been sent yet.
  await page.route('**/api/conversations/*/snapshot', (route) => {
    const events = capture.submitted.length ? snapshotEvents : []
    return json(route, 200, {
      conversation_id: conversationFrom(route.request().url(), '/conversations/'),
      sequence: events.reduce((max, e) => Math.max(max, e.sequence), -1),
      events,
    })
  })

  // The stream's URL carries an `after_sequence` query, and Playwright's
  // glob ends at the pattern: the trailing wildcard is what lets the query
  // through. Without it the fixture is never asked and the browser sees a
  // 404 instead of the record.
  await page.route('**/api/conversations/*/events/stream**', (route) => {
    capture.stream.push(route.request().url())
    if (streamStatus !== 200) {
      return json(route, streamStatus, { detail: 'Event stream unavailable' })
    }
    return route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: streamEvents.map(durableFrame).join(''),
    })
  })

  await page.route('**/api/runs/*/turns', async (route) => {
    if (turnsStatus !== 201) {
      return json(route, turnsStatus, { detail: 'Turn could not be submitted' })
    }
    const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>
    capture.submitted.push({
      conversation_id: conversationFrom(route.request().url(), '/runs/'),
      payload: body,
    })
    json(route, 201, submittedRun)
  })

  await page.route('**/api/runs/*/cancel', (route) => {
    capture.cancelled.push(conversationFrom(route.request().url(), '/runs/'))
    json(
      route,
      cancelStatus === 200 ? 200 : cancelStatus,
      cancelStatus === 200 ? cancelledRun : { detail: 'Run could not be cancelled' },
    )
  })

  return capture
}
