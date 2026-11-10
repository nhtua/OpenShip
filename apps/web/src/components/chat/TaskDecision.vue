<script setup lang="ts">
import { computed } from 'vue'
import type { TaskCardState, TaskPendingDecision, RecoveryAction } from '@/types'

interface Props {
  card?: TaskCardState | null
  readOnly?: boolean
}

const props = withDefaults(defineProps<Props>(), { card: null, readOnly: false })

const emit = defineEmits<{
  respond: [action: string, answer: string | null]
}>()

const RECOVERY_ORDER: RecoveryAction[] = ['abort', 'repeat_step', 'continue_next']
const RECOVERY_LABEL: Record<RecoveryAction, string> = {
  abort: 'Abort',
  repeat_step: 'Repeat the step',
  continue_next: 'Continue to the next step',
}
const KIND_LABEL = {
  plan_confirmation: 'Plan confirmation',
  clarification: 'A question to answer',
  configuration: 'A configuration to resume',
  recovery: 'A recovery choice',
}

// Why an unavailable option is unavailable, in the words a reader can act
// on. The backend's own blockers come first; the notes only apply when the
// records gave none.
const RECOVERY_NOTE: Record<RecoveryAction, string> = {
  abort: 'Aborting is always available',
  repeat_step: 'This step has reached its attempt limit',
  continue_next: 'A step that depends on this one cannot start yet',
}

const pending = computed<TaskPendingDecision | null>(() => props.card?.pendingDecision ?? null)
// A decision left over from a plan revision that is no longer under review
// stays visible, and nothing about it may be approved.
const stale = computed(() => pending.value !== null && props.card !== null &&
  pending.value!.plan_revision !== props.card!.currentRevision)
const eligible = computed(() => {
  const diagnosis = pending.value?.diagnosis
  return diagnosis?.eligible_actions ?? RECOVERY_ORDER
})

function blockedFor(action: string): boolean {
  if (!pending.value || props.readOnly || stale.value) return true
  if (pending.value.kind === 'recovery') return !eligible.value.includes(action as RecoveryAction)
  return false
}

function reasonFor(action: RecoveryAction): string {
  const diagnosis = pending.value?.diagnosis
  if (!diagnosis) return ''
  if (eligible.value.includes(action)) return ''
  if (diagnosis.blockers?.length) return diagnosis.blockers.join(' ')
  return RECOVERY_NOTE[action]
}

// A suggestion is a label the reader may act on, never a preselected or
// auto-submitted choice.
const recommendedLabel = computed(() => {
  const action = pending.value?.diagnosis?.recommended_action
  return action ? `Suggested: ${RECOVERY_LABEL[action]}` : null
})

function choose(action: string, answer: string | null = null) {
  // The guard is the whole point: a control the records say is unavailable
  // cannot become a submission, and a superseded request never posts.
  if (blockedFor(action)) return
  emit('respond', action, answer)
}
</script>

