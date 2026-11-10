<script setup lang="ts">
import { computed } from 'vue'
import type { TaskCardState, TaskStatus } from '@/types'

interface Props {
  card?: TaskCardState | null
}

const props = withDefaults(defineProps<Props>(), { card: null })

const STEP_WORD: Record<string, string> = {
  pending: 'Pending',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  interrupted: 'Interrupted',
  uncertain: 'Outcome unknown',
  verified: 'Verified',
  skipped: 'Skipped',
}

// A step that was skipped or whose outcome is unknown is never shown as done:
// only "verified" claims that, and it is the only word the records grant.
const STATUS_WORD: Record<string, TaskStatus | string> = {
  unknown: 'Status unknown',
  planned: 'Planned',
  running: 'Running',
  waiting: 'Waiting for you',
  verification_pending: 'Verification pending',
  completed: 'Completed',
  blocked: 'Blocked',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const DISPOSITION_WORD: Record<string, string> = {
  none: 'Recorded without a failure class',
  known_failure: 'A known failure',
  material_revision: 'Repaired after a changed plan',
  uncertain: 'An outcome that could not be confirmed',
  denied: 'A refusal',
  policy_revoked: 'A policy was revoked',
  budget_exhausted: 'The limits were reached',
}

const statusWord = computed(() => STATUS_WORD[props.card?.progress.status ?? 'unknown'] ?? 'Status unknown')
const modeWord = computed(() => {
  const mode = props.card?.progress.mode
  return mode === 'auto' ? 'Auto' : mode === 'copilot' ? 'Copilot' : 'Mode not recorded'
})
function stepLabel(status: string): string {
  return STEP_WORD[status] ?? 'Recorded'
}
</script>

<template>
  <section
    v-if="card"
    data-testid="task-plan"
    aria-label="Task plan and progress"
    aria-live="polite"
    class="gap-2 rounded-lg border border-border px-3 py-2"
  >
    <p data-testid="task-plan-status" class="text-sm font-medium">
      {{ statusWord }}<template v-if="card.progress.mode"> · {{ modeWord }}</template>
    </p>

    <!-- Every revision the stream carried stays on the card: a repair
         replaced the plan under review, it did not hide the earlier one. -->
    <div data-testid="task-plan-revisions" class="flex flex-wrap gap-2">
      <span
        v-for="revision in card.planRevisions"
        :key="revision.revision"
        class="flex gap-1"
      >
        <span
          data-testid="task-plan-revision"
          :class="revision.revision === card.currentRevision ? 'font-medium' : 'text-muted-foreground'"
        >{{ revision.revision }}</span>
        <span class="text-muted-foreground">· {{ revision.steps.length }} steps</span>
      </span>
    </div>

    <ol data-testid="task-plan-steps" class="m-0 p-0">
      <li
        v-for="step in card.progress.steps ?? []"
        :key="step.id"
        data-testid="task-plan-step"
        class="flex flex-wrap gap-2 items-center"
      >
        <span :data-testid="`task-plan-step-${step.id}`">
          {{ stepLabel(step.status) }}<template v-if="step.attempt_count && step.attempt_count > 1">
            · attempt {{ step.attempt_count }}</template>
          <template v-if="(step.outputs ?? []).length">
            · {{ (step.outputs ?? []).length }} published</template>
        </span>
        <span class="text-muted-foreground">{{ step.id }}</span>
      </li>
    </ol>

    <div
      data-testid="task-plan-disposition"
      class="flex flex-wrap gap-2 text-xs text-muted-foreground"
    >
      <span v-for="(entry, index) in card.dispositions" :key="index" data-testid="task-plan-disposition-entry">
        {{ DISPOSITION_WORD[entry.disposition] ?? entry.disposition }}{{ entry.stepId ? ` · ${entry.stepId}` : '' }}
      </span>
    </div>

    <div v-if="card.progress.skipped_steps?.length" class="flex flex-wrap gap-2 text-xs">
      <span
        v-for="skipped in card.progress.skipped_steps"
        :key="skipped"
        data-testid="task-plan-skipped"
        class="text-muted-foreground"
      >
        Skipped · {{ skipped }}
      </span>
    </div>

    <div class="flex flex-wrap gap-2">
      <span
        v-for="output in card.progress.verified_outputs ?? []"
        :key="output.id"
        data-testid="task-plan-output"
        class="text-xs text-muted-foreground"
      >
        {{ output.name ?? output.id }} · {{ output.status === 'ready' ? 'ready' : output.status }}
      </span>
    </div>

    <p
      v-if="card.decisionError"
      data-testid="task-plan-decision-error"
      aria-live="polite"
      class="text-xs text-destructive"
    >
      {{ card.decisionError }}
    </p>

    <!-- Only a completed task may say it finished: unresolved or skipped
         work keeps the run's own state. -->
    <div
      v-if="card.progress.status === 'completed'"
      data-testid="task-plan-completed-banner"
      class="rounded-md bg-muted/30 px-2 py-1 text-xs"
    >
      Completed
    </div>
  </section>
</template>
