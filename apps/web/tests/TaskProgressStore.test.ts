import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTaskProgressStore } from '@/stores/taskProgress'
import { taskDecisionApi } from '@/services/api'
import type { RecoveryAction, TaskDecisionCommand } from '@/types'

// The decision POST is the single door every user response takes. The store
// must never post for itself: a choice is posted only when a person chose it,
// and a reconnect or a reload must land the same durable state, never a second
// local copy of the task.
vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  conversationsApi: { list: vi.fn(), get: vi.fn() },
  modelProfilesApi: { list: vi.fn() },
  capabilitiesApi: { list: vi.fn() },
  preferencesApi: { get: vi.fn(), update: vi.fn() },
  runsApi: { submitTurn: vi.fn(), getSnapshot: vi.fn(), getRun: vi.fn(), getOperations: vi.fn(), cancelRun: vi.fn() },
  taskDecisionApi: { submit: vi.fn() },
}))

const mockSubmit = vi.mocked(taskDecisionApi)

type PlanRevision = { revision: number; review_digest: string; steps: Array<{ id: string; intent: string; depends_on?: string[] }> }

const PLAN_ONE: PlanRevision = {
  revision: 1,
  review_digest: 'a'.repeat(64),
  steps: [{ id: 'fetch', intent: 'Fetch the observations' }],
}
const PLAN_TWO: PlanRevision = {
  revision: 2,
  review_digest: 'b'.repeat(64),
  steps: [{ id: 'fetch', intent: 'Fetch the observations' }, { id: 'chart', intent: 'Chart them', depends_on: ['fetch'] }],
}

function event(sequence: number, type: string, payload: Record<string, unknown>, id = `${type}-${sequence}`) {
  return {
    id,
    conversation_id: 'c1',
    run_id: 'run-1',
    sequence,
    type,
    payload,
    created_at: '2026-11-09T10:00:00Z',
  }
}

function planProposed(sequence: number, plan: PlanRevision) {
  return event(sequence, 'task.plan_proposed', {
    schema_version: 1,
    run_id: 'run-1',
    revision: plan.revision,
    mode: 'copilot',
    review_digest: plan.review_digest,
    steps: plan.steps,
  })
}

function progressEvent(sequence: number, revision: number, steps: Array<{ id: string; status: string }>) {
  return event(sequence, 'task.progress', {
    schema_version: 1,
    run_id: 'run-1',
    mode: 'copilot',
    revision,
    status: 'running',
    steps,
    skipped_steps: [],
    verified_outputs: [],
    last_disposition: 'none',
  })
}