<template>
  <section
    v-if="pending"
    data-testid="task-decision"
    :aria-label="KIND_LABEL[pending.kind]"
    aria-live="polite"
    class="gap-2 rounded-lg border border-border px-3 py-2"
  >
    <p data-testid="task-decision-heading" class="text-sm font-medium">
      {{ KIND_LABEL[pending.kind] }} · plan revision {{ pending.plan_revision }}
    </p>

    <p
      v-if="pending.prompt"
      data-testid="task-decision-prompt"
      class="text-sm text-muted-foreground"
    >
      {{ pending.prompt }}
    </p>

    <p
      v-if="stale"
      data-testid="task-decision-stale"
      aria-live="polite"
      class="text-xs text-muted-foreground"
    >
      This request belongs to a plan revision that was superseded; the card shows the current plan.
    </p>

    <!-- A recovery states what is known, what is not, and what could go
         wrong before any choice is offered. -->
    <div
      v-if="pending.diagnosis"
      data-testid="task-decision-diagnosis"
      class="flex gap-2 rounded-md bg-muted/30 px-2 py-1 text-xs"
    >
      <p data-testid="task-decision-explanation">
        {{ pending.diagnosis.explanation }} · attempt {{ pending.diagnosis.attempt }} of
        {{ pending.diagnosis.step_id }}
      </p>
      <ul v-if="pending.diagnosis.unknowns?.length" data-testid="task-decision-unknowns" class="m-0 p-0 list-none">
        <li v-for="unknown in pending.diagnosis.unknowns" :key="unknown">{{ unknown }}</li>
      </ul>
      <ul v-if="pending.diagnosis.risks?.length" data-testid="task-decision-risks" class="m-0 p-0 list-none">
        <li v-for="risk in pending.diagnosis.risks" :key="risk">{{ risk }}</li>
      </ul>
      <p
        v-if="recommendedLabel"
        data-testid="task-decision-recommended"
        class="text-xs text-muted-foreground"
      >
        {{ recommendedLabel }}
      </p>
    </div>

    <!-- Recovery offers all three choices every time; eligibility decides
         which one a click may send, and a suggestion is only a label. -->
    <div v-if="pending.kind === 'recovery'" data-testid="task-decision-choices" class="flex flex-wrap gap-2">
      <button
        v-for="action in RECOVERY_ORDER"
        :key="action"
        type="button"
        data-testid="task-decision-choice"
        :aria-label="RECOVERY_LABEL[action]"
        :aria-disabled="blockedFor(action) ? 'true' : undefined"
        :disabled="blockedFor(action)"
        @click="choose(action)"
        class="rounded-md px-2 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span :data-testid="`task-decision-choice-${action}`">{{ RECOVERY_LABEL[action] }}</span>
        <span
          v-if="reasonFor(action)"
          :data-testid="`task-decision-reason-${action}`"
          aria-live="polite"
          class="text-xs text-muted-foreground"
        >
          {{ reasonFor(action) }}
        </span>
      </button>
    </div>

    <div v-else-if="pending.kind === 'plan_confirmation'" data-testid="task-decision-confirm-actions" class="flex flex-wrap gap-2">
      <button
        type="button"
        data-testid="task-decision-confirm"
        aria-label="Confirm this plan"
        :aria-disabled="blockedFor('confirm') ? 'true' : undefined"
        :disabled="blockedFor('confirm')"
        @click="choose('confirm')"
        class="rounded-md px-2 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
      >
        Confirm plan
      </button>
      <button
        type="button"
        data-testid="task-decision-reject"
        aria-label="Reject this plan"
        :aria-disabled="blockedFor('reject') ? 'true' : undefined"
        :disabled="blockedFor('reject')"
        @click="choose('reject')"
        class="rounded-md px-2 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
      >
        Reject plan
      </button>
    </div>

    <div v-else-if="pending.kind === 'configuration'" data-testid="task-decision-configuration" class="flex flex-wrap gap-2">
      <button
        type="button"
        data-testid="task-decision-resume-configuration"
        aria-label="Resume with these settings"
        :aria-disabled="blockedFor('resume_configuration') ? 'true' : undefined"
        :disabled="blockedFor('resume_configuration')"
        @click="choose('resume_configuration')"
        class="rounded-md px-2 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
      >
        Resume with these settings
      </button>
    </div>

    <!-- One answer path: the composer. A typed reply is bound to this exact
         request, and it reaches the same decision endpoint the buttons do. -->
    <p
      v-else
      data-testid="task-decision-answer-in-composer"
      class="text-xs text-muted-foreground"
    >
      Answer in the composer below.
    </p>

    <!-- The recorded outcome stays after a request is settled: history is
         kept, never a second local task. -->
    <div
      v-if="card && card.resolvedDecisions.length"
      data-testid="task-decision-history"
      class="flex flex-wrap gap-2 text-xs text-muted-foreground"
    >
      <span v-for="entry in card.resolvedDecisions" :key="entry.request_id" data-testid="task-decision-resolved">
        {{ entry.kind ?? 'Decision' }} · {{ entry.state }}
      </span>
    </div>
  </section>
</template>
