<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui'
import ToolOperationCard from '@/components/chat/ToolOperationCard.vue'
import type {
  ProviderAttemptSummary,
  RunCard,
  RunModelSummary,
  UsageSummary,
} from '@/types'

interface Props {
  card: RunCard
}

const props = defineProps<Props>()
const emit = defineEmits<{ downloadArtifact: [artifactId: string, name: string] }>()
const kind = computed(() => props.card.kind ?? 'chat')

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

// Provider-reported counts only. Missing stays missing: nothing is
// estimated, and a reader can tell the difference.
const usageText = computed(() => {
  const usage: UsageSummary | null = props.card.usage
  if (!usage) return 'Not reported'
  const parts: string[] = []
  if (usage.input_tokens !== null) parts.push(`in ${usage.input_tokens}`)
  if (usage.output_tokens !== null) parts.push(`out ${usage.output_tokens}`)
  return parts.length ? parts.join(' · ') : 'Not reported'
})
</script>

<template>
  <div
    class="flex flex-col gap-2 rounded-md border border-slate-700 bg-slate-800/50 px-3 py-2 text-xs"
  >
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <span class="text-slate-400">{{ kind === 'tool' ? 'Search run:' : 'Run:' }}</span>
      <Badge :variant="statusVariant" class="h-5 text-[10px]">
        {{ statusLabel }}
      </Badge>
      <span v-if="card.started_at" class="text-slate-500">
        Started {{ new Date(card.started_at).toLocaleTimeString() }}
      </span>
      <span v-if="card.ended_at" class="text-slate-500">
        Ended {{ new Date(card.ended_at).toLocaleTimeString() }}
      </span>
      <template v-if="kind === 'chat' && finished">
        <span class="text-slate-400">{{ requestedText }}</span>
        <span v-if="servingText" class="text-slate-400">{{ servingText }}</span>
      </template>
      <span v-if="attemptText" class="text-slate-500">
        {{ attemptText }}
      </span>
      <span v-if="kind === 'chat' && finished" class="text-slate-500">{{ usageText }}</span>
      <!-- The settled safe copy, never a provider's own body. -->
      <span v-if="card.error_message" class="text-red-400">
        ({{ card.error_message }})
      </span>
      <span v-else-if="card.error_code" class="text-red-400">
        ({{ card.error_code }})
      </span>
    </div>
    <ToolOperationCard
      v-for="operation in card.operations"
      :key="operation.id"
      :operation="operation"
      @download="(artifactId) => emit('downloadArtifact', artifactId, operation.artifacts.find((item) => item.id === artifactId)?.name ?? 'search-results.json')"
    />
  </div>
</template>
