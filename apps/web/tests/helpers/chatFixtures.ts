import type { ChatMessage, Conversation, User } from '@/types'

export const fixtureUser: User = {
  id: 'u1',
  username: 'testuser',
  email: 'test@example.com',
}

export const fixtureConversations: Conversation[] = [
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

export const messagesByConversation: Record<string, ChatMessage[]> = {
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

/**
 * axios-style error with a `response.data.detail` payload, matching what
 * the api interceptor rejects with.
 */
export function apiError(detail: string): Error {
  return Object.assign(new Error(detail), {
    response: { data: { detail } },
  })
}

/**
 * Healthy implementation for the axios instance used by the chat store:
 * serves the conversation list and per-conversation message endpoints.
 */
export function healthyChatApiGet(url: string): { data: unknown } {
  if (url === '/workspace/conversations') {
    return { data: fixtureConversations.map((c) => ({ ...c })) }
  }
  const match = String(url).match(/^\/conversations\/([^/]+)\/messages$/)
  if (match) {
    return { data: (messagesByConversation[match[1]] ?? []).map((m) => ({ ...m })) }
  }
  return { data: [] }
}
