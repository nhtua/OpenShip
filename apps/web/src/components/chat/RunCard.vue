<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Badge, Icon } from '@/components/ui'
import RunResume from './RunResume.vue'
import TaskPlan from './TaskPlan.vue'
import TaskDecision from './TaskDecision.vue'
import type {
  ProviderAttemptSummary,
  RunCard,
  RunModelSummary,
  UsageSummary,
  Run,
  TaskCardState,
} from '@/types'

interface Props {
  card: RunCard
  hasActions?: boolean
  canResumeConnector?: boolean
  connectorActionBusy?: boolean
  task?: TaskCardState | null
  readOnly?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  hasActions: false,
  canResumeConnector: false,
  connectorActionBusy: false,
  task: null,
  readOnly: false,
})
const emit = defineEmits<{
  'analysis-resumed': [run: Run]
  'resume-connector': []
  'dismiss-connector': []
  'setup-connector': []
  'resubmit-connector': []
  respond: [action: string, answer: string | null]
}>()
const kind = computed(() => props.card.kind ?? 'chat')
const expanded = ref(false)
const now = ref(Date.now())
const detailsId = computed(() => `run-details-${props.card.run_id || 'current'}`)
let durationTimer: number | null = null

const statusLabel = computed(() => {
  switch (props.card.status) {
    case 'queued':
      return 'Queued'
    case 'running':
      return 'Running'
    case 'waiting_connector':
      return 'Waiting for connector'
    case 'waiting_plan':
    case 'waiting_input':
      return 'Waiting for you'
    case 'waiting_config':
      return 'Waiting for configuration'
    case 'paused':
      return 'Paused'
    case 'failed':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
    case 'succeeded':
      return 'Completed'
    default:
      return props.card.status
  }
})

const statusVariant = computed(() => {
  switch (props.card.status) {
    case 'running':
      return 'default'
    case 'waiting_connector':
    case 'waiting_plan':
    case 'waiting_input':
    case 'waiting_config':
      return 'outline'
    case 'failed':
      return 'destructive'
    case 'cancelled':
      return 'secondary'
    case 'succeeded':
      return 'default'
    default:
      return 'outline'
  }
})

// Anything about the provider is only worth stating once the run reached an
// end: before that, "not recorded" would read as a failure that has not
// happened yet.
const finished = computed(
  () =>
    ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(props.card.status),
)

// What the run was pinned to. Null means the run predates profiles, and
// nothing here guesses one for it — the card says what is not known.
const requestedText = computed(() => {
  const model: RunModelSummary | null = props.card.model
  if (!model) return 'Provider not recorded'
  const destination = model.destination_label
    ? ` at ${model.destination_label}`
    : ''
  return `Requested: ${model.label} · ${model.requested_model}${destination}`
})

const servingText = computed(() => {
  const model: RunModelSummary | null = props.card.model
  if (!model) return ''
  const attempt = [...props.card.attempts]
    .reverse()
    .find((candidate) => candidate.outcome === 'succeeded')
  const profileId = model.serving_profile_id ?? attempt?.profile_id
  const servedModel = model.actual_model ?? attempt?.actual_model

  // Older run reads may have the observed name without serving destination
  // metadata. State only that name; never attach it to the pinned destination.
  if (!profileId && servedModel) return `Served model: ${servedModel}`
  if (!profileId) return ''

  const label = model.serving_label ?? profileId
  const adapter = model.serving_adapter ?? attempt?.adapter
  const destination = model.serving_destination_label ?? attempt?.destination_label
  const modelName = servedModel ?? attempt?.requested_model ?? model.requested_model
  const details = [label, adapter, modelName].filter(Boolean).join(' · ')
  return `Served by: ${details}${destination ? ` at ${destination}` : ''}`
})

// The requests actually made, in order. A fallback is part of this chain
// rather than hidden, because it is what a reader would want to know.
const attemptText = computed(() => {
  const attempts: ProviderAttemptSummary[] = props.card.attempts
  if (!attempts.length) return ''
  return attempts.map((attempt) => attempt.profile_id).join(' → ')
})

function formatDuration(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000)
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainder = seconds % 60
  if (hours) return `${hours}h ${minutes}m`
  if (minutes) return `${minutes}m ${remainder}s`
  return `${remainder}s`
}

