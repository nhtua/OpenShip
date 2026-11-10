import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { useTaskProgressStore } from '@/stores/taskProgress'
import { taskDecisionApi } from '@/services/api'
import TaskDecision from '@/components/chat/TaskDecision.vue'
import type { RecoveryAction, TaskCardState, TaskSummary } from '@/types'

// A decision card only ever offers what the records say is offerable, and a
// click sends a command bound to the exact request, revision and digest the
// card shows. Nothing here may choose for the reader: a suggestion is a label,
// and a bare "yes" in reply context is not a choice among recovery options.
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

const PLAN_ONE_REVIEW = 'a'.repeat(64)
const PLAN_TWO_REVIEW = 'b'.repeat(64)
const DIAGNOSIS_DIGEST = 'd'.repeat(64)

function card(pending: Record<string, unknown> | null, currentRevision = 1): TaskCardState {
  return {
    runId: 'run-1',
    sequence: 3,
    currentRevision,
    planRevisions: [
      { revision: 1, review_digest: PLAN_ONE_REVIEW, steps: [{ id: 'fetch', intent: 'Fetch the observations' }] },
    ],
    progress: {
      schema_version: 1,
      run_id: 'run-1',
      mode: 'copilot',
      revision: currentRevision,
      status: 'waiting',
      steps: [{ id: 'fetch', status: 'pending', attempt_count: 0, evidence_refs: [], outputs: [] }],
      skipped_steps: [],
      verified_outputs: [],
      last_disposition: 'none',
      pending_decision: null,
    },
    dispositions: [],
    pendingDecision: pending as unknown as TaskSummary['pending_decision'],
    resolvedDecisions: [],
    decisionError: null,
    readOnly: false,
  }
}

function mountDecision(cardValue: TaskCardState, readOnly = false) {
  return mount(TaskDecision, { props: { card: { ...cardValue, readOnly }, readOnly } })
}

