<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Badge, Icon } from '@/components/ui'
import type {
  ProviderAttemptSummary,
  RunCard,
  RunModelSummary,
  UsageSummary,
} from '@/types'

interface Props {
  card: RunCard
  hasActions?: boolean
}

const props = withDefaults(defineProps<Props>(), { hasActions: false })
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
    class="run-card overflow-hidden rounded-md border border-slate-700 bg-slate-800/50 text-xs"
  >
    <div class="flex min-w-0 items-center gap-2 px-2.5 py-1.5">
      <button
        :aria-controls="detailsId"
        :aria-expanded="expanded"
        :aria-label="`${statusLabel}. ${expanded ? 'Hide' : 'Show'} run details`"
        data-testid="run-summary"
        type="button"
        class="run-card__summary flex min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="expanded = !expanded"
      >
        <Badge :variant="statusVariant" class="run-card__status h-5 shrink-0 text-[10px]">
          {{ statusLabel }}
        </Badge>
        <span v-if="durationText" data-testid="run-duration" class="run-card__stat run-card__duration truncate text-slate-400">
          {{ durationText }}
        </span>
        <span v-if="contextText" data-testid="run-context" class="run-card__stat run-card__context truncate text-slate-400">
          {{ contextText }}
        </span>
        <span v-if="totalTokensText" data-testid="run-total-tokens" class="run-card__stat run-card__tokens truncate text-slate-400">
          {{ totalTokensText }}
        </span>
        <Icon
          :name="expanded ? 'chevron-up' : 'chevron-down'"
          class="ml-auto size-3 shrink-0 text-slate-500"
          aria-hidden="true"
        />
      </button>
      <div v-if="hasActions" data-testid="run-actions" class="flex shrink-0 items-center gap-1">
        <slot />
      </div>
    </div>

    <div
      v-if="expanded"
      :id="detailsId"
      data-testid="run-details"
      class="flex flex-col gap-1.5 border-t border-slate-700 px-3 py-2 text-slate-400"
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
      <!-- The settled safe copy, never a provider's own body. -->
      <span v-if="card.error_message" class="text-red-400">
        ({{ card.error_message }})
      </span>
      <span v-else-if="card.error_code" class="text-red-400">
        ({{ card.error_code }})
      </span>
    </div>
  </div>
</template>

<style scoped>
.run-card {
  container-name: run-card;
  container-type: inline-size;
}

@container run-card (max-width: 26rem) {
  .run-card[data-has-actions='true'] .run-card__context,
  .run-card[data-has-actions='true'] .run-card__tokens {
    display: none;
  }
}

@container run-card (max-width: 18rem) {
  .run-card[data-has-actions='true'] .run-card__duration {
    display: none;
  }
}

@container run-card (max-width: 14rem) {
  .run-card[data-has-actions='true'] .run-card__status {
    display: none;
  }
}
</style>