const durationText = computed(() => {
  if (!props.card.started_at) return null
  const started = Date.parse(props.card.started_at)
  const finished = props.card.ended_at
    ? Date.parse(props.card.ended_at)
    : props.card.status === 'running'
      ? now.value
      : Number.NaN
  if (!Number.isFinite(started) || !Number.isFinite(finished) || finished < started) return null
  return `Duration ${formatDuration(finished - started)}`
})

const contextText = computed(() => {
  const inputTokens = props.card.usage?.input_tokens
  const contextTokens = props.card.model?.serving_context_tokens
  if (inputTokens == null || contextTokens == null || contextTokens <= 0) return null
  return `Context ${((inputTokens / contextTokens) * 100).toFixed(1)}%`
})

const totalTokensText = computed(() => {
  const totalTokens: UsageSummary['total_tokens'] = props.card.usage?.total_tokens ?? null
  return totalTokens == null ? null : `${totalTokens.toLocaleString()} tokens`
})
const verifiedCount = computed(() => props.task?.progress.steps?.filter((step) => step.status === 'verified').length ?? 0)
const stepCount = computed(() => props.task?.planRevisions.find((plan) => plan.revision === props.task?.currentRevision)?.steps.length ?? props.task?.progress.steps?.length ?? 0)
const currentStep = computed(() => {
  const running = props.task?.progress.steps?.find((step) => step.status === 'running')
  if (!running) return null
  return props.task?.planRevisions.find((plan) => plan.revision === props.task?.currentRevision)?.steps.find((step) => step.id === running.id)?.intent ?? running.id
})
const needsAttention = computed(() => Boolean(props.task?.pendingDecision && !props.task.readOnly && !props.task.submitting && !props.readOnly &&
  props.task.pendingDecision.plan_revision === props.task.currentRevision))

function syncDurationTimer() {
  if (props.card.status === 'running' && durationTimer === null) {
    now.value = Date.now()
    durationTimer = window.setInterval(() => {
      now.value = Date.now()
    }, 1000)
  } else if (props.card.status !== 'running' && durationTimer !== null) {
    window.clearInterval(durationTimer)
    durationTimer = null
  }
}

watch(() => props.card.status, syncDurationTimer)
onMounted(syncDurationTimer)
onBeforeUnmount(() => {
  if (durationTimer !== null) window.clearInterval(durationTimer)
})
</script>

