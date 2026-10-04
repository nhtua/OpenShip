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
    if (conversationMessagesStatus !== 200) {
      return json(route, conversationMessagesStatus, {
        detail: 'Conversation not found',
      })
    }
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
