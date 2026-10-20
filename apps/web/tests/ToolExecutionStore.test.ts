import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from '@/stores/chat'
import api, { capabilitiesApi, runsApi } from '@/services/api'
import type { RunCard, ToolOperationSummary } from '@/types'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  modelProfilesApi: { list: vi.fn() },
  capabilitiesApi: { list: vi.fn() },
  artifactsApi: { list: vi.fn(), download: vi.fn() },
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
const mockRuns = vi.mocked(runsApi)
const mockCapabilities = vi.mocked(capabilitiesApi)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

const emptySnapshot = (conversationId: string) => ({
  conversation_id: conversationId,
  sequence: -1,
  events: [],
  messages: [],
  runs: [],
  artifacts: [],
  artifacts_next_cursor: null,
})

function card(operations: ToolOperationSummary[]): RunCard {
  return {
    run_id: 'run-1',
    kind: 'tool',
    graph_version: 1,
    status: 'running',
    started_at: null,
    ended_at: null,
    usage: null,
    error_code: null,
    error_message: null,
    model: null,
    attempts: [],
    operations,
  }
}

function runningOperation(): ToolOperationSummary {
  return {
    id: 'operation-1',
    run_id: 'run-1',
    conversation_id: 'c1',
    logical_call_id: 'manual-1',
    capability_id: 'brave_web_search',
    capability_version: '1.0.0',
    manifest_digest: 'a'.repeat(64),
    status: 'running',
    error_code: null,
    error: null,
    result: null,
    artifacts: [],
    attempts: [],
    created_at: '',
    updated_at: '',
    finished_at: null,
    next_cursor: null,
  }
}

describe('tool execution state', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    setActivePinia(createPinia())
    mockRuns.getSnapshot.mockImplementation(async (id: string) => ({ data: emptySnapshot(id) } as never))
    mockRuns.getOperations.mockResolvedValue({ data: { items: [], next_cursor: null } } as never)
    mockRuns.getRun.mockResolvedValue({ data: { id: 'run-1', status: 'running' } } as never)
    mockApi.get.mockImplementation(async (url: string) => {
      const id = String(url).match(/^\/conversations\/([^/]+)\/messages$/)?.[1] ?? ''
      return { data: [{ id: `${id}-message`, role: 'user', content: id, conversation_id: id, created_at: '' }] } as never
    })
    mockCapabilities.list.mockResolvedValue({ data: [] } as never)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: new ReadableStream({ start(controller) { controller.close() } }),
    }))
  })

  it('retains and retries the exact manual tool payload', async () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('request-fixed')
    mockRuns.submitTool
      .mockRejectedValueOnce(new Error('network lost'))
      .mockResolvedValueOnce({
        data: { id: 'run-1', conversation_id: 'c1', status: 'queued', kind: 'tool', graph_version: 1 },
      } as never)
    const chat = useChatStore()
    chat.currentConversation = { id: 'c1', title: 'Search', created_at: '', updated_at: '' }
    const command = {
      capability: { id: 'tavily_web_search', version: '1.0.0', manifest_digest: 'b'.repeat(64) },
      arguments: { query: 'terraform modules', max_results: 5 },
    }

    await chat.submitToolSearch('c1', command)
    const [[requestId, pending]] = [...chat.pendingToolRequests.entries()]
    expect(requestId).toBe('request-fixed')
    expect(pending.payload).toEqual({ client_request_id: requestId, ...command })

    await chat.retryToolSubmission(requestId)

    expect(mockRuns.submitTool).toHaveBeenNthCalledWith(2, 'c1', pending.payload)
    expect(chat.pendingToolRequests.has(requestId)).toBe(false)
  })

  it('does not infer operation success from a completion event before the API read confirms it', async () => {
    const confirmed = deferred<{ data: { items: ToolOperationSummary[]; next_cursor: null } }>()
    mockRuns.getOperations.mockReturnValue(confirmed.promise as never)
    const chat = useChatStore()
    const active = runningOperation()
    chat.runCards.set('c1', card([active]))

    chat.processDurableEvent({
      id: 'event-completed',
      conversation_id: 'c1',
      run_id: 'run-1',
      sequence: 3,
      type: 'tool.completed',
      payload: { operation_id: active.id, status: 'succeeded' },
      created_at: '',
    })
    await Promise.resolve()

    expect(chat.runCards.get('c1')?.operations[0].status).toBe('running')
    confirmed.resolve({ data: { items: [{ ...active, status: 'succeeded' }], next_cursor: null } })
    await vi.waitFor(() => expect(chat.runCards.get('c1')?.operations[0].status).toBe('succeeded'))
  })

  it('ignores a delayed snapshot and message response for the previously selected conversation', async () => {
    const oldSnapshot = deferred<{ data: ReturnType<typeof emptySnapshot> }>()
    mockRuns.getSnapshot.mockImplementation((id: string) =>
      id === 'c1' ? oldSnapshot.promise as never : Promise.resolve({ data: emptySnapshot(id) } as never),
    )
    const oldMessages = deferred<{ data: unknown[] }>()
    mockApi.get.mockImplementation((url: string) => {
      if (url === '/conversations/c1/messages') return oldMessages.promise as never
      return Promise.resolve({ data: [{ id: 'c2-message', role: 'user', content: 'c2', conversation_id: 'c2', created_at: '' }] } as never)
    })
    const chat = useChatStore()

    const oldLoad = chat.loadConversation('c1')
    await chat.loadConversation('c2')
    oldSnapshot.resolve({ data: emptySnapshot('c1') })
    await Promise.resolve()
    oldMessages.resolve({ data: [{ id: 'c1-message', role: 'user', content: 'c1', conversation_id: 'c1', created_at: '' }] })
    await oldLoad

    expect(chat.currentConversation?.id).toBe('c2')
    expect(chat.messages.map((message) => message.id)).toEqual(['c2-message'])
  })
})
