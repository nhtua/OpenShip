<script setup lang="ts">
import { computed } from 'vue'
import type { TaskCardState } from '@/types'
import TaskDecision from './TaskDecision.vue'

const props = defineProps<{ card: TaskCardState; readOnly?: boolean }>()
const emit = defineEmits<{ respond: [action: string, answer: string | null] }>()
const plan = computed(() => props.card.planRevisions.find((item) => item.revision === props.card.currentRevision))
const outcome = computed(() => props.card.resolvedDecisions.at(-1))
const outcomeLabel = computed(() => outcome.value?.state === 'confirmed' ? 'Confirmed' : outcome.value?.state)
// What the run was pinned to. A Copilot plan is gated by a decision request;
// an Auto plan is not, so a plan with nothing under review says so rather than
// leaving a reader looking for a choice that the records never opened.
const modeWord = computed(() => {
  const mode = props.card.progress.mode
  return mode === 'auto' ? 'Auto' : mode === 'copilot' ? 'Copilot' : null
})
const heading = computed(() => ['Task', modeWord.value, plan.value ? `Plan revision ${plan.value.revision}` : 'Decision']
  .filter(Boolean)
  .join(' · '))
const confirmationNote = computed(() =>
  props.card.progress.mode === 'auto' && !props.card.pendingDecision && !props.card.resolvedDecisions.length
    ? 'No confirmation was asked'
    : null,
)
</script>

<template>
  <div class="message flex min-w-0 items-start gap-4" data-testid="task-timeline-entry">
    <div class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground" aria-hidden="true">OS</div>
    <section class="min-w-0 max-w-full flex-1 rounded-lg border border-border bg-card px-4 py-3 md:max-w-2xl" aria-label="Task interaction">
      <p class="text-xs font-semibold text-muted-foreground">{{ heading }}</p>
      <p v-if="confirmationNote" data-testid="task-confirmation-note" class="mt-1 text-xs text-muted-foreground">{{ confirmationNote }}</p>
      <ol v-if="plan && (!outcomeLabel || card.pendingDecision)" class="mt-2 list-decimal space-y-1 pl-5 text-sm">
        <li v-for="step in plan.steps" :key="step.id">{{ step.intent }}</li>
      </ol>
      <TaskDecision
        v-if="card.pendingDecision"
        class="mt-3"
        :card="card"
        :read-only="readOnly"
        @respond="(action, answer) => emit('respond', action, answer)"
      />
      <p v-else-if="outcomeLabel" class="mt-2 text-xs text-muted-foreground">{{ outcomeLabel }}</p>
    </section>
  </div>
</template>
