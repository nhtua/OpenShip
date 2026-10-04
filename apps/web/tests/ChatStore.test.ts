import { describe, it, expect, beforeEach, vi, Mock } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import api, { runsApi } from '@/services/api'
import {
  apiError,
  fixtureConversations,
  healthyChatApiGet,
  messagesByConversation,
} from './helpers/chatFixtures'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  runsApi: {
    submitTurn: vi.fn(),
    cancelRun: vi.fn(),
    getSnapshot: vi.fn(),
    getRun: vi.fn(),
  },
}))

const mockApi = vi.mocked(api)
const mockRunsApi = vi.mocked(runsApi)

describe('chat store', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    localStorage.clear()
    vi.clearAllMocks()
    setActivePinia(createPinia())
    mockApi.get.mockImplementation(
      (async (url: string) => healthyChatApiGet(url)) as never,
    )
    mockApi.post.mockResolvedValue({ data: null })
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

  it('sendMessage creates a durable run and subscribes to events', async () => {
    // Mock UUID generation
    const mockUuid = 'req-uuid-123'
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(mockUuid)

    const mockRunId = 'run-abc-456'
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: mockRunId,
        conversation_id: 'c1',
        status: 'queued',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = []

    // Mock SSE stream (empty — no events)
    const mockStream = new ReadableStream({
      start(controller) {
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    await chat.sendMessage('Hello', 'c1')

    // Verify the run command was posted with a client_request_id
    expect(mockRunsApi.submitTurn).toHaveBeenCalledWith('c1', {
      content: 'Hello',
      client_request_id: mockUuid,
    })

    // Verify user message was stored with server-assigned ID (after run response)
    // The store should have a pending draft that gets resolved
    expect(chat.messages.length).toBeGreaterThan(0)
    expect(chat.messages[0].role).toBe('user')
    expect(chat.messages[0].content).toBe('Hello')

    // Verify SSE subscription was opened with the conversation's cursor
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/events/stream?after_sequence='),
      expect.anything(),
    )
  })

  it('deduplicates events by event ID', async () => {
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-dedup-789',
        conversation_id: 'c1',
        status: 'queued',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = []

    // Emit duplicate SSE events with same ID
    const encoder = new TextEncoder()
    let eventsEmitted = 0
    const mockStream = new ReadableStream({
      start(controller) {
        const event = 'data: {"id":"evt-1","sequence":1,"type":"turn_submitted","payload":{"client_request_id":"req-123"}}\n\n'
        controller.enqueue(encoder.encode(event))
        eventsEmitted++
        // Duplicate with same ID
        controller.enqueue(encoder.encode(event))
        eventsEmitted++
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    await chat.sendMessage('Hello', 'c1')

    // Despite duplicate SSE frames, the store should only apply once
    expect(eventsEmitted).toBe(2)
    // Only one user message should exist
    expect(chat.messages.filter((m) => m.role === 'user').length).toBe(1)
  })

  it('loadConversation fetches snapshot and rebuilds state', async () => {
    // Mock snapshot response
    const snapshotEvents = [
      {
        id: 'evt-1',
        conversation_id: 'c1',
        run_id: 'run-1',
        sequence: 0,
        type: 'turn_submitted',
        payload: { client_request_id: 'req-1', content: 'Hello' },
        created_at: '2026-10-04T00:00:00Z',
      },
    ]
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1',
        sequence: 0,
        events: snapshotEvents,
      },
    })

    const chat = useChatStore()
    await chat.loadConversation('c1')

    // Verify snapshot was fetched
    expect(mockRunsApi.getSnapshot).toHaveBeenCalledWith('c1')
    // Verify cursor was stored
    expect(chat.cursors.get('c1')).toBe(0)
  })

  it('maintains cursor per conversation and resumes from last position', async () => {
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-cursor-1',
        conversation_id: 'c1',
        status: 'queued',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = []

    // First send: cursor starts at -1
    const mockStream1 = new ReadableStream({
      start(controller) {
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: mockStream1,
    } as Response)

    await chat.sendMessage('First', 'c1')
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('after_sequence=-1'),
      expect.anything(),
    )

    // Update cursor after processing an event
    chat.cursors.set('c1', 5)

    // Second send: should resume from cursor 5
    const mockStream2 = new ReadableStream({
      start(controller) {
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: mockStream2,
    } as Response)

    await chat.sendMessage('Second', 'c1')
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('after_sequence=5'),
      expect.anything(),
    )
  })

  it('does not show duplicate user or assistant messages on reconnect', async () => {
    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }

    // Simulate existing messages loaded from server
    chat.messages = [
      { id: 'msg-1', role: 'user', content: 'Hello', conversation_id: 'c1', created_at: '' },
      { id: 'msg-2', role: 'assistant', content: 'Hi!', conversation_id: 'c1', created_at: '' },
    ]

    // Reconnect: receive SSE events for the same messages
    // turn_submitted event should not create a new user message
    const encoder = new TextEncoder()
    const mockStream = new ReadableStream({
      start(controller) {
        // Event for the existing user message
        const turnEvent = 'data: {"id":"evt-1","sequence":0,"type":"turn_submitted","payload":{"content":"Hello"}}\n\n'
        controller.enqueue(encoder.encode(turnEvent))
        // Event for the existing assistant message (complete)
        const completeEvent = 'data: {"id":"evt-2","sequence":1,"type":"run.succeeded","payload":{"message_id":"msg-2"}}\n\n'
        controller.enqueue(encoder.encode(completeEvent))
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    // Process reconnect stream
    chat.subscribeToEvents('c1', -1, () => {})

    // Should still have exactly 2 messages, no duplicates
    expect(chat.messages.length).toBe(2)
    expect(chat.messages.filter((m) => m.role === 'user').length).toBe(1)
    expect(chat.messages.filter((m) => m.role === 'assistant').length).toBe(1)
  })

  it('replaces optimistic temp message ID with server ID on turn_submitted', async () => {
    const mockUuid = 'req-uuid-789'
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(mockUuid)

    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-confirmed-1',
        conversation_id: 'c1',
        status: 'queued',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = []

    // Send message — creates optimistic temp message
    const encoder = new TextEncoder()
    let resolveStream: (() => void) | null = null
    const mockStream = new ReadableStream({
      start(controller) {
        resolveStream = () => {
          // Server confirms with turn_submitted event including client_request_id
          const event = `data: {"id":"evt-confirm-1","sequence":0,"type":"turn_submitted","payload":{"client_request_id":"${mockUuid}"}}\n\n`
          controller.enqueue(encoder.encode(event))
          controller.close()
        }
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    const sendPromise = chat.sendMessage('Hello', 'c1')

    // Wait a tick for optimistic message to be created
    await new Promise((r) => setTimeout(r, 10))
    // Temp message should exist
    expect(chat.messages[0].id).toBe(`temp-${mockUuid}`)

    // Server confirms
    resolveStream!()
    await sendPromise

    // Temp ID should be replaced with server-derived ID
    expect(chat.messages[0].id).toBe('user-evt-confirm-1')
  })

  it('handles SSE out-of-order events without duplication', async () => {
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-ooo-1',
        conversation_id: 'c1',
        status: 'queued',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = []

    const encoder = new TextEncoder()
    const mockStream = new ReadableStream({
      start(controller) {
        // Send run.started before turn_submitted (out of order)
        const started = 'data: {"id":"evt-1","sequence":1,"type":"run.started","payload":{}}\n\n'
        controller.enqueue(encoder.encode(started))
        // Then turn_submitted (should still be processed)
        const submitted = 'data: {"id":"evt-0","sequence":0,"type":"turn_submitted","payload":{"content":"Hello"}}\n\n'
        controller.enqueue(encoder.encode(submitted))
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    await chat.sendMessage('Hello', 'c1')

    // Only one user message despite out-of-order events
    expect(chat.messages.filter((m) => m.role === 'user').length).toBe(1)
    // Run card should be in running state (last status update wins)
    expect(chat.runCards.get('c1')?.status).toBe('running')
  })

  it('does not show false completed card when stream reconnects mid-run', async () => {
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-mid-1',
        conversation_id: 'c1',
        status: 'running',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = []

    const encoder = new TextEncoder()
    const mockStream = new ReadableStream({
      start(controller) {
        // Simulate: stream starts, run is running, stream disconnects
        // (no run.succeeded event ever received)
        const submitted = 'data: {"id":"evt-1","sequence":0,"type":"turn_submitted","payload":{"content":"Hello"}}\n\n'
        controller.enqueue(encoder.encode(submitted))
        const started = 'data: {"id":"evt-2","sequence":1,"type":"run.started","payload":{}}\n\n'
        controller.enqueue(encoder.encode(started))
        // Stream ends without completion — run is still in progress
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    await chat.sendMessage('Hello', 'c1')

    // Run card should NOT show succeeded/failed/cancelled
    const card = chat.runCards.get('c1')
    expect(card).toBeTruthy()
    expect(['succeeded', 'failed', 'cancelled']).not.toContain(card?.status)
    expect(card?.status).toBe('running')
  })

  it('switching conversations during stream does not mix messages', async () => {
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-switch-1',
        conversation_id: 'c1',
        status: 'queued',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()

    // Send in c1
    chat.currentConversation = { id: 'c1', title: 'Conv 1', created_at: '', updated_at: '' }
    chat.messages = []
    const encoder = new TextEncoder()
    const mockStream = new ReadableStream({
      start(controller) {
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    await chat.sendMessage('Hello c1', 'c1')

    // Switch to c2 mid-stream
    chat.currentConversation = { id: 'c2', title: 'Conv 2', created_at: '', updated_at: '' }
    chat.messages = [
      { id: 'm1', role: 'user', content: 'Existing c2 msg', conversation_id: 'c2', created_at: '' },
    ]

    // Process event for c1 while c2 is current
    const c1Event = {
      id: 'evt-c1-1',
      conversation_id: 'c1',
      run_id: 'run-switch-1',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello c1' },
      created_at: '2026-10-04T00:00:00Z',
    }
    chat.processDurableEvent(c1Event as never)

    // c2 messages should be unchanged
    expect(chat.messages.length).toBe(1)
    expect(chat.messages[0].content).toBe('Existing c2 msg')
  })

  it('tab reload after partial model answer rebuilds from snapshot', async () => {
    // Simulate: user sent message, got partial response, tab reloaded
    // Snapshot contains turn_submitted and run.started but not run.succeeded

    const snapshotEvents = [
      {
        id: 'evt-1',
        conversation_id: 'c1',
        run_id: 'run-reload-1',
        sequence: 0,
        type: 'turn_submitted',
        payload: { content: 'Hello', client_request_id: 'req-old' },
        created_at: '2026-10-04T00:00:00Z',
      },
      {
        id: 'evt-2',
        conversation_id: 'c1',
        run_id: 'run-reload-1',
        sequence: 1,
        type: 'run.started',
        payload: {},
        created_at: '2026-10-04T00:00:01Z',
      },
    ]
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1',
        sequence: 1,
        events: snapshotEvents,
      },
    })

    const chat = useChatStore()
    await chat.loadConversation('c1')

    // User message should be reconstructed from snapshot
    expect(chat.messages.filter((m) => m.role === 'user').length).toBe(1)
    // Run card should show running (not completed)
    expect(chat.runCards.get('c1')?.status).toBe('running')
    expect(chat.cursors.get('c1')).toBe(1)
  })
})
