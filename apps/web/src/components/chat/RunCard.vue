<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui'
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
const providerText = computed(() => {
  const model: RunModelSummary | null = props.card.model
  if (!model) return 'Provider not recorded'
  // The model that actually served is stated when it differs from the one
  // requested; a destination is named as a label, never as a URL.
  const served = model.actual_model || model.requested_model
  const destination = model.destination_label
    ? ` at ${model.destination_label}`
    : ''
  return `${model.label} · ${served}${destination}`
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
    class="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-800/50 px-3 py-2 text-xs"
  >
    <span class="text-slate-400">Run:</span>
    <Badge :variant="statusVariant" class="h-5 text-[10px]">
      {{ statusLabel }}
    </Badge>
    <span v-if="card.started_at" class="text-slate-500">
      Started {{ new Date(card.started_at).toLocaleTimeString() }}
    </span>
    <span v-if="card.ended_at" class="text-slate-500">
      Ended {{ new Date(card.ended_at).toLocaleTimeString() }}
    </span>
    <span v-if="finished" class="text-slate-400">{{ providerText }}</span>
    <span v-if="attemptText" class="text-slate-500">
      {{ attemptText }}
    </span>
    <span v-if="finished" class="text-slate-500">{{ usageText }}</span>
    <!-- The settled safe copy, never a provider's own body. -->
    <span v-if="card.error_message" class="text-red-400">
      ({{ card.error_message }})
    </span>
    <span v-else-if="card.error_code" class="text-red-400">
      ({{ card.error_code }})
    </span>
  </div>
</template>
