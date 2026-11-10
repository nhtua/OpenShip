import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAgentModeStore } from '@/stores/agentMode'
import { useChatStore } from '@/stores/chat'
import api, { preferencesApi, runsApi } from '@/services/api'
import type { AgentMode, AgentPreferences } from '@/types'

// What the account's own preference endpoint does: read it, and compare-and-swap
// it. No test here may let the browser decide a mode the server has not returned.
vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  preferencesApi: { get: vi.fn(), update: vi.fn() },
  runsApi: {
    submitTurn: vi.fn(),
    getSnapshot: vi.fn(),
    getRun: vi.fn(),
    getOperations: vi.fn(),
    cancelRun: vi.fn(),
  },
  conversationsApi: { list: vi.fn(), get: vi.fn() },
  modelProfilesApi: { list: vi.fn() },
  capabilitiesApi: { list: vi.fn() },
}))

const mockApi = vi.mocked(api)
const mockPreferences = vi.mocked(preferencesApi)
const mockRuns = vi.mocked(runsApi)

function preferences(agentMode: AgentMode, revision: number): AgentPreferences {
  return { agent_mode: agentMode, revision }
}

function stalePreference(agentMode: AgentMode, revision: number) {
  return {
    response: {
      status: 409,
      data: {
        detail: {
          error: {
            code: 'stale_agent_preference',
            message: `The preference is now ${agentMode} at revision ${revision}; reload it before saving again`,
          },
        },
      },
    },
  }
}

