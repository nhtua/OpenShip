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

  it('rendersChunksBeforeCompletion', async () => {
    const store = useChatStore()
    store.messages = []

    // Controlled stream: each enqueued SSE event is delivered in its own
    // read (one chunk per pull), so intermediate state is observable
    // without real timers.
    const encoder = new TextEncoder()
    const queue: Uint8Array[] = []
    const waiters: Array<(item: Uint8Array | null) => void> = []
    let closed = false

    function nextChunk(): Promise<Uint8Array | null> {
      return new Promise((resolve) => {
        const item = queue.shift()
        if (item !== undefined) {
          resolve(item)
        } else if (closed) {
          resolve(null)
        } else {
          waiters.push((woken) =>
            resolve(woken !== undefined ? woken : queue.shift() ?? null),
          )
        }
      })
    }

    const stream = new ReadableStream<Uint8Array>({
      async pull(controller) {
        const item = await nextChunk()
        if (item === null) {
          controller.close()
        } else {
          controller.enqueue(item)
        }
      },
    })

    function enqueue(payload: Record<string, unknown>) {
      const chunk = encoder.encode(`data: ${JSON.stringify(payload)}\n\n`)
      const waiter = waiters.shift()
      if (waiter) {
        waiter(chunk)
      } else {
        queue.push(chunk)
      }
    }

    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: stream,
    } as Response)

    const pending = store.sendMessage('Hi', null)

    // Local user message + assistant placeholder exist before any chunk.
    await vi.waitFor(() => expect(store.messages).toHaveLength(2))
    const last = () => store.messages[store.messages.length - 1]
    expect(last().role).toBe('assistant')
    expect(last().content).toBe('')

    enqueue({ type: 'chunk', content: 'He' })
    await vi.waitFor(() => expect(last().content).toBe('He'))
    expect(store.isStreaming).toBe(true)

    enqueue({ type: 'chunk', content: 'llo' })
    await vi.waitFor(() => expect(last().content).toBe('Hello'))
    expect(store.isStreaming).toBe(true)

    enqueue({ type: 'complete', message_id: 'msg-900' })
    closed = true
    await pending

    expect(last().content).toBe('Hello')
    expect(last().id).toBe('msg-900')
    expect(store.isStreaming).toBe(false)
  })
})
