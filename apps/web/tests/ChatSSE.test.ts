import { describe, it, expect, beforeEach, afterEach, vi, Mock } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'

describe('ChatStore SSE handling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('parses SSE chunk events and accumulates content', async () => {
    const store = useChatStore()
    store.messages = []

    // Mock fetch with SSE response
    const encoder = new TextEncoder()
    const sseData = [
      'data: {"type":"chunk","content":"Hello"}\n\n',
      'data: {"type":"chunk","content":" world"}\n\n',
      'data: {"type":"chunk","content":"!"}\n\n',
      'data: {"type":"complete","message_id":"msg-123"}\n\n',
    ].join('')

    const mockResponse: Response = {
      ok: true,
      status: 200,
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(sseData))
          controller.close()
        },
      }),
    } as Response

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse)

    const conversationId = '550e8400-e29b-41d4-a716-446655440000'
    await store.sendMessage('Hi', conversationId)

    // Verify the messages were accumulated
    expect(store.messages.length).toBeGreaterThan(0)
    const lastMsg = store.messages[store.messages.length - 1]
    expect(lastMsg.role).toBe('assistant')
    expect(lastMsg.content).toContain('Hello')
    expect(lastMsg.content).toContain('world')
    expect(lastMsg.content).toContain('!')
    expect(lastMsg.id).toBe('msg-123')
  })

  it('sets error state on failed response', async () => {
    const store = useChatStore()
    store.messages = []

    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
    } as Response)

    const conversationId = '550e8400-e29b-41d4-a716-446655440000'
    await store.sendMessage('Hi', conversationId)

    // Verify error state
    expect(store.error).toBeTruthy()
    expect(store.isStreaming).toBe(false)
  })
})
