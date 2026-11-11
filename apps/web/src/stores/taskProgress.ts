import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { generateRequestId } from '@/lib/requestId'
import { taskDecisionApi } from '@/services/api'
import type {
  RecoveryAction,
  TaskCardState,
  TaskDecisionCommand,
  TaskPendingDecision,
  TaskSummary,
  TaskPlanView,
  TaskDisposition,
  TaskStatus,
  DurableEvent,
} from '@/types'

// The browser's copy of one durable task. It is a fold, never an authority:
// the same events replay into the same card, and a reload folds the
// snapshot's own task summary over that fold. Nothing here starts work,
// re-runs a step, or chooses a recovery action - the only door a person's
// answer takes is the decision endpoint, bound to the exact request,
// revision and digest the card showed.
const TASK_EVENT_TYPES = new Set([
  'task.plan_proposed',
  'task.progress',
  'task.disposition',
  'task.decision_requested',
  'task.decision_resolved',
])

const AFFIRMATIVE = new Set(['yes', 'y', 'confirm', 'confirmed', 'approve', 'approved', 'ok', 'okay', 'proceed'])
const NEGATIVE = new Set(['no', 'n', 'reject', 'rejected', 'deny', 'denied', 'cancel', 'cancelled', 'abort'])
const RECOVERY_WORDS: Record<string, RecoveryAction> = {
  abort: 'abort',
  'abort task': 'abort',
  'abort the task': 'abort',
  cancel: 'abort',
  repeat: 'repeat_step',
  'repeat step': 'repeat_step',
  'repeat the step': 'repeat_step',
  retry: 'repeat_step',
  'retry step': 'repeat_step',
  'run step again': 'repeat_step',
  continue: 'continue_next',
  'continue next': 'continue_next',
  'continue to the next step': 'continue_next',
  next: 'continue_next',
  'skip step': 'continue_next',
  'skip this step': 'continue_next',
}