describe('task progress store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    mockSubmit.submit.mockResolvedValue({ data: { current_revision: 1, status: 'running' } } as never)
  })

  it('reconnectDoesNotDuplicatePlanOrFinalMessage', async () => {
    const store = useTaskProgressStore()
    const publish = planProposed(5, PLAN_ONE)
    const progress = progressEvent(6, 1, [{ id: 'fetch', status: 'succeeded' }])

    store.handleEvent(publish)
    store.handleEvent(progress)
    // A reconnect replays the stream after a reload: the same events land
    // again, and the card shows one story, not two.
    store.handleEvent({ ...publish, id: 'replay-1' })
    store.handleEvent({ ...progress, id: 'replay-2' })
    // Older sequences never rewind the card.
    store.handleEvent({ ...progress, sequence: 4, id: 'older' })

    const card = store.cards.get('run-1')
    expect(card).toBeDefined()
    expect(card!.planRevisions.map((plan) => plan.revision)).toEqual([1])
    expect(card!.progress.revision).toBe(1)
    expect(card!.progress.steps.map((step) => step.status)).toEqual(['succeeded'])
    expect(store.submitTaskDecision)
    // Nothing a task event carries can masquerade as the run's final
    // message: the store only owns the task card, never chat messages.
    expect(mockSubmit.submit).not.toHaveBeenCalled()
  })

  it('answerAndContextBoundChatUseSameDecisionApi', async () => {
    const store = useTaskProgressStore()
    store.handleEvent(planProposed(1, PLAN_ONE))
    store.handleEvent(event(2, 'task.decision_requested', {
      schema_version: 1,
      run_id: 'run-1',
      revision: 1,
      request_id: 'request-1',
      kind: 'clarification',
      prompt: 'Which source should the chart use?',
      expires_at: '2026-11-10T00:00:00Z',
    }))

    // The button path and the composer path produce the same command: one
    // stable command id per decision and action, bound to the exact request
    // the card shows.
    const typed = store.replyToDecision('run-1', 'The CSV file from the morning shift')
    expect(typed!.action).toBe('answer')
    await store.submitTaskDecision('run-1', typed!)

    expect(mockSubmit.submit).toHaveBeenCalledTimes(1)
    const [runId, command] = mockSubmit.submit.mock.calls[0]
    expect(runId).toBe('run-1')
    expect(command).toEqual({
      client_request_id: command.client_request_id,
      request_id: 'request-1',
      plan_revision: 1,
      expected_digest: 'a'.repeat(64),
      action: 'answer',
      answer: 'The CSV file from the morning shift',
    } as TaskDecisionCommand)
    expect(store.cards.get('run-1')!.pendingDecision!.request_id).toBe('request-1')
  })

  it('unrecordedApprovalIsAskedAgainWithoutResettingProgress', async () => {
    const store = useTaskProgressStore()
    store.handleEvent(planProposed(1, PLAN_ONE))
    store.handleEvent(progressEvent(2, 1, [{ id: 'fetch', status: 'succeeded' }]))
    store.handleEvent(event(3, 'task.decision_requested', {
      schema_version: 1,
      run_id: 'run-1',
      revision: 1,
      request_id: 'request-1',
      kind: 'plan_confirmation',
      prompt: 'Confirm this plan?',
      expires_at: '2026-11-10T00:00:00Z',
    }))
    mockSubmit.submit.mockRejectedValue(({ response: { status: 0 } }) as never)

    // The approval never reached the account: the same request is asked
    // again, and completed work is not restarted.
    await store.submitTaskDecision('run-1', store.commandFor('run-1', 'confirm')!)
    const card = store.cards.get('run-1')
    expect(card!.pendingDecision!.request_id).toBe('request-1')
    expect(card!.progress.steps.map((step) => step.status)).toEqual(['succeeded'])
    expect(card!.decisionError).toBe('The choice could not be sent. The same request will be asked again.')

    const retry = store.commandFor('run-1', 'confirm')
    expect(retry!.client_request_id).toMatch(/^[0-9a-f-]{36}$/)
    await store.submitTaskDecision('run-1', retry!)
    const ids = mockSubmit.submit.mock.calls.map((call) => (call[1] as TaskDecisionCommand).client_request_id)
    expect(new Set(ids).size).toBe(1)
  })

  it('does not send a second choice while the same decision is being submitted', async () => {
    const store = useTaskProgressStore()
    store.handleEvent(planProposed(1, PLAN_ONE))
    store.handleEvent(event(2, 'task.decision_requested', {
      revision: 1, request_id: 'request-1', kind: 'plan_confirmation', prompt: 'Approve this plan?',
    }))
    let finish!: (result: { data: { status: string } }) => void
    mockSubmit.submit.mockImplementation(() => new Promise((resolve) => { finish = resolve }) as never)

    const first = store.buildCommand('run-1', 'confirm')!
    const sending = store.submitTaskDecision('run-1', first)
    expect(store.cards.get('run-1')?.submitting).toBe(true)
    expect(store.buildCommand('run-1', 'reject')).toBeNull()
    await store.submitTaskDecision('run-1', { ...first, action: 'reject' })
    expect(mockSubmit.submit).toHaveBeenCalledTimes(1)

    finish({ data: { status: 'running' } })
    await sending
    expect(store.buildCommand('run-1', 'reject')).toBeNull()
    store.handleEvent(event(3, 'task.decision_resolved', { request_id: 'request-1', revision: 1, state: 'confirmed' }))
    expect(store.cards.get('run-1')?.submitting).toBe(false)
  })

  it('keeps the durable question on a snapshot-only reload or after a partial event', () => {
    const store = useTaskProgressStore()
    store.handleEvent(planProposed(1, PLAN_ONE))
    store.handleEvent(event(2, 'task.decision_requested', {
      revision: 1, request_id: 'request-1', kind: 'clarification',
    }))
    store.handleSnapshot({ schema_version: 1, run_id: 'run-1', revision: 1,
      pending_decision: { request_id: 'request-1', plan_revision: 1, kind: 'clarification', prompt: 'Which file should I chart?' },
    })
    expect(store.cards.get('run-1')?.pendingDecision?.prompt).toBe('Which file should I chart?')
  })

  it('accepts decision resolution after a snapshot-only reload without replaying older events', () => {
    const store = useTaskProgressStore()
    store.handleSnapshot({ schema_version: 1, run_id: 'run-1', revision: 1,
      plan: PLAN_ONE, status: 'waiting',
      pending_decision: { request_id: 'request-1', plan_revision: 1, kind: 'plan_confirmation', expected_digest: 'a'.repeat(64), prompt: 'Approve?' },
    }, 10)
    store.handleEvent(event(9, 'task.decision_resolved', { request_id: 'request-1', revision: 1, state: 'confirmed' }))
    expect(store.cards.get('run-1')?.pendingDecision?.request_id).toBe('request-1')
    store.handleEvent(event(11, 'task.decision_resolved', { request_id: 'request-1', revision: 1, state: 'confirmed' }))
    expect(store.cards.get('run-1')?.pendingDecision).toBeNull()
    expect(store.cards.get('run-1')?.resolvedDecisions).toHaveLength(1)
  })

  it('keeps a replayed decision request after a snapshot-only task load', () => {
    const store = useTaskProgressStore()
    store.handleSnapshot({ schema_version: 1, run_id: 'run-replay', revision: 1,
      plan: PLAN_ONE, status: 'waiting',
      pending_decision: { request_id: 'request-1', plan_revision: 1, kind: 'plan_confirmation', expected_digest: 'a'.repeat(64), prompt: 'Approve?' },
    }, 5)
    store.handleEvent(planProposed(1, PLAN_ONE))
    store.handleEvent(event(5, 'task.decision_requested', { revision: 1, request_id: 'request-1', kind: 'plan_confirmation', prompt: 'Approve?', expires_at: '2026-11-10T00:00:00Z' }))
    expect(store.cards.get('run-replay')?.pendingDecision?.request_id).toBe('request-1')
    expect(store.buildCommand('run-replay', 'confirm')).toBeTypeOf('object')
    expect(store.cards.get('run-replay')?.currentRevision).toBe(1)
  })

  it('keeps a decision request that arrives after a cursor-less task read', () => {
    const store = useTaskProgressStore()
    // The run read is one the browser makes after the stream closed: it says
    // what the plan was, and the park had not committed yet, so it names no
    // open decision. The decision event is the next thing the browser learns.
    store.handleSnapshot({ schema_version: 1, run_id: 'run-1', revision: 1,
      plan: PLAN_ONE, status: 'running', mode: 'copilot',
    })
    store.handleEvent(planProposed(5, PLAN_ONE))
    store.handleEvent(event(6, 'task.decision_requested', {
      revision: 1, request_id: 'request-1', kind: 'plan_confirmation',
      prompt: 'Approve the plan?', expires_at: '2026-11-12T00:00:00Z',
    }))
    expect(store.cards.get('run-1')?.pendingDecision?.request_id).toBe('request-1')
    expect(store.buildCommand('run-1', 'confirm')).toBeTypeOf('object')
    expect(store.cards.get('run-1')?.currentRevision).toBe(1)
  })

  it('does not lose a replayed decision request when the task card is snapshot-only', () => {
    const store = useTaskProgressStore()
    store.handleSnapshot({ schema_version: 1, run_id: 'run-old-snapshot', revision: 1,
      plan: PLAN_ONE, status: 'waiting',
      pending_decision: { request_id: 'request-1', plan_revision: 1, kind: 'plan_confirmation', expected_digest: 'a'.repeat(64) },
    })
    store.handleEvent(planProposed(1, PLAN_ONE))
    store.handleEvent(event(5, 'task.decision_requested', { revision: 1, request_id: 'request-1', kind: 'plan_confirmation', prompt: 'Approve?', expires_at: '2026-11-10T00:00:00Z' }))
    expect(store.cards.get('run-old-snapshot')?.pendingDecision?.request_id).toBe('request-1')
    expect(store.buildCommand('run-old-snapshot', 'confirm')).toBeTypeOf('object')
  })

  it('recoveryCommandEchoesTheRecoveryDigestNotThePlanDigest', async () => {
    const store = useTaskProgressStore()
    store.handleEvent(planProposed(1, PLAN_TWO))
    store.handleEvent(event(2, 'task.decision_requested', {
      schema_version: 1,
      run_id: 'run-1',
      revision: 2,
      request_id: 'request-1',
      kind: 'recovery',
      prompt: 'The fetch may have run without completing. Choose how to carry on.',
      expires_at: '2026-11-10T00:00:00Z',
      diagnosis: {
        plan_revision: 2,
        plan_digest: 'b'.repeat(64),
        step_id: 'fetch',
        attempt: 1,
        explanation: 'The fetch ended without publishing its output.',
        unknowns: ['Whether the command finished after the stream stopped'],
        risks: ['Repeating the step may fetch the same source twice'],
        blockers: [],
        eligible_actions: ['abort', 'repeat_step', 'continue_next'] as RecoveryAction[],
        recommended_action: 'continue_next' as RecoveryAction,
        digest: 'd'.repeat(64),
      },
    }))

    const card = store.cards.get('run-1')
    const recovery = store.commandFor('run-1', 'repeat_step')
    expect(recovery!.expected_digest).toBe(card!.pendingDecision!.expected_digest)
    expect(recovery!.expected_digest).toBe('d'.repeat(64))
  })

  it('markReadOnlyIsIdempotentSoAViewWatchingTheCardStopsUpdating', () => {
    // ChatView watches the card's read-only state and marks it on every
    // change it sees. A mark that rewrote the card when nothing changed made
    // that watcher re-run forever, and the task conversation never rendered.
    const store = useTaskProgressStore()
    store.handleEvent(planProposed(1, PLAN_ONE))
    const before = store.cards.get('run-1')
    store.markReadOnly('run-1', false)
    expect(store.cards.get('run-1')).toBe(before)
    store.markReadOnly('run-1', true)
    const marked = store.cards.get('run-1')
    expect(marked).not.toBe(before)
    expect(marked!.readOnly).toBe(true)
    store.markReadOnly('run-1', true)
    expect(store.cards.get('run-1')).toBe(marked)
  })
})
