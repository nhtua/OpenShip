<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui'
import type { RunCard } from '@/types'

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
    <span v-if="card.usage" class="text-slate-500">{{ card.usage }}</span>
    <span v-if="card.error_code" class="text-red-400">
      ({{ card.error_code }})
    </span>
  </div>
</template>
