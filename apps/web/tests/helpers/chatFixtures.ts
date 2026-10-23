import type {
  ChatMessage,
  Conversation,
  ModelProfileCatalog,
  User,
} from '@/types'

export const fixtureUser: User = {
  id: 'u1',
  username: 'testuser',
  email: 'test@example.com',
}

export const fixtureConversations: Conversation[] = [
  {
    id: 'c1',
    project_name: 'Fixture project',
    project_archived_at: null,
    title: 'Deploy pipeline',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T10:00:00Z',
    archived_at: null,
  },
  {
    id: 'c2',
    project_name: 'Fixture project',
    project_archived_at: null,
    title: 'Scale up web',
    created_at: '2026-01-02T00:00:00Z',
    updated_at: '2026-01-02T10:00:00Z',
    archived_at: null,
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
 * What this test installation offers. Two profiles can answer, one cannot:
 * the third states why in a safe reason, never with a credential value or a
 * full endpoint. The default is named, so "no selection" is a claim a reader
 * can check rather than a guess.
 */
export const modelProfileCatalog: ModelProfileCatalog = {
  default_profile_id: 'primary-fixture',
  profiles: [
    {
      profile_id: 'primary-fixture',
      label: 'Fixture primary',
      adapter: 'openai',
      model: 'gpt-6-luna',
      destination_label: 'fixture.provider.test',
      capabilities: { context_tokens: 8192, tool_calling: false },
      available: true,
      reason: null,
      explanation: '',
      allowed_fallback_profile_ids: ['secondary-fixture'],
    },
    {
      profile_id: 'secondary-fixture',
      label: 'Fixture backup',
      adapter: 'openai',
      model: 'backup-model',
      destination_label: 'backup.provider.test',
      capabilities: { context_tokens: 4096, tool_calling: false },
      available: true,
      reason: null,
      explanation: '',
      allowed_fallback_profile_ids: [],
    },
    {
      profile_id: 'offline-fixture',
      label: 'Fixture offline',
      adapter: 'openai',
      model: 'offline-model',
      destination_label: null,
      capabilities: { context_tokens: 8192, tool_calling: false },
      available: false,
      reason: 'missing_credentials',
      explanation: 'this profile needs a credential binding this build lacks',
      allowed_fallback_profile_ids: [],
    },
  ],
}

/**
 * Healthy implementation for the axios instance used by the chat store:
 * serves the conversation list, the model-profile catalog, and per-conversation
 * message endpoints.
 */
export function healthyChatApiGet(url: string): { data: unknown } {
  if (url === '/workspace/conversations') {
    return { data: fixtureConversations.map((c) => ({ ...c })) }
  }
  if (url === '/model-profiles') {
    return { data: modelProfileCatalog }
  }
  const metadataMatch = String(url).match(/^\/conversations\/([^/]+)$/)
  if (metadataMatch) {
    const conversation = fixtureConversations.find((item) => item.id === metadataMatch[1])
    if (!conversation) throw apiError('Conversation not found')
    return { data: { ...conversation } }
  }
  const match = String(url).match(/^\/conversations\/([^/]+)\/messages$/)
  if (match) {
    return { data: (messagesByConversation[match[1]] ?? []).map((m) => ({ ...m })) }
  }
  return { data: [] }
}