// A stream that ends with a terminal event, so a submit lane that reaches the
// event stream stops instead of reconnecting. These cases are about which mode
// a send carries, never about transport.
function serveStream(...types: string[]) {
  const encoder = new TextEncoder()
  const frames = types.map((type, index) =>
    `id: evt-${index}\nevent: ${type}\ndata: ${JSON.stringify({ id: `evt-${index}`, conversation_id: 'c1', run_id: 'run-mode', sequence: index, type, payload: {}, created_at: '' })}\n\n`,
  )
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

describe('agent mode store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    mockPreferences.get.mockResolvedValue({ data: preferences('copilot', 4) } as never)
    mockPreferences.update.mockResolvedValue({ data: preferences('auto', 5) } as never)
    mockRuns.submitTurn.mockResolvedValue({ data: { id: 'run-1' } } as never)
    // Chat lanes that reach the run read stay off the wire: these cases are
    // about which mode a send carries, never about the run's own progress.
    mockRuns.getRun.mockResolvedValue({
      data: { id: 'run-1', conversation_id: 'c1', status: 'queued', created_at: '', attempt: 0 },
    } as never)
    mockRuns.getOperations.mockResolvedValue({ data: { items: [], next_cursor: null } } as never)
    mockRuns.getSnapshot.mockResolvedValue({
      data: { conversation_id: 'c1', sequence: -1, events: [], runs: [] },
    } as never)
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('stream offline'))
  })

  it('loadsAccountPreferenceAfterReload', async () => {
    const store = useAgentModeStore()
    await store.load()

    expect(store.mode).toBe('copilot')
    expect(store.revision).toBe(4)
    expect(store.pendingMode).toBe(null)
    expect(store.submissionSelection()).toEqual({ agent_mode: 'copilot', agent_mode_revision: 4 })

    // A reload keeps the token but nothing in memory, and localStorage is never
    // the authority for a mode: the account's preference is read again.
    store.reset()
    expect(store.mode).toBe(null)
    expect(store.submissionSelection()).toBe(null)
    mockPreferences.get.mockResolvedValue({ data: preferences('auto', 7) } as never)
    await store.load()

    expect(store.mode).toBe('auto')
    expect(store.revision).toBe(7)
  })

  it('failedSaveKeepsCommittedModeAndBlocksAmbiguousSend', async () => {
    const mode = useAgentModeStore()
    await mode.load()
    mockPreferences.update.mockRejectedValue({ response: { status: 500 } } as never)

    await mode.save('auto')

    // The save never reached the server, so the account's mode is still the one
    // it returned, and the browser's wish stays visibly unsaved.
    expect(mockPreferences.update).toHaveBeenCalledWith({ agent_mode: 'auto', expected_revision: 4 })
    expect(mode.mode).toBe('copilot')
    expect(mode.pendingMode).toBe('auto')
    expect(mode.submissionSelection()).toBe(null)
    expect(mode.submissionBlocked).toBe(true)
    expect(mode.error).toBe('The agent mode could not be saved. The account still uses Copilot.')

    // An ambiguous mode never drives a task send: the browser does not pick one
    // so that a send can proceed.
    const chat = useChatStore()
    await chat.sendMessage('Chart the supplied observations', 'c1', { enableTools: true })
    expect(chat.error).toBe('Confirm the agent execution mode before sending a task.')
    expect(chat.pendingRequests.size).toBe(0)
    expect(mockRuns.submitTurn).not.toHaveBeenCalled()
  })

  it('staleBrowserGetsConflictNotSilentAutoSend', async () => {
    const mode = useAgentModeStore()
    await mode.load()
    // Another tab saved the account first: this browser's copy is one revision
    // behind, and the server answers with the current preference.
    mockPreferences.update.mockRejectedValue(stalePreference('auto', 5))
    mockPreferences.get.mockResolvedValue({ data: preferences('auto', 5) } as never)

    await mode.save('auto')

    expect(mode.mode).toBe('auto')
    expect(mode.revision).toBe(5)
    expect(mode.pendingMode).toBe(null)
    expect(mode.conflict).toBe(false)
    expect(mode.error).toBe('Another tab saved this preference first. The current account mode has been loaded.')
    // The stale send never happened on the browser's guess.
    expect(mockRuns.submitTurn).not.toHaveBeenCalled()
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('logoutDiscardsLatePriorAccountResponse', async () => {
    const store = useAgentModeStore()
    let resolvePrior!: (value: { data: AgentPreferences }) => void
    mockPreferences.get
      .mockImplementationOnce(() => {
        const pending = new Promise((resolve) => { resolvePrior = resolve })
        return pending as never
      })
    const priorLoad = store.load()
    mockPreferences.get.mockResolvedValue({ data: preferences('auto', 9) } as never)
    const currentLoad = store.load()

    // Signing out (or switching accounts) discards anything the previous
    // account's request may still bring back.
    store.reset()
    await currentLoad
    resolvePrior({ data: preferences('copilot', 1) })
    await priorLoad

    expect(store.mode).toBe(null)
    expect(store.revision).toBe(0)
    expect(store.submissionSelection()).toBe(null)
  })

  it('retryKeepsOriginalModePayload', async () => {
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('stable-mode-request')
    const chat = useChatStore()
    const mode = useAgentModeStore()
    await mode.load()
    chat.currentConversation = { id: 'c1', title: 'Test', created_at: '', updated_at: '', archived_at: null, project_name: 'P', project_archived_at: null }
    serveStream('run.succeeded')
    mockRuns.submitTurn
      .mockRejectedValueOnce(new Error('response lost after server commit'))
      .mockResolvedValue({
        data: { id: 'run-mode', conversation_id: 'c1', status: 'queued', created_at: '', attempt: 0 },
      } as never)

    await chat.sendMessage('Chart the supplied observations', 'c1', { enableTools: true })

    // The account's mode changes while the turn is still pending. A retry is
    // the same turn, so it replays the mode that turn was sent with.
    await mode.save('auto')
    await chat.retrySubmission('stable-mode-request')

    const submitted = mockRuns.submitTurn.mock.calls.map((call) => call[1])
    const [firstPayload, retryPayload] = [submitted[0], submitted[submitted.length - 1]]
    expect(retryPayload).toEqual(firstPayload)
    expect(firstPayload).toMatchObject({
      content: 'Chart the supplied observations',
      client_request_id: 'stable-mode-request',
      enable_tools: true,
      agent_mode: 'copilot',
      agent_mode_revision: 4,
    })
    expect(mode.mode).toBe('auto')
    expect(mode.revision).toBe(5)
  })

  it('switchingToAutoDoesNotConsumeRecoveryDecision', async () => {
    const mode = useAgentModeStore()
    await mode.load()

    await mode.save('auto')

    // Choosing a mode is a preference change and nothing else: it sends no run
    // command, so a pending recovery decision stays exactly as it was.
    expect(mockPreferences.update).toHaveBeenCalledWith({ agent_mode: 'auto', expected_revision: 4 })
    expect(mode.mode).toBe('auto')
    expect(mode.pendingMode).toBe(null)
    expect(mockRuns.submitTurn).not.toHaveBeenCalled()
    expect(mockApi.post).not.toHaveBeenCalled()
    const chat = useChatStore()
    expect(chat.pendingRequests.size).toBe(0)
  })
})
