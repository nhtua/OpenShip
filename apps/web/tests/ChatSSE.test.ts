import { describe, it, expect, beforeEach, afterEach, vi, Mock } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import { runsApi } from '@/services/api'

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

const mockRunsApi = vi.mocked(runsApi)

describe('ChatStore durable event handling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('handles turn_submitted and creates run card', async () => {
    const store = useChatStore()
    store.currentConversation = {
      id: 'conv-1',
      title: 'Test',
      created_at: '',
      updated_at: '',
    }
    store.messages = []

    // Simulate turn_submitted event
    const event = {
      id: 'evt-1',
      conversation_id: 'conv-1',
      run_id: 'run-1',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello', client_request_id: 'req-1' },
      created_at: '2026-10-04T00:00:00Z',
    }
    store.processDurableEvent(event as never)

    // Run card should be created
    const card = store.runCards.get('conv-1')
    expect(card).toBeTruthy()
    expect(card!.status).toBe('queued')
    expect(card!.run_id).toBe('run-1')
  })

  it('updates run card status on run.started', async () => {
    const store = useChatStore()
    store.currentConversation = {
      id: 'conv-1',
      title: 'Test',
      created_at: '',
      updated_at: '',
    }
    store.messages = []

    // Create initial run card via turn_submitted
    const submitEvent = {
      id: 'evt-1',
      conversation_id: 'conv-1',
      run_id: 'run-1',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello', client_request_id: 'req-1' },
      created_at: '2026-10-04T00:00:00Z',
    }
    store.processDurableEvent(submitEvent as never)

    // Then the run starts
    const startEvent = {
      id: 'evt-2',
      conversation_id: 'conv-1',
      run_id: 'run-1',
      sequence: 1,
      type: 'run.started',
      payload: {},
      created_at: '2026-10-04T00:00:01Z',
    }
    store.processDurableEvent(startEvent as never)

    const card = store.runCards.get('conv-1')
    expect(card!.status).toBe('running')
  })

  it('updates run card status on run.succeeded', async () => {
    const store = useChatStore()
    store.currentConversation = {
      id: 'conv-1',
      title: 'Test',
      created_at: '',
      updated_at: '',
    }
    store.messages = []

    // Create initial run card via turn_submitted
    const submitEvent = {
      id: 'evt-3',
      conversation_id: 'conv-1',
      run_id: 'run-1',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello', client_request_id: 'req-1' },
      created_at: '2026-10-04T00:00:00Z',
    }
    store.processDurableEvent(submitEvent as never)

    // Then succeed it
    const successEvent = {
      id: 'evt-4',
      conversation_id: 'conv-1',
      run_id: 'run-1',
      sequence: 1,
      type: 'run.succeeded',
      payload: { message_id: 'msg-1' },
      created_at: '2026-10-04T00:00:01Z',
    }
    store.processDurableEvent(successEvent as never)

    const card = store.runCards.get('conv-1')
    expect(card!.status).toBe('succeeded')
  })

  it('handles run.failed', async () => {
    const store = useChatStore()
    store.currentConversation = {
      id: 'conv-1',
      title: 'Test',
      created_at: '',
      updated_at: '',
    }
    store.messages = []

    const submitEvent = {
      id: 'evt-5',
      conversation_id: 'conv-1',
      run_id: 'run-2',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello', client_request_id: 'req-2' },
      created_at: '2026-10-04T00:00:00Z',
    }
    store.processDurableEvent(submitEvent as never)

    const failEvent = {
      id: 'evt-6',
      conversation_id: 'conv-1',
      run_id: 'run-2',
      sequence: 1,
      type: 'run.failed',
      payload: { error: 'Something broke' },
      created_at: '2026-10-04T00:00:01Z',
    }
    store.processDurableEvent(failEvent as never)

    const card = store.runCards.get('conv-1')
    expect(card!.status).toBe('failed')
  })

  it('handles run_cancelled', async () => {
    const store = useChatStore()
    store.currentConversation = {
      id: 'conv-1',
      title: 'Test',
      created_at: '',
      updated_at: '',
    }
    store.messages = []

    const submitEvent = {
      id: 'evt-7',
      conversation_id: 'conv-1',
      run_id: 'run-3',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello', client_request_id: 'req-3' },
      created_at: '2026-10-04T00:00:00Z',
    }
    store.processDurableEvent(submitEvent as never)

    const cancelEvent = {
      id: 'evt-8',
      conversation_id: 'conv-1',
      run_id: 'run-3',
      sequence: 1,
      type: 'run_cancelled',
      payload: { status: 'cancelled' },
      created_at: '2026-10-04T00:00:01Z',
    }
    store.processDurableEvent(cancelEvent as never)

    const card = store.runCards.get('conv-1')
    expect(card!.status).toBe('cancelled')
  })

  it('deduplicates events by event ID', async () => {
    const store = useChatStore()
    store.currentConversation = {
      id: 'conv-1',
      title: 'Test',
      created_at: '',
      updated_at: '',
    }
    store.messages = []

    const event = {
      id: 'evt-9',
      conversation_id: 'conv-1',
      run_id: 'run-4',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello', client_request_id: 'req-4' },
      created_at: '2026-10-04T00:00:00Z',
    }

    // Process same event twice
    store.processDurableEvent(event as never)
    store.processDurableEvent(event as never)

    // Should only create one user message
    expect(store.messages.filter((m) => m.role === 'user').length).toBe(1)
    // Only one run card
    expect(store.runCards.size).toBe(1)
  })

  it('ignores events for non-current conversation', async () => {
    const store = useChatStore()
    store.currentConversation = null
    store.messages = []

    const event = {
      id: 'evt-10',
      conversation_id: 'conv-2',
      run_id: 'run-5',
      sequence: 0,
      type: 'turn_submitted',
      payload: { content: 'Hello', client_request_id: 'req-5' },
      created_at: '2026-10-04T00:00:00Z',
    }

    store.processDurableEvent(event as never)

    // No messages added for non-current conversation
    expect(store.messages.length).toBe(0)
  })
})