// One stable command id per decision and action, so a response the browser
// never managed to record re-asks the same command instead of starting a
// second one.
export const useTaskProgressStore = defineStore('taskProgress', () => {
  const cards = ref<Map<string, TaskCardState>>(new Map())
  const seenEvents = ref<Set<string>>(new Set())
  const commandIds = ref<Map<string, string>>(new Map())

  function writeCard(runId: string, next: TaskCardState): void {
    const map = new Map(cards.value)
    map.set(runId, next)
    cards.value = map
  }

  function planRevisionsFor(card: TaskCardState, revision: number, plan: TaskPlanView | null): TaskPlanView[] {
    const revisions = [...card.planRevisions]
    if (plan && !revisions.some((entry) => entry.revision === plan.revision)) {
      revisions.push(plan)
      revisions.sort((left, right) => left.revision - right.revision)
    }
    void revision
    return revisions
  }

  // The digest a confirmation must echo is the one the review made; a
  // recovery echoes the digest its own diagnosis made. Both arrive on the
  // public events, so the browser repeats what it saw instead of inventing a
  // copy that the server would refuse.
  function expectedDigestFor(pending: TaskPendingDecision, revisions: TaskPlanView[]): string | null {
    if (pending.diagnosis) return pending.diagnosis.digest
    const review = revisions.find((entry) => entry.revision === pending.plan_revision)
    return review?.review_digest ?? null
  }

  function handleEvent(event: DurableEvent): void {
    if (!TASK_EVENT_TYPES.has(event.type) || !event.run_id) return
    const runId = event.run_id
    if (seenEvents.value.has(`${runId}|${event.id}`)) return
    seenEvents.value = new Set([...seenEvents.value, `${runId}|${event.id}`])

    const current = cards.value.get(runId)
    const card: TaskCardState = current ?? {
      runId,
      sequence: -1,
      currentRevision: 0,
      planRevisions: [],
      progress: { schema_version: 1, run_id: runId },
      dispositions: [],
      pendingDecision: null,
      resolvedDecisions: [],
      decisionError: null,
      readOnly: false,
    }
    // Older or equal sequences never rewind the card: reconnects replay a
    // stream that already landed.
    if (event.sequence <= card.sequence) return

    const payload = event.payload
    const next: TaskCardState = { ...card, sequence: event.sequence, decisionError: null }

    switch (event.type) {
      case 'task.plan_proposed': {
        const plan: TaskPlanView = {
          revision: Number(payload.revision ?? 0),
          review_digest: String(payload.review_digest ?? ''),
          steps: (payload.steps as unknown as TaskPlanView['steps']) ?? [],
        }
        next.progress = { ...card.progress, revision: plan.revision, mode: (payload.mode ?? card.progress.mode) as TaskSummary['mode'], status: (payload.status ?? card.progress.status ?? 'planned') as TaskStatus }
        next.currentRevision = Math.max(card.currentRevision, plan.revision)
        next.planRevisions = planRevisionsFor(card, plan.revision, plan)
        break
      }
      case 'task.progress': {
        next.progress = {
          schema_version: 1,
          run_id: runId,
          mode: (payload.mode ?? card.progress.mode) as TaskSummary['mode'],
          revision: Number(payload.revision ?? card.progress.revision ?? 0),
          status: (payload.status ?? card.progress.status ?? 'unknown') as TaskStatus,
          steps: (payload.steps as unknown as TaskSummary['steps']) ?? card.progress.steps ?? [],
          skipped_steps: (payload.skipped_steps as unknown as string[]) ?? card.progress.skipped_steps ?? [],
          verified_outputs: (payload.verified_outputs as unknown as TaskSummary['verified_outputs']) ?? card.progress.verified_outputs ?? [],
          last_disposition: (payload.last_disposition ?? card.progress.last_disposition ?? 'none') as TaskSummary['last_disposition'],
          // The open decision has its own events; a progress view never
          // invents one.
          pending_decision: card.progress.pending_decision ?? null,
        }
        next.currentRevision = Math.max(card.currentRevision, next.progress.revision ?? 0)
        break
      }
      case 'task.disposition': {
        const stepId = payload.step_id === undefined || payload.step_id === null ? null : String(payload.step_id)
        next.dispositions = [
          ...card.dispositions,
          { stepId, disposition: (payload.disposition ?? 'none') as TaskDisposition },
        ]
        break
      }
      case 'task.decision_requested': {
        const review = next.planRevisions.find((entry) => entry.revision === Number(payload.revision ?? card.currentRevision))
        const pending: TaskPendingDecision = {
          request_id: String(payload.request_id ?? ''),
          kind: (payload.kind ?? 'plan_confirmation') as 'plan_confirmation' | 'clarification' | 'configuration' | 'recovery',
          plan_revision: Number(payload.revision ?? card.currentRevision),
          prompt: payload.prompt === undefined || payload.prompt === null ? null : String(payload.prompt),
          diagnosis: (payload.diagnosis as unknown as TaskPendingDecision['diagnosis']) ?? null,
          expires_at: payload.expires_at === undefined || payload.expires_at === null ? null : String(payload.expires_at),
          // The digest a response must echo: a recovery repeats its own
          // diagnosis, anything else repeats the review that made the plan.
          expected_digest:
            (payload.diagnosis as { digest?: string } | undefined)?.digest ?? review?.review_digest ?? null,
        }
        // A parked request also says the run is waiting for this person.
        next.progress = { ...card.progress, status: 'waiting', pending_decision: pending }
        next.pendingDecision = pending
        next.submitting = false
        break
      }
      case 'task.decision_resolved': {
        next.pendingDecision = null
        next.submitting = false
        next.progress = { ...card.progress, pending_decision: null }
        const previous = card.pendingDecision ?? card.resolvedDecisions.at(-1)
        next.resolvedDecisions = [
          ...card.resolvedDecisions,
          {
            request_id: String(payload.request_id ?? ''),
            kind: previous?.kind ?? null,
            plan_revision: Number(payload.revision ?? card.currentRevision),
            state: (payload.state ?? 'confirmed') as 'confirmed' | 'rejected' | 'answered' | 'expired' | 'cancelled',
          },
        ]
        break
      }
    }

    // A resolved request never leaves an answer pending, and a stale request
    // (a newer plan revision) stays visible but unactionable.
    writeCard(runId, next)
  }

  // The snapshot is authoritative over the fold: a reload lands what the
  // records say now, while the plan revisions a reader may still want to see
  // stay on the card.
  function handleSnapshot(task: TaskSummary, cursor?: number): void {
    const runId = task.run_id
    const card = cards.value.get(runId) ?? {
      runId,
      sequence: cursor ?? Number.MAX_SAFE_INTEGER,
      currentRevision: 0,
      planRevisions: [],
      progress: { schema_version: 1, run_id: runId },
      dispositions: [],
      pendingDecision: null,
      resolvedDecisions: [],
      decisionError: null,
      readOnly: false,
    }
    const pending = task.pending_decision ?? null
    const known = cards.value.get(runId)
    const carried = known?.pendingDecision ?? null
    // The durable snapshot carries the question; older snapshots may omit it,
    // so a matching request keeps the wording from its event when needed.
    const withPrompt =
      pending && carried && carried.request_id === pending.request_id
        ? { ...pending, prompt: pending.prompt ?? carried.prompt }
        : pending
    writeCard(
      runId,
      {
        ...card,
        sequence: cursor === undefined ? card.sequence : Math.max(card.sequence, cursor),
        currentRevision: task.revision ?? card.currentRevision,
        planRevisions: planRevisionsFor(card, task.revision ?? 0, task.plan ?? null),
        progress: { ...task, pending_decision: withPrompt },
        pendingDecision: withPrompt,
        decisionError: null,
        submitting: pending && carried?.request_id === pending.request_id ? card.submitting : false,
      },
    )
  }

  function markReadOnly(runId: string, readOnly: boolean): void {
    const card = cards.value.get(runId)
    if (!card) return
    // Marking a card that already says the same thing is not a change. A view
    // watches this state and marks it back; rewriting the card on a no-op made
    // that watcher re-fire without end, and the conversation never rendered.
    if (card.readOnly === readOnly) return
    writeCard(runId, { ...card, readOnly })
  }

  function pendingFor(runId: string): TaskPendingDecision | null {
    return cards.value.get(runId)?.pendingDecision ?? null
  }

  function decisionActionsDisabled(runId: string): boolean {
    const card = cards.value.get(runId)
    const pending = card?.pendingDecision
    if (!card || !pending) return true
    // A decision that belongs to a plan revision no longer under review
    // cannot be approved: the buttons stay, and the newer plan is what the
    // card shows.
    if (pending.plan_revision !== card.currentRevision) return true
    if (card.readOnly || card.submitting) return true
    const allowed = ACTIONS_FOR_KIND[pending.kind]
    return allowed.length === 0
  }

  const ACTIONS_FOR_KIND: Record<string, string[]> = {
    plan_confirmation: ['confirm', 'reject'],
    clarification: ['answer'],
    configuration: ['resume_configuration'],
    recovery: ['abort', 'repeat_step', 'continue_next'],
  }

  // A reply typed in the composer only counts in this decision's context, and
  // only deterministic wording maps. A bare "yes" facing recovery choices
  // chooses none of them.
  function replyToDecision(runId: string, text: string | null): TaskDecisionCommand | null {
    const pending = pendingFor(runId)
    if (!pending || decisionActionsDisabled(runId)) return null
    const words = (text ?? '').trim().toLowerCase().replace(/\s+/g, ' ')
    if (!words) return null

    if (pending.kind === 'clarification') {
      return buildCommand(runId, 'answer', (text ?? '').trim())
    }
    if (pending.kind === 'configuration') {
      return AFFIRMATIVE.has(words) ? buildCommand(runId, 'resume_configuration') : null
    }
    if (pending.kind === 'plan_confirmation') {
      if (AFFIRMATIVE.has(words)) return buildCommand(runId, 'confirm')
      if (NEGATIVE.has(words)) return buildCommand(runId, 'reject')
      return null
    }

    const action = RECOVERY_WORDS[words]
    if (!action) return null
    const eligible = pending.diagnosis?.eligible_actions ?? []
    if (!eligible.includes(action)) return null
    return buildCommand(runId, action)
  }

  function buildCommand(
    runId: string,
    action: string,
    answer?: string | null,
  ): TaskDecisionCommand | null {
    const pending = pendingFor(runId)
    if (!pending || decisionActionsDisabled(runId)) return null
    // Only an action the decision lane accepts for this kind may bind; a
    // stray click or a hand-built call for anything else gets nothing.
    if (!ACTIONS_FOR_KIND[pending.kind].includes(action)) return null
    const choice = action as TaskDecisionCommand['action']
    const key = `${runId}|${pending.request_id}|${choice}`
    const clientRequestId = commandIds.value.get(key) ?? generateRequestId()
    const ids = new Map(commandIds.value)
    ids.set(key, clientRequestId)
    commandIds.value = ids
    const expectedDigest =
      pending.expected_digest ?? expectedDigestFor(pending, cards.value.get(runId)!.planRevisions)
    if (!expectedDigest) return null
    return {
      client_request_id: clientRequestId,
      request_id: pending.request_id,
      plan_revision: pending.plan_revision,
      expected_digest: expectedDigest,
      action: choice,
      ...(choice === 'answer' ? { answer } : {}),
    }
  }

  async function submitTaskDecision(runId: string, command: TaskDecisionCommand): Promise<void> {
    const pending = pendingFor(runId)
    if (cards.value.get(runId)?.submitting) return
    // The card must still show that exact request. A command for a superseded
    // revision, a settled request, or a read-only scope never reaches the
    // server: the recorded state is what the card shows.
    if (
      !pending ||
      decisionActionsDisabled(runId) ||
      command.request_id !== pending.request_id ||
      command.plan_revision !== pending.plan_revision ||
      command.expected_digest !== (pending.expected_digest ?? expectedDigestFor(pending, cards.value.get(runId)!.planRevisions))
    ) {
      const card = cards.value.get(runId)
      if (card) {
        writeCard(
          runId,
          {
            ...card,
            decisionError: `This run is no longer waiting on request ${command.request_id}; the card shows the recorded state.`,
          },
        )
      }
      return
    }

    writeCard(runId, { ...cards.value.get(runId)!, submitting: true, decisionError: null })
    try {
      const response = await taskDecisionApi.submit(runId, command)
      const projection = response.data as Record<string, unknown>
      const card = cards.value.get(runId)
      if (!card) return
      // The projection is the server's own view: adopt what it states, and
      // leave the rest of the fold alone. Absent fields mean absent, not
      // "reset to nothing".
      const progress: TaskSummary = {
        ...card.progress,
        ...(projection.current_revision === undefined
          ? {}
          : { revision: Number(projection.current_revision) }),
        ...(projection.status === undefined
          ? {}
          : { status: projection.status as TaskStatus }),
        // The projection names an open request only when one is open.
        ...('pending_decision' in projection
          ? { pending_decision: (projection.pending_decision as TaskPendingDecision | null) }
          : {}),
      }
      writeCard(runId, { ...card, progress, pendingDecision: card.pendingDecision, decisionError: null })
    } catch (failure: unknown) {
      const card = cards.value.get(runId)
      if (!card) return
      // The choice is the same request again: completed steps stay exactly
      // where they were.
      writeCard(
        runId,
        {
          ...card,
          submitting: false,
          decisionError: 'The choice could not be sent. The same request will be asked again.',
        },
      )
    }
  }

  function reset(): void {
    cards.value = new Map()
    seenEvents.value = new Set()
    commandIds.value = new Map()
  }

  const hasTask = computed(() => cards.value.size > 0)

  return {
    cards,
    hasTask,
    handleEvent,
    handleSnapshot,
    markReadOnly,
    pendingFor,
    decisionActionsDisabled,
    replyToDecision,
    buildCommand,
    commandFor: buildCommand,
    submitTaskDecision,
    reset,
  }
})