<template>
  <div
    data-testid="run-card"
    :data-has-actions="hasActions"
    class="run-card overflow-hidden rounded-md border border-border bg-card text-card-foreground text-xs"
  >
    <div class="flex min-w-0 items-center gap-2 px-2.5 py-1.5">
      <button
        :aria-controls="detailsId"
        :aria-expanded="expanded"
        :aria-label="`${statusLabel}. ${needsAttention ? 'Action needed. ' : ''}${expanded ? 'Hide' : 'Show'} run details`"
        data-testid="run-summary"
        type="button"
        class="run-card__summary flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="expanded = !expanded"
      >
        <Badge :variant="statusVariant" class="run-card__status h-5 shrink-0 text-[10px]">
          {{ statusLabel }}
        </Badge>
        <span v-if="task && stepCount" data-testid="run-task-progress" class="shrink-0 font-medium">
          {{ verifiedCount }} of {{ stepCount }} verified
        </span>
        <span v-if="currentStep" data-testid="run-current-step" class="min-w-0 truncate text-muted-foreground">Now: {{ currentStep }}</span>
        <span v-if="needsAttention" class="shrink-0 font-medium text-destructive">Action needed</span>
        <span class="ml-auto flex shrink-0 items-center gap-2">
          <span v-if="needsAttention" data-testid="run-attention-dot" aria-hidden="true" class="size-2 rounded-full bg-destructive shadow-[0_0_8px_currentColor] text-destructive animate-pulse motion-reduce:animate-none" />
          <Icon :name="expanded ? 'chevron-up' : 'chevron-down'" class="size-3 text-muted-foreground" aria-hidden="true" />
        </span>
        <span v-if="durationText || contextText || totalTokensText" data-testid="run-metrics" class="flex w-full flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
          <span v-if="durationText" data-testid="run-duration">{{ durationText }}</span>
          <span v-if="contextText" data-testid="run-context">{{ contextText }}</span>
          <span v-if="totalTokensText" data-testid="run-total-tokens">{{ totalTokensText }}</span>
        </span>
      </button>
      <div v-if="hasActions" data-testid="run-actions" class="flex shrink-0 items-center gap-1">
        <slot />
      </div>
    </div>

    <div
      v-if="expanded"
      :id="detailsId"
      data-testid="run-details"
      class="flex flex-col gap-1.5 border-t border-border px-3 py-2 text-muted-foreground"
    >
      <span v-if="card.started_at">
        Started {{ new Date(card.started_at).toLocaleTimeString() }}
      </span>
      <span v-if="card.ended_at">
        Ended {{ new Date(card.ended_at).toLocaleTimeString() }}
      </span>
      <template v-if="kind === 'chat' && finished">
        <span>{{ requestedText }}</span>
        <span v-if="servingText">{{ servingText }}</span>
      </template>
      <span v-if="attemptText">{{ attemptText }}</span>
      <span v-if="contextText">{{ contextText }}</span>
      <span v-if="totalTokensText">{{ totalTokensText }}</span>
      <span v-if="card.model?.serving_context_tokens">Context window {{ card.model.serving_context_tokens.toLocaleString() }} tokens</span>
      <span v-if="card.usage?.input_tokens != null">Input {{ card.usage.input_tokens.toLocaleString() }}</span>
      <span v-if="card.usage?.output_tokens != null">Output {{ card.usage.output_tokens.toLocaleString() }}</span>
      <!-- The settled safe copy, never a provider's own body. -->
      <span v-if="card.error_message" class="text-destructive">
        ({{ card.error_message }})
      </span>
      <span v-else-if="card.error_code" class="text-destructive">
        ({{ card.error_code }})
      </span>
      <TaskPlan v-if="task" :card="task" class="text-card-foreground" />
      <TaskDecision v-if="task?.pendingDecision" :card="task" :read-only="readOnly" @respond="(action, answer) => emit('respond', action, answer)" />
    </div>

    <RunResume
      v-if="card.analysis && (finished || card.analysis.output_ids.length || card.analysis.retained_error_code)"
      :key="card.run_id"
      :run-id="card.run_id"
      :analysis="card.analysis"
      :can-resume="finished"
      :error="card.error_message || card.error_code"
      @resumed="emit('analysis-resumed', $event)"
    />
    <section
      v-if="card.status === 'waiting_connector' && card.connector_blocker"
      data-testid="connector-blocker"
      class="grid gap-2 border-t border-border px-3 py-3"
      aria-label="Connector setup required"
    >
      <p class="text-xs text-muted-foreground">
        {{ card.connector_blocker.connector_kind.toUpperCase() }} setup is needed before this request can run. No provider or cloud request was sent.
      </p>
      <p class="text-xs text-muted-foreground">
        Waiting for revision {{ card.connector_blocker.connector_revision }} · Expires {{ new Date(card.connector_blocker.expires_at).toLocaleString() }}
      </p>
      <p v-if="!canResumeConnector" class="text-xs text-muted-foreground">
        The current connector revision does not match this request. Resubmit it as a new turn after setup.
      </p>
      <div class="flex flex-wrap gap-2">
        <button
          v-if="canResumeConnector"
          type="button"
          data-testid="resume-connector"
          class="rounded-md border border-input px-2.5 py-1.5 text-xs font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :disabled="connectorActionBusy"
          @click="emit('resume-connector')"
        >
          Resume request
        </button>
        <button
          type="button"
          data-testid="setup-connector"
          class="rounded-md border border-input px-2.5 py-1.5 text-xs font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :disabled="connectorActionBusy"
          @click="emit('setup-connector')"
        >
          Open connector setup
        </button>
        <button
          type="button"
          data-testid="resubmit-connector"
          class="rounded-md border border-input px-2.5 py-1.5 text-xs font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :disabled="connectorActionBusy"
          @click="emit('resubmit-connector')"
        >
          Resubmit in composer
        </button>
        <button
          type="button"
          data-testid="dismiss-connector"
          class="rounded-md border border-input px-2.5 py-1.5 text-xs font-medium text-destructive hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          :disabled="connectorActionBusy"
          @click="emit('dismiss-connector')"
        >
          Cancel wait
        </button>
      </div>
    </section>
  </div>
</template>
