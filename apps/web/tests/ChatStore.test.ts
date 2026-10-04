import { describe, it, expect, beforeEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import api from '@/services/api'
import {
  apiError,
  fixtureConversations,
  healthyChatApiGet,
  messagesByConversation,
} from './helpers/chatFixtures'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
}))

const mockApi = vi.mocked(api)

describe('chat store', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    localStorage.clear()
    vi.clearAllMocks()
    setActivePinia(createPinia())
    mockApi.get.mockImplementation(
      (async (url: string) => healthyChatApiGet(url)) as never,
    )
  })

  it('loadConversationKeepsKnownTitle', async () => {
    const chat = useChatStore()
    await chat.getConversations()
    await chat.loadConversation('c1')

    expect(chat.currentConversation?.id).toBe('c1')
    expect(chat.currentConversation?.title).toBe('Deploy pipeline')
    expect(chat.messages).toEqual(messagesByConversation.c1)
  })

  it('falls back to the title "Conversation" when metadata is unavailable', async () => {
    const chat = useChatStore()
    await chat.loadConversation('unknown-id')
    expect(chat.currentConversation?.title).toBe('Conversation')
  })

  it('failedConversationLoadKeepsPreviousSelection', async () => {
    mockApi.get.mockImplementation(
      (async (url: string) => {
        if (url === '/conversations/c2/messages') {
          throw apiError('Conversation not found')
        }
        return healthyChatApiGet(url)
      }) as never,
    )

    const chat = useChatStore()
    await chat.getConversations()
    await chat.loadConversation('c1')
    expect(chat.currentConversation?.id).toBe('c1')

    await chat.loadConversation('c2')

    expect(chat.error).toBe('Conversation not found')
    // Previous selection and messages are retained on failure.
    expect(chat.currentConversation?.id).toBe('c1')
    expect(chat.messages).toEqual(messagesByConversation.c1)
  })

  it('getConversations stores the loaded list', async () => {
    const chat = useChatStore()
    await chat.getConversations()
    expect(chat.conversations).toEqual(fixtureConversations)
    expect(chat.error).toBeNull()
  })
})
