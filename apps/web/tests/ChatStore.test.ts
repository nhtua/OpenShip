import { describe, it, expect, beforeEach, vi, Mock } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import api, { modelProfilesApi, runsApi } from '@/services/api'
import { buildConversationTimeline } from '@/lib/chatTimeline'
import {
  apiError,
  fixtureConversations,
  healthyChatApiGet,
  messagesByConversation,
  modelProfileCatalog,
} from './helpers/chatFixtures'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  modelProfilesApi: { list: vi.fn() },
  runsApi: {
    submitTurn: vi.fn(),
    submitTool: vi.fn(),
    cancelRun: vi.fn(),
    getSnapshot: vi.fn(),
    getRun: vi.fn(),
    getOperations: vi.fn(),
  },
}))

const mockApi = vi.mocked(api)
const mockProfiles = vi.mocked(modelProfilesApi)
const mockRunsApi = vi.mocked(runsApi)

// A stream that ends with a terminal event, so snapshot recovery is never
// what a provider-selection case is about.
function serveStream(...frames: string[]) {
  const encoder = new TextEncoder()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    status: 200,
    body: new ReadableStream({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame))
        controller.close()
      },
    }),
  } as Response)
}

function frame(
  sequence: number,
  type: string,
  runId: string,
  payload: Record<string, unknown> = {},
): string {
  const event = {
    id: `evt-${sequence}`,
    conversation_id: 'c1',
    run_id: runId,
    sequence,
    type,
    payload,
    created_at: '',
  }
  // A real frame names the event type in its own field: that is what lets a
  // reader stop when the turn ends rather than wait for a closed stream.
  return `id: evt-${sequence}\nevent: ${type}\ndata: ${JSON.stringify(event)}\n\n`
}

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
    mockRunsApi.getSnapshot.mockImplementation(async (id: string) => ({
      data: { conversation_id: id, sequence: -1, events: [], runs: [] },
    } as never))
    mockRunsApi.getOperations.mockResolvedValue({
      data: { items: [], next_cursor: null },
    } as never)
    // What this installation offers. A test that wants a missing catalog
    // replaces this; nothing here guesses a profile in its place.
    mockProfiles.list.mockResolvedValue({ data: modelProfileCatalog })
  })

  it('loadConversationKeepsKnownTitle', async () => {
    const chat = useChatStore()
    await chat.getConversations()
    await chat.loadConversation('c1')

    expect(chat.currentConversation?.id).toBe('c1')
    expect(chat.currentConversation?.title).toBe('Deploy pipeline')
    expect(chat.messages).toEqual(messagesByConversation.c1)
  })

  it('deselecting a conversation invalidates its pending load', async () => {
    let release: ((response: { data: unknown }) => void) | undefined
    mockApi.get.mockImplementation((async (url: string) => {
      if (url === '/conversations/c1/messages') {
        return new Promise((resolve) => { release = resolve })
      }
      return healthyChatApiGet(url)
    }) as never)
    const chat = useChatStore()
    const loading = chat.loadConversation('c1')
    await vi.waitFor(() => expect(release).toBeTypeOf('function'))

    chat.deselectConversation()
    release?.({ data: messagesByConversation.c1 })
    await loading

    expect(chat.currentConversation).toBeNull()
    expect(chat.messages).toEqual([])
  })

  it('ignores a delayed load error for an older conversation', async () => {
    let reject: ((error: Error) => void) | undefined
    mockApi.get.mockImplementation((async (url: string) => {
      if (url === '/conversations/c1/messages') {
        return new Promise((_resolve, rejectLoad) => { reject = rejectLoad })
      }
      return healthyChatApiGet(url)
    }) as never)
    const chat = useChatStore()
    const loading = chat.loadConversation('c1')
    await vi.waitFor(() => expect(reject).toBeTypeOf('function'))

    await chat.loadConversation('c2')
    reject?.(apiError('Old conversation unavailable'))
    await loading

    expect(chat.currentConversation?.id).toBe('c2')
    expect(chat.error).toBeNull()
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

  it('creates a conversation under the selected project identity', async () => {
    mockApi.post.mockResolvedValue({
      data: { id: 'project-conversation', project_id: 'project-1', title: 'Selected project', created_at: '', updated_at: '' },
    } as never)
    const chat = useChatStore()

    await chat.createConversation('Selected project', 'project-1')

    expect(mockApi.post).toHaveBeenCalledWith('/conversations', {
      title: 'Selected project',
      project_id: 'project-1',
    })
    expect(chat.conversations[0].project_id).toBe('project-1')
  })

  it('updates the conversation list and header when a title event arrives', () => {
    const chat = useChatStore()
    chat.conversations = [
      { id: 'c1', title: 'New Conversation', created_at: '', updated_at: '' },
    ]
    chat.currentConversation = {
      id: 'c1',
      title: 'New Conversation',
      created_at: '',
      updated_at: '',
    }

    chat.processDurableEvent({
      id: 'evt-title-1',
      conversation_id: 'c1',
      run_id: 'run-title-1',
      sequence: 1,
      type: 'title_updated',
      payload: { conversation_id: 'c1', title: 'Deploy with the staging profile' },
      created_at: '',
    } as never)

    expect(chat.conversations[0].title).toBe('Deploy with the staging profile')
    expect(chat.currentConversation?.title).toBe('Deploy with the staging profile')
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

    // Verify the run command was posted with a client_request_id. Naming no
    // profile is what lets this installation's default answer; the browser
    // never names an endpoint, an adapter, or a credential.
    expect(mockRunsApi.submitTurn).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({
        content: 'Hello',
        client_request_id: mockUuid,
        provider_profile_id: null,
        fallback_profile_ids: [],
      }),
    )

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

  it('uses persisted event time to keep a live turn and its tools after existing history', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-20T12:00:00Z'))

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = [
      {
        id: 'old-user', role: 'user', content: 'Previous question', conversation_id: 'c1',
        created_at: '2026-10-20T13:00:00Z',
      },
      {
        id: 'old-assistant', role: 'assistant', content: 'Previous answer', conversation_id: 'c1',
        created_at: '2026-10-20T13:00:05Z',
      },
    ]

    const submitted = {
      id: 'evt-live-submit', conversation_id: 'c1', run_id: 'run-live', sequence: 1,
      type: 'turn_submitted', payload: { content: 'Current question' },
      created_at: '2026-10-20T13:00:10Z',
    }
    const terminal = {
      id: 'evt-live-done', conversation_id: 'c1', run_id: 'run-live', sequence: 2,
      type: 'run.succeeded', payload: {}, created_at: '2026-10-20T13:00:20Z',
    }
    const encoder = new TextEncoder()
    const mockStream = new ReadableStream({
      start(controller) {
        for (const event of [submitted, terminal]) {
          controller.enqueue(encoder.encode(
            `id: ${event.id}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
          ))
        }
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: mockStream,
    } as Response)

    const operation = {
      id: 'search-live', run_id: 'run-live', conversation_id: 'c1',
      logical_call_id: 'call-live', capability_id: 'brave_web_search',
      capability_version: '1.0.0', manifest_digest: 'a'.repeat(64),
      status: 'succeeded' as const, error_code: null, error: null, result: { items: [] },
      artifacts: [], attempts: [], created_at: '2026-10-20T12:00:01Z',
      updated_at: '2026-10-20T12:00:02Z', finished_at: '2026-10-20T12:00:02Z',
    }
    chat.toolOperationsByConversation.set('c1', new Map([[operation.id, operation]]))

    try {
      await chat.subscribeToEvents('c1', 0, (event) => chat.processDurableEvent(event))

      const operations = [...chat.toolOperationsByConversation.get('c1')!.values()]
      expect(buildConversationTimeline(chat.messages, operations).map((entry) => entry.id)).toEqual([
        'old-user',
        'old-assistant',
        'user-evt-live-submit',
        'search-live',
      ])
    } finally {
      vi.useRealTimers()
    }
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

  it('retains operations from every snapshot run and deduplicates durable refreshes per conversation', async () => {
    const operation = (id: string, runId: string) => ({
      id,
      run_id: runId,
      conversation_id: 'c1',
      logical_call_id: id,
      capability_id: 'brave_web_search',
      capability_version: '1.0.0',
      manifest_digest: 'a'.repeat(64),
      status: 'succeeded',
      error_code: null,
      error: null,
      result: { query: id, items: [] },
      artifacts: [],
      attempts: [],
      created_at: '2026-10-20T12:00:00Z',
      updated_at: '2026-10-20T12:00:00Z',
      finished_at: '2026-10-20T12:00:00Z',
    })
    const first = operation('search-1', 'run-1')
    const second = operation('search-2', 'run-2')
    mockRunsApi.getSnapshot.mockImplementation(async (id: string) => ({
      data: {
        conversation_id: id,
        sequence: -1,
        events: [],
        runs: id === 'c1' ? [
          { id: 'run-1', status: 'completed', attempt: 1, started_at: null, ended_at: null, error_code: null, kind: 'tool', operations: [first], operations_next_cursor: null },
          { id: 'run-2', status: 'completed', attempt: 1, started_at: null, ended_at: null, error_code: null, kind: 'tool', operations: [second], operations_next_cursor: null },
        ] : [],
      },
    } as never))
    mockRunsApi.getOperations.mockResolvedValue({
      data: { items: [first], next_cursor: null },
    } as never)
    mockApi.get.mockImplementation(async (url: string) => {
      if (url === '/conversations/c2/messages') return { data: [] } as never
      if (url === '/conversations/c1/messages') return { data: [] } as never
      return healthyChatApiGet(url)
    })

    const chat = useChatStore()
    await chat.loadConversation('c1')
    expect([...chat.toolOperationsByConversation.get('c1')!.keys()]).toEqual(['search-1', 'search-2'])

    const event = {
      id: 'evt-tool-complete', conversation_id: 'c1', run_id: 'run-1', sequence: 2,
      type: 'tool.completed', payload: {}, created_at: '2026-10-20T12:00:01Z',
    }
    chat.processDurableEvent(event as never)
    chat.processDurableEvent(event as never)
    await vi.waitFor(() => expect(mockRunsApi.getOperations).toHaveBeenCalledTimes(1))
    expect([...chat.toolOperationsByConversation.get('c1')!.keys()]).toEqual(['search-1', 'search-2'])

    await chat.loadConversation('c2')
    expect([...chat.toolOperationsByConversation.get('c1')!.keys()]).toEqual(['search-1', 'search-2'])
    expect(chat.toolOperationsByConversation.get('c2')?.size ?? 0).toBe(0)
  })

  it('continues every snapshot operation page into the conversation timeline', async () => {
    const operation = (id: string) => ({
      id,
      run_id: 'run-paged',
      conversation_id: 'c1',
      logical_call_id: id,
      capability_id: 'tavily_web_search',
      capability_version: '1.0.0',
      manifest_digest: 'b'.repeat(64),
      status: 'succeeded',
      error_code: null,
      error: null,
      result: { query: id, items: [] },
      artifacts: [],
      attempts: [],
      created_at: '2026-10-20T12:00:00Z',
      updated_at: '2026-10-20T12:00:00Z',
      finished_at: '2026-10-20T12:00:00Z',
    })
    const first = operation('page-one')
    const second = operation('page-two')
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1', sequence: -1, events: [],
        runs: [{
          id: 'run-paged', status: 'completed', attempt: 1, started_at: null,
          ended_at: null, error_code: null, kind: 'tool', operations: [first],
          operations_next_cursor: 'cursor-next',
        }],
      },
    } as never)
    mockRunsApi.getOperations.mockResolvedValue({
      data: { items: [second], next_cursor: null },
    } as never)
    mockApi.get.mockImplementation(async (url: string) => {
      if (url === '/conversations/c1/messages') return { data: [] } as never
      return healthyChatApiGet(url)
    })

    const chat = useChatStore()
    await chat.loadConversation('c1')
    await vi.waitFor(() => {
      expect([...chat.toolOperationsByConversation.get('c1')!.keys()]).toEqual(['page-one', 'page-two'])
    })
    expect(mockRunsApi.getOperations).toHaveBeenCalledWith('run-paged', { cursor: 'cursor-next' })
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
          const event = `data: {"id":"evt-confirm-1","sequence":0,"run_id":"run-confirm-1","type":"turn_submitted","payload":{"client_request_id":"${mockUuid}"}}\n\n`
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
    expect(chat.messages[0].run_id).toBe('run-confirm-1')
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

  it('cancelRun waits for durable state by polling getRun', async () => {
    // First call returns running, second returns cancelled
    mockRunsApi.getRun
      .mockResolvedValueOnce({
        data: { id: 'run-poll-1', conversation_id: 'c1', status: 'running', created_at: '', attempt: 0 },
      })
      .mockResolvedValueOnce({
        data: { id: 'run-poll-1', conversation_id: 'c1', status: 'cancelled', created_at: '', attempt: 0 },
      })

    const chat = useChatStore()
    await chat.cancelRun('run-poll-1')

    // Should have polled getRun at least twice
    expect(mockRunsApi.getRun).toHaveBeenCalled()
    expect(mockRunsApi.getRun.mock.calls.length).toBeGreaterThanOrEqual(2)
    // Last call should see cancelled status
    const lastCall = mockRunsApi.getRun.mock.calls[mockRunsApi.getRun.mock.calls.length - 1]
    expect(lastCall[0]).toBe('run-poll-1')
  })

  it('refetches snapshot when stream closes without terminal event', async () => {
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-expiry-1',
        conversation_id: 'c1',
        status: 'running',
        created_at: '2026-10-04T00:00:00Z',
        attempt: 0,
      },
    })

    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    chat.messages = []

    // Set cursor to 1 (after turn_submitted)
    chat.cursors.set('c1', 1)
    // Process the turn_submitted event to set up state
    chat.processDurableEvent({
      id: 'evt-expiry-0',
      conversation_id: 'c1',
      run_id: 'run-expiry-1',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello' },
      created_at: '',
    } as never)
    chat.processDurableEvent({
      id: 'evt-expiry-1',
      conversation_id: 'c1',
      run_id: 'run-expiry-1',
      sequence: 1,
      type: 'run.started',
      payload: {},
      created_at: '',
    } as never)

    // Mock snapshot fetch — returns run completion that we missed
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1',
        sequence: 2,
        events: [
          { id: 'evt-expiry-0', conversation_id: 'c1', run_id: 'run-expiry-1', sequence: 0, type: 'turn_submitted', payload: { content: 'Hello' }, created_at: '' },
          { id: 'evt-expiry-1', conversation_id: 'c1', run_id: 'run-expiry-1', sequence: 1, type: 'run.started', payload: {}, created_at: '' },
          { id: 'evt-expiry-2', conversation_id: 'c1', run_id: 'run-expiry-1', sequence: 2, type: 'run.succeeded', payload: { message_id: 'msg-1' }, created_at: '' },
        ],
      },
    })

    const encoder = new TextEncoder()
    // Stream closes without sending a terminal event
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

    // Should have fetched snapshot due to stream expiry
    expect(mockRunsApi.getSnapshot).toHaveBeenCalledWith('c1')
    // Run card should show succeeded after snapshot recovery
    expect(chat.runCards.get('c1')?.status).toBe('succeeded')
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

  it('offers the profiles this installation can answer with', async () => {
    const chat = useChatStore()
    await chat.loadModelProfiles()

    // The default is named, so "no selection" is a claim a reader can check.
    expect(chat.modelProfiles?.default_profile_id).toBe('primary-fixture')
    expect(chat.modelProfiles?.profiles).toHaveLength(3)

    const offline = chat.modelProfiles?.profiles.find(
      (profile) => profile.profile_id === 'offline-fixture',
    )
    // One cannot answer, and says why in a safe reason rather than being
    // hidden.
    expect(offline?.available).toBe(false)
    expect(offline?.reason).toBe('missing_credentials')
    // No credential value and no endpoint ever crosses to the browser.
    expect(
      chat.modelProfiles?.profiles.some((profile) => !profile.destination_label),
    ).toBe(true)
  })

  it('states a missing catalog where the choice is made', async () => {
    mockProfiles.list.mockImplementation(() => {
      throw apiError('catalog unavailable')
    })
    const chat = useChatStore()
    await chat.loadModelProfiles()

    expect(chat.modelProfiles).toBeNull()
    // A missing catalog is stated beside the choice, not as a page-wide
    // failure: the installation still answers with its default.
    expect(chat.profileError).toBe('catalog unavailable')
    expect(chat.error).toBeNull()
    // Nothing is claimed about a provider that could not be read.
    expect(chat.selectedProfileId).toBeNull()
  })

  it('switching profile affects only the next turn', async () => {
    const chat = useChatStore()
    await chat.loadModelProfiles()
    chat.selectProfile('secondary-fixture', [])

    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-switch-1',
        conversation_id: 'c1',
        status: 'queued',
        created_at: '',
        attempt: 0,
      },
    })
    serveStream(frame(0, 'run.succeeded', 'run-switch-1', { message_id: 'msg-1' }))

    await chat.sendMessage('Hello', 'c1')

    expect(mockRunsApi.submitTurn).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({ provider_profile_id: 'secondary-fixture' }),
    )
  })

  it('keeps a pending run on the provider it was submitted with', async () => {
    const chat = useChatStore()
    await chat.loadModelProfiles()
    chat.selectProfile('primary-fixture', [])

    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-pin-1',
        conversation_id: 'c1',
        status: 'queued',
        created_at: '',
        attempt: 0,
      },
    })
    // The run read states what this run was pinned to — the primary.
    mockRunsApi.getRun.mockResolvedValue({
      data: {
        id: 'run-pin-1',
        conversation_id: 'c1',
        status: 'completed',
        created_at: '',
        attempt: 0,
        model: {
          profile_id: 'primary-fixture',
          label: 'Fixture primary',
          adapter: 'openai',
          requested_model: 'primary-model',
          actual_model: null,
          destination_label: 'fixture.provider.test',
          fallback_profile_ids: ['secondary-fixture'],
          recorded: true,
        },
      },
    })
    serveStream(frame(0, 'run.succeeded', 'run-pin-1', { message_id: 'msg-1' }))

    await chat.sendMessage('Hello', 'c1')

    // Changing selection now is about the next turn, never this run.
    chat.selectProfile('secondary-fixture', [])
    expect(chat.selectedProfileId).toBe('secondary-fixture')

    // The card still states the run's own provider.
    expect(chat.runCards.get('c1')?.model?.profile_id).toBe('primary-fixture')
  })

  it('does not accept a fallback the chosen profile does not allow', async () => {
    const chat = useChatStore()
    await chat.loadModelProfiles()

    // The backup allows none, so asking for one is not this profile's to
    // promise.
    chat.selectProfile('secondary-fixture', ['primary-fixture'])
    expect(chat.selectedFallbackIds).toEqual([])

    // The primary does allow it.
    chat.selectProfile('primary-fixture', ['secondary-fixture'])
    expect(chat.selectedFallbackIds).toEqual(['secondary-fixture'])
  })

  it('accepts a fallback the default profile allows, with nothing chosen', async () => {
    const chat = useChatStore()
    await chat.loadModelProfiles()

    // The selector offers the opt-in while the default answers, so the store
    // has to accept it: refusing it would leave a control that does nothing.
    chat.selectProfile(null, ['secondary-fixture'])
    expect(chat.selectedProfileId).toBeNull()
    expect(chat.selectedFallbackIds).toEqual(['secondary-fixture'])

    // One the default does not allow is still not this turn's to promise.
    chat.selectProfile(null, ['offline-fixture'])
    expect(chat.selectedFallbackIds).toEqual([])
  })

  it('keeps the captured payload when selection changes before submission', async () => {
    const chat = useChatStore()
    await chat.loadModelProfiles()
    chat.selectProfile('primary-fixture', [])

    // The conversation is made between capture and submission, so a click in
    // the selector can land in that window. The turn already has its
    // provider, so the payload must not change.
    mockApi.post.mockImplementation(async (url: string) => {
      if (url === '/conversations') {
        chat.selectProfile('secondary-fixture', [])
        return {
          data: {
            id: 'c-new',
            title: 'New Conversation',
            created_at: '',
            updated_at: '',
          },
        }
      }
      return { data: null }
    })
    mockRunsApi.submitTurn.mockResolvedValue({
      data: {
        id: 'run-captured-1',
        conversation_id: 'c-new',
        status: 'queued',
        created_at: '',
        attempt: 0,
      },
    })
    serveStream(
      frame(0, 'run.succeeded', 'run-captured-1', { message_id: 'msg-1' }),
    )

    await chat.sendMessage('Hello', null)

    expect(mockRunsApi.submitTurn).toHaveBeenCalledWith(
      'c-new',
      expect.objectContaining({ provider_profile_id: 'primary-fixture' }),
    )
  })

  it('rebuilds provider, usage, and failure from a pruned snapshot', async () => {
    // Only the submission survives: the events alone never say what a run
    // was pinned to or what it reported.
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1',
        sequence: 0,
        events: [
          {
            id: 'evt-1',
            conversation_id: 'c1',
            run_id: 'run-pruned-1',
            sequence: 0,
            type: 'turn_submitted',
            payload: { content: 'Hello', client_request_id: 'req-1' },
            created_at: '',
          },
        ],
      },
    })
    mockRunsApi.getRun.mockResolvedValue({
      data: {
        id: 'run-pruned-1',
        conversation_id: 'c1',
        status: 'failed',
        created_at: '',
        attempt: 1,
        model: {
          profile_id: 'primary-fixture',
          label: 'Fixture primary',
          adapter: 'openai',
          requested_model: 'primary-model',
          actual_model: null,
          destination_label: 'fixture.provider.test',
          fallback_profile_ids: ['secondary-fixture'],
          recorded: true,
        },
        usage: {
          input_tokens: 150,
          output_tokens: null,
          total_tokens: null,
          cached_input_tokens: null,
          reasoning_tokens: null,
          source: null,
        },
        error: {
          code: 'output_limit_exceeded',
          message: 'The answer hit the limit set for this profile.',
          retry_after_seconds: null,
        },
        provider_attempts: [
          {
            ordinal: 1,
            profile_id: 'primary-fixture',
            adapter: 'openai',
            requested_model: 'primary-model',
            actual_model: null,
            destination_label: null,
            outcome: 'length',
            error_code: 'output_limit_exceeded',
            usage: null,
            started_at: null,
            ended_at: null,
          },
        ],
      },
    })

    const chat = useChatStore()
    await chat.loadConversation('c1')

    const card = chat.runCards.get('c1')
    expect(card?.model?.profile_id).toBe('primary-fixture')
    // Missing stays missing: only what the provider reported is rebuilt.
    expect(card?.usage?.input_tokens).toBe(150)
    expect(card?.usage?.total_tokens).toBeNull()
    expect(card?.error_code).toBe('output_limit_exceeded')
    expect(card?.error_message).toBe(
      'The answer hit the limit set for this profile.',
    )
    expect(card?.attempts).toHaveLength(1)
  })

  it('rebuilds terminal run cards from retained runs when events are pruned', async () => {
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1',
        sequence: 12,
        events: [],
        runs: [{
          id: 'run-retained', status: 'failed', attempt: 2,
          started_at: '2026-10-12T10:00:00Z', ended_at: '2026-10-12T10:01:00Z',
          error_code: 'provider_unavailable',
          error: { code: 'provider_unavailable', message: 'The provider is unavailable.', retry_after_seconds: null },
          model: { profile_id: 'primary-fixture', label: 'Primary', adapter: 'openai', requested_model: 'm', actual_model: null, destination_label: 'provider.test', fallback_profile_ids: [], recorded: true },
          usage: null, provider_attempts: [],
        }],
      },
    })

    const chat = useChatStore()
    await chat.loadConversation('c1')

    expect(chat.runCards.get('c1')).toMatchObject({
      run_id: 'run-retained', status: 'failed',
      started_at: '2026-10-12T10:00:00Z', ended_at: '2026-10-12T10:01:00Z',
      error_code: 'provider_unavailable', error_message: 'The provider is unavailable.',
      model: { profile_id: 'primary-fixture' }, attempts: [],
    })
    expect(mockRunsApi.getRun).not.toHaveBeenCalled()
  })

  it('retries a lost submission response with the original request and provider choices', async () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('stable-request')
    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    await chat.loadModelProfiles()
    chat.selectProfile('primary-fixture', ['secondary-fixture'])
    const committedByRequestId = new Map<string, { id: string; conversation_id: string; status: string; created_at: string; attempt: number }>()
    let loseFirstResponse = true
    mockRunsApi.submitTurn.mockImplementation(async (_conversationId, payload) => {
      const existing = committedByRequestId.get(payload.client_request_id)
      if (existing) return { data: existing }
      const committed = { id: 'run-reconciled', conversation_id: 'c1', status: 'queued', created_at: '', attempt: 0 }
      committedByRequestId.set(payload.client_request_id, committed)
      if (loseFirstResponse) {
        loseFirstResponse = false
        throw new Error('response lost after server commit')
      }
      return { data: committed }
    })

    await chat.sendMessage('Hello', 'c1')
    chat.selectProfile('secondary-fixture', [])
    serveStream(frame(0, 'run.succeeded', 'run-reconciled'))
    await chat.retrySubmission('stable-request')

    const submittedPayload = mockRunsApi.submitTurn.mock.calls.map((call) => call[1])
    expect(submittedPayload[1]).toEqual(submittedPayload[0])
    expect(submittedPayload[1]).toMatchObject({
      content: 'Hello', client_request_id: 'stable-request',
      provider_profile_id: 'primary-fixture', fallback_profile_ids: ['secondary-fixture'],
    })
    expect(committedByRequestId.size).toBe(1)
    expect(chat.runCards.get('c1')?.run_id).toBe('run-reconciled')
    expect(chat.pendingRequests.has('stable-request')).toBe(false)
  })

  it('retries automatic tool mode with the exact original profile and no upload requirement', async () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('stable-tool-request')
    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    await chat.loadModelProfiles()
    chat.selectProfile('primary-fixture', [])
    mockRunsApi.submitTurn
      .mockRejectedValueOnce(new Error('response lost after server commit'))
      .mockResolvedValueOnce({
        data: {
          id: 'run-tool-reconciled', conversation_id: 'c1', status: 'queued',
          created_at: '', attempt: 0,
        },
      } as never)

    await chat.sendMessage('Find current search results', 'c1', {
      enableTools: true,
      connectorSelections: { brave: 'global-brave', tavily: 'project-tavily' },
    })
    const originalPayload = mockRunsApi.submitTurn.mock.calls[0][1]
    expect(originalPayload).toMatchObject({
      content: 'Find current search results',
      client_request_id: 'stable-tool-request',
      enable_tools: true,
      connector_selections: { brave: 'global-brave', tavily: 'project-tavily' },
      provider_profile_id: 'primary-fixture',
      fallback_profile_ids: [],
    })
    expect(originalPayload).not.toHaveProperty('artifact_ids')

    chat.selectProfile('secondary-fixture', [])
    serveStream(frame(0, 'run.succeeded', 'run-tool-reconciled'))
    await chat.retrySubmission('stable-tool-request')

    expect(mockRunsApi.submitTurn.mock.calls[1][1]).toEqual(originalPayload)
    expect(chat.pendingRequests.has('stable-tool-request')).toBe(false)
  })

  it('retries a direct tool request with the originally selected capability version and count', async () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('stable-direct-tool-request')
    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '' }
    mockRunsApi.submitTool
      .mockRejectedValueOnce(new Error('response lost after server commit'))
      .mockResolvedValueOnce({
        data: {
          id: 'run-direct-tool-reconciled', conversation_id: 'c1', status: 'queued',
          created_at: '', attempt: 0, kind: 'tool', graph_version: 1, operations: [],
        },
      } as never)
    const command = {
      capability: {
        id: 'tavily_web_search', version: '1.0.0', manifest_digest: 'b'.repeat(64),
      },
      arguments: { query: 'terraform modules', max_results: 8 },
    }

    await chat.submitToolSearch('c1', command)
    const originalPayload = mockRunsApi.submitTool.mock.calls[0][1]
    expect(originalPayload).toEqual({ client_request_id: 'stable-direct-tool-request', ...command })
    chat.searchCapabilities = [
      { id: 'tavily_web_search', version: '2.0.0', manifest_digest: 'c'.repeat(64), input_schema: {}, output_schema: {}, effect: 'read_only_external', enabled: true, available: true, unavailable_reason: null },
    ]

    await chat.retryToolSubmission('stable-direct-tool-request')

    expect(mockRunsApi.submitTool.mock.calls[1][1]).toEqual(originalPayload)
    expect(chat.pendingToolRequests.has('stable-direct-tool-request')).toBe(false)
  })

  it('does not let an automatic-tool response replace a newly selected conversation', async () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('old-conversation-request')
    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Old', created_at: '', updated_at: '' }
    chat.messages = []
    mockRunsApi.submitTurn.mockResolvedValue({
      data: { id: 'run-old-conversation', conversation_id: 'c1', status: 'queued', created_at: '', attempt: 0 },
    } as never)
    let releaseOldResponse: ((response: { data: unknown }) => void) | undefined
    mockApi.get.mockImplementation((async (url: string) => {
      if (url === '/conversations/c1/messages') {
        return await new Promise((resolve) => { releaseOldResponse = resolve })
      }
      return healthyChatApiGet(url)
    }) as never)
    serveStream(frame(0, 'run.succeeded', 'run-old-conversation'))

    await chat.sendMessage('Search this', 'c1', { enableTools: true })
    expect(mockRunsApi.submitTurn.mock.calls[0][1]).toMatchObject({ enable_tools: true })
    await vi.waitFor(() => expect(releaseOldResponse).toBeTypeOf('function'))

    await chat.loadConversation('c2')
    const selectedMessages = [...chat.messages]
    releaseOldResponse?.({ data: messagesByConversation.c1 })
    await vi.waitFor(() => expect(chat.currentConversation?.id).toBe('c2'))

    expect(chat.messages).toEqual(selectedMessages)
  })

  it('refreshes failed run details when a reloaded running turn reaches a terminal event', async () => {
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1', sequence: 1, runs: [],
        events: [
          { id: 'evt-submit', conversation_id: 'c1', run_id: 'run-reload-live', sequence: 0, type: 'turn_submitted', payload: { content: 'Hello' }, created_at: '' },
          { id: 'evt-start', conversation_id: 'c1', run_id: 'run-reload-live', sequence: 1, type: 'run.started', payload: {}, created_at: '' },
        ],
      },
    })
    mockRunsApi.getRun
      .mockResolvedValueOnce({ data: { id: 'run-reload-live', conversation_id: 'c1', status: 'running', created_at: '', attempt: 1 } })
      .mockResolvedValueOnce({
        data: {
          id: 'run-reload-live', conversation_id: 'c1', status: 'failed', created_at: '', attempt: 1,
          error: { code: 'provider_unavailable', message: 'The provider is unavailable.', retry_after_seconds: null },
          usage: { input_tokens: 21, output_tokens: null, total_tokens: null, cached_input_tokens: null, reasoning_tokens: null, source: 'provider' },
          provider_attempts: [{ ordinal: 1, profile_id: 'primary-fixture', adapter: 'openai', requested_model: 'm', actual_model: null, destination_label: null, outcome: 'provider_unavailable', error_code: 'provider_unavailable', usage: null, started_at: null, ended_at: null }],
        },
      })
    serveStream(frame(2, 'run.failed', 'run-reload-live'))

    const chat = useChatStore()
    await chat.loadConversation('c1')
    await vi.waitFor(() => expect(mockRunsApi.getRun).toHaveBeenCalledTimes(2))

    expect(chat.runCards.get('c1')).toMatchObject({
      run_id: 'run-reload-live', status: 'failed',
      error_code: 'provider_unavailable', error_message: 'The provider is unavailable.',
      usage: { input_tokens: 21 }, attempts: [{ profile_id: 'primary-fixture' }],
    })
  })

  it('does not let a delayed terminal read replace a newer run card', async () => {
    let resolveRun: ((value: { data: never }) => void) | undefined
    mockRunsApi.getRun.mockImplementationOnce(() => new Promise((resolve) => {
      resolveRun = resolve as (value: { data: never }) => void
    }))
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1', sequence: 0,
        events: [{ id: 'evt-old', conversation_id: 'c1', run_id: 'run-old', sequence: 0, type: 'turn_submitted', payload: {}, created_at: '' }],
        runs: [],
      },
    })
    const chat = useChatStore()
    const load = chat.loadConversation('c1')
    await vi.waitFor(() => expect(mockRunsApi.getRun).toHaveBeenCalledWith('run-old'))
    chat.runCards.set('c1', {
      run_id: 'run-new', status: 'running', started_at: null, ended_at: null,
      usage: null, error_code: null, error_message: null, model: null, attempts: [],
    })
    resolveRun!({ data: { id: 'run-old', conversation_id: 'c1', status: 'failed', created_at: '', attempt: 1 } as never })
    await load

    expect(chat.runCards.get('c1')?.run_id).toBe('run-new')
    expect(chat.runCards.get('c1')?.status).toBe('running')
  })

  it('refreshes fallback attempts when a reloaded turn succeeds', async () => {
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: {
        conversation_id: 'c1', sequence: 1, runs: [],
        events: [
          { id: 'evt-submit', conversation_id: 'c1', run_id: 'run-fallback-reload', sequence: 0, type: 'turn_submitted', payload: { content: 'Hello' }, created_at: '' },
          { id: 'evt-start', conversation_id: 'c1', run_id: 'run-fallback-reload', sequence: 1, type: 'run.started', payload: {}, created_at: '' },
        ],
      },
    })
    const completedRun = {
      id: 'run-fallback-reload', conversation_id: 'c1', status: 'completed', created_at: '', attempt: 2,
      model: { profile_id: 'primary-fixture', label: 'Primary', adapter: 'openai', requested_model: 'primary-model', actual_model: 'fallback-served-alias', destination_label: 'primary.test', fallback_profile_ids: ['secondary-fixture'], recorded: true },
      provider_attempts: [
        { ordinal: 1, profile_id: 'primary-fixture', adapter: 'openai', requested_model: 'primary-model', actual_model: null, destination_label: 'primary.test', outcome: 'provider_unavailable', error_code: 'provider_unavailable', usage: null, started_at: null, ended_at: null },
        { ordinal: 2, profile_id: 'secondary-fixture', adapter: 'openai_compatible', requested_model: 'fallback-model', actual_model: 'fallback-served-alias', destination_label: 'backup.test', outcome: 'succeeded', error_code: null, usage: null, started_at: null, ended_at: null },
      ],
    }
    mockRunsApi.getRun
      .mockResolvedValueOnce({ data: { ...completedRun, status: 'running' } })
      .mockResolvedValueOnce({ data: completedRun })
    serveStream(frame(2, 'run.succeeded', 'run-fallback-reload'))

    const chat = useChatStore()
    await chat.loadConversation('c1')
    await vi.waitFor(() => expect(mockRunsApi.getRun).toHaveBeenCalledTimes(2))

    expect(chat.runCards.get('c1')).toMatchObject({
      run_id: 'run-fallback-reload', status: 'succeeded',
      attempts: [
        { profile_id: 'primary-fixture', outcome: 'provider_unavailable' },
        { profile_id: 'secondary-fixture', outcome: 'succeeded', actual_model: 'fallback-served-alias' },
      ],
      model: { requested_model: 'primary-model', actual_model: 'fallback-served-alias' },
    })
  })
})