describe('TaskDecision', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('oldRevisionButtonsCannotApproveNewPlan', async () => {
    const store = useTaskProgressStore()
    store.handleEvent({
      id: 'e1', conversation_id: 'c1', run_id: 'run-1', sequence: 1, type: 'task.plan_proposed',
      payload: { schema_version: 1, run_id: 'run-1', revision: 1, mode: 'copilot', review_digest: PLAN_ONE_REVIEW, steps: [{ id: 'fetch', intent: 'Fetch the observations', depends_on: [] }] },
      created_at: '',
    })
    store.handleEvent({
      id: 'e2', conversation_id: 'c1', run_id: 'run-1', sequence: 2, type: 'task.decision_requested',
      payload: { schema_version: 1, run_id: 'run-1', revision: 1, request_id: 'request-1', kind: 'plan_confirmation', prompt: 'Confirm this plan?', expires_at: '2026-11-10T00:00:00Z' },
      created_at: '',
    })
    // A repair published a new revision: the confirmation the old card
    // offered belongs to a plan no longer under review.
    store.handleEvent({
      id: 'e3', conversation_id: 'c1', run_id: 'run-1', sequence: 3, type: 'task.plan_proposed',
      payload: { schema_version: 1, run_id: 'run-1', revision: 2, mode: 'copilot', review_digest: PLAN_TWO_REVIEW, steps: [{ id: 'fetch', intent: 'Fetch the observations', depends_on: [] }] },
      created_at: '',
    })

    expect(store.decisionActionsDisabled('run-1')).toBe(true)
    const wrapper = mountDecision(store.cards.get('run-1')!)
    const choices = wrapper.findAll('button')
    expect(choices).toHaveLength(2)
    expect(choices.map((button) => button.attributes('aria-disabled'))).toEqual(['true', 'true'])
    await wrapper.get('[data-testid="task-decision-confirm"]').trigger('click')
    expect(wrapper.emitted('respond')).toBe(undefined)
    expect(wrapper.get('[data-testid="task-decision-stale"]').text()).toContain('superseded')

    // A hand-built command for the old revision is refused the same way.
    await store.submitTaskDecision(
      'run-1',
      {
        client_request_id: '11111111-1111-1111-1111-111111111111',
        request_id: 'request-1',
        plan_revision: 1,
        expected_digest: PLAN_ONE_REVIEW,
        action: 'confirm',
      },
    )
    expect(mockSubmit.submit.mock.calls).toHaveLength(0)
    wrapper.unmount()
  })

  it('rejectCancelExpiryAndReadOnlyScopeDisableActions', async () => {
    const store = useTaskProgressStore()
    for (const state of ['rejected', 'cancelled', 'expired']) {
      store.handleEvent({
        id: `p-${state}`, conversation_id: 'c1', run_id: `run-${state}`, sequence: 1, type: 'task.plan_proposed',
        payload: { schema_version: 1, run_id: `run-${state}`, revision: 1, mode: 'copilot', review_digest: PLAN_ONE_REVIEW, steps: [{ id: 'fetch', intent: 'Fetch the observations', depends_on: [] }] },
        created_at: '',
      })
      store.handleEvent({
        id: `ask-${state}`, conversation_id: 'c1', run_id: `run-${state}`, sequence: 2, type: 'task.decision_requested',
        payload: { schema_version: 1, run_id: `run-${state}`, revision: 1, request_id: `request-${state}`, kind: 'plan_confirmation', prompt: 'Confirm this plan?', expires_at: '2026-11-10T00:00:00Z' },
        created_at: '',
      })
      store.handleEvent({
        id: `end-${state}`, conversation_id: 'c1', run_id: `run-${state}`, sequence: 3, type: 'task.decision_resolved',
        payload: { schema_version: 1, run_id: `run-${state}`, revision: 1, request_id: `request-${state}`, state },
        created_at: '',
      })

      const open = store.cards.get(`run-${state}`)!
      expect(open.pendingDecision).toBe(null)
      // The recorded outcome stands: the card shows what happened, never a
      // second local task waiting for the same choice.
      expect(open.resolvedDecisions.map((entry) => entry.state)).toEqual([state])
      expect(store.decisionActionsDisabled(`run-${state}`)).toBe(true)
      await store.submitTaskDecision(
        `run-${state}`,
        {
          client_request_id: '22222222-2222-2222-2222-222222222222',
          request_id: `request-${state}`,
          plan_revision: 1,
          expected_digest: PLAN_ONE_REVIEW,
          action: 'confirm',
        },
      )
    }
    expect(mockSubmit.submit.mock.calls).toHaveLength(0)

    const settled = mountDecision(store.cards.get('run-rejected')!)
    expect(settled.findAll('button')).toHaveLength(0)
    settled.unmount()

    // A read-only or archived scope cannot answer either: the controls stay
    // where they are, but the recorded state is what stands.
    const readOnlyCard = card({
      request_id: 'request-readonly',
      kind: 'plan_confirmation',
      plan_revision: 1,
      expected_digest: PLAN_ONE_REVIEW,
      expires_at: '2026-11-10T00:00:00Z',
    })
    const readOnly = mountDecision(readOnlyCard, true)
    const readOnlyChoices = readOnly.findAll('button')
    expect(readOnlyChoices).toHaveLength(2)
    expect(readOnlyChoices.map((node) => node.attributes('aria-disabled'))).toEqual(['true', 'true'])
    await readOnly.get('[data-testid="task-decision-confirm"]').trigger('click')
    expect(readOnly.emitted('respond')).toBe(undefined)
    readOnly.unmount()
  })

  it('autoRecoveryShowsDiagnosisAndThreeExplicitChoices', async () => {
    const store = useTaskProgressStore()
    store.handleEvent({
      id: 'r1', conversation_id: 'c1', run_id: 'run-recovery', sequence: 1, type: 'task.plan_proposed',
      payload: {
        schema_version: 1, run_id: 'run-recovery', revision: 1, mode: 'auto', review_digest: PLAN_ONE_REVIEW,
        steps: [{ id: 'fetch', intent: 'Fetch the observations', depends_on: [] }],
      },
      created_at: '',
    })
    store.handleEvent({
      id: 'r2', conversation_id: 'c1', run_id: 'run-recovery', sequence: 2, type: 'task.decision_requested',
      payload: {
        schema_version: 1, run_id: 'run-recovery', revision: 1, request_id: 'request-recovery', kind: 'recovery',
        prompt: 'The fetch may have run without completing. Choose how to carry on.',
        expires_at: '2026-11-10T00:00:00Z',
        diagnosis: {
          plan_revision: 1,
          plan_digest: PLAN_ONE_REVIEW,
          step_id: 'fetch',
          attempt: 1,
          explanation: 'The fetch ended without publishing its output.',
          evidence_refs: ['artifact:11111111-1111-1111-1111-111111111111'],
          unknowns: ['Whether the command finished after the stream stopped'],
          risks: ['Repeating the step may fetch the same source twice'],
          blockers: [],
          eligible_actions: ['abort', 'repeat_step', 'continue_next'] as RecoveryAction[],
          recommended_action: 'continue_next' as RecoveryAction,
          digest: DIAGNOSIS_DIGEST,
        },
      },
      created_at: '',
    })

    const open = store.cards.get('run-recovery')!
    const wrapper = mountDecision(open)
    const recoveryButtonLabels = wrapper.findAll('[data-testid="task-decision-choice"]').map((node) => node.text())
    expect(recoveryButtonLabels).toEqual(['Abort', 'Repeat the step', 'Continue to the next step'])
    expect(wrapper.get('[data-testid="task-decision-diagnosis"]').text()).toContain('The fetch ended without publishing its output.')
    expect(wrapper.get('[data-testid="task-decision-unknowns"]').text()).toContain('Whether the command finished after the stream stopped')
    expect(wrapper.get('[data-testid="task-decision-risks"]').text()).toContain('Repeating the step may fetch the same source twice')
    // A recommendation is a label the reader may act on: it is never
    // preselected and never submitted on the reader's behalf.
    expect(wrapper.get('[data-testid="task-decision-recommended"]').text()).toBe('Suggested: Continue to the next step')
    expect(wrapper.findAll('[data-testid="task-decision-choice"]')).toHaveLength(3)
    expect(wrapper.find('[data-testid="task-decision-choice"]').attributes('aria-disabled')).toBe(undefined)
    const automaticRecoverySubmissions = mockSubmit.submit.mock.calls
    expect(automaticRecoverySubmissions).toHaveLength(0)

    // Choosing is one thing and sending another: only an explicit click binds.
    const recoveryPayload = store.commandFor('run-recovery', 'continue_next')
    expect(recoveryPayload!.expected_digest).toBe(open.pendingDecision!.expected_digest)
    expect(recoveryPayload!.expected_digest).toBe(DIAGNOSIS_DIGEST)
    expect(recoveryPayload!.request_id).toBe('request-recovery')
    wrapper.unmount()
  })

  it('unavailableRecoveryChoiceShowsReasonWithoutFallback', async () => {
    const store = useTaskProgressStore()
    store.handleEvent({
      id: 'u1', conversation_id: 'c1', run_id: 'run-unavailable', sequence: 1, type: 'task.plan_proposed',
      payload: {
        schema_version: 1, run_id: 'run-unavailable', revision: 1, mode: 'auto', review_digest: PLAN_ONE_REVIEW,
        steps: [{ id: 'fetch', intent: 'Fetch the observations', depends_on: [] }],
      },
      created_at: '',
    })
    store.handleEvent({
      id: 'u2', conversation_id: 'c1', run_id: 'run-unavailable', sequence: 2, type: 'task.decision_requested',
      payload: {
        schema_version: 1, run_id: 'run-unavailable', revision: 1, request_id: 'request-unavailable', kind: 'recovery',
        prompt: 'The step may already be running.',
        expires_at: '2026-11-10T00:00:00Z',
        diagnosis: {
          plan_revision: 1,
          plan_digest: PLAN_ONE_REVIEW,
          step_id: 'fetch',
          attempt: 1,
          explanation: 'The step was interrupted while its command may still have been running.',
          evidence_refs: [],
          unknowns: ['Whether the interrupted command finished'],
          risks: [],
          blockers: ['A command for this step may still be running'],
          eligible_actions: ['abort', 'continue_next'] as RecoveryAction[],
          recommended_action: null,
          digest: DIAGNOSIS_DIGEST,
        },
      },
      created_at: '',
    })

    const open = store.cards.get('run-unavailable')!
    expect(open.pendingDecision!.diagnosis!.eligible_actions).toEqual(['abort', 'continue_next'] as RecoveryAction[])
    const wrapper = mountDecision(open)
    const choices = wrapper.findAll('[data-testid="task-decision-choice"]')
    expect(choices).toHaveLength(3)
    expect(choices.map((node) => node.attributes('aria-disabled'))).toEqual([undefined, 'true', undefined])
    expect(wrapper.get('[data-testid="task-decision-reason-repeat_step"]').text()).toBe('A command for this step may still be running')

    // A keyboard user can still reach every control: disabled is not removed.
    expect(wrapper.find('[data-testid="task-decision-choice-repeat_step"]').exists()).toBe(true)
    await wrapper.get('[data-testid="task-decision-choice-repeat_step"]').trigger('click')
    expect(wrapper.emitted('respond')).toBe(undefined)

    // A bare "yes" in reply context chooses none of them.
    expect(store.replyToDecision('run-unavailable', 'yes')).toBe(null)
    const automaticRecoverySubmissions = mockSubmit.submit.mock.calls
    expect(automaticRecoverySubmissions).toHaveLength(0)
    wrapper.unmount()
  })
})
