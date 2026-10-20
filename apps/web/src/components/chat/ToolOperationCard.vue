<script setup lang="ts">
import { computed } from 'vue'
import { Badge, Button } from '@/components/ui'
import type { ToolOperationSummary } from '@/types'

const props = defineProps<{ operation: ToolOperationSummary }>()
const emit = defineEmits<{ download: [artifactId: string] }>()

function toolLabel(id: string): string {
  if (id === 'brave_web_search') return 'Brave Search'
  if (id === 'tavily_web_search') return 'Tavily Search'
  return id
}

const statusLabel = computed(() => {
  switch (props.operation.status) {
    case 'admitted': return 'Queued'
    case 'dispatching':
    case 'running': return 'Running'
    case 'succeeded': return 'Completed'
    case 'failed': return 'Failed'
    case 'cancelled': return 'Cancelled'
    default: return 'Interrupted'
  }
})

const statusVariant = computed(() => {
  if (props.operation.status === 'failed' || props.operation.status === 'interrupted') return 'destructive'
  if (props.operation.status === 'cancelled') return 'secondary'
  return props.operation.status === 'succeeded' ? 'default' : 'outline'
})

const observedAt = computed(() => {
  const value = props.operation.result?.observed_at
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.valueOf())
    ? ''
    : `Observed ${date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`
})

function safeHref(value: string): string | null {
  try {
    const url = new URL(value)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null
    return url.href
  } catch {
    return null
  }
}
</script>

<template>
  <section
    class="rounded-lg border border-slate-700 bg-slate-900/70 p-3 text-sm"
    :data-operation-id="operation.id"
    data-testid="tool-operation-card"
  >
    <div class="flex flex-wrap items-center gap-2">
      <span class="font-medium text-slate-100">{{ toolLabel(operation.capability_id) }}</span>
      <Badge :variant="statusVariant" class="h-5 text-[10px]">{{ statusLabel }}</Badge>
      <span v-if="observedAt" class="text-xs text-slate-400">{{ observedAt }}</span>
    </div>
    <p v-if="operation.result?.query" class="mt-2 text-xs text-slate-300">
      Query: {{ operation.result.query }}
    </p>
    <p v-if="operation.error" class="mt-2 text-xs text-red-300" role="alert">
      {{ operation.error.message }}
    </p>
    <ol v-if="operation.status === 'succeeded' && operation.result?.items?.length" class="mt-3 space-y-3">
      <li v-for="item in operation.result.items" :key="`${item.rank}-${item.url}`" class="min-w-0">
        <a
          v-if="safeHref(item.url)"
          :href="safeHref(item.url)!"
          target="_blank"
          rel="noopener noreferrer"
          class="font-medium text-sky-300 underline decoration-sky-700 underline-offset-2 hover:text-sky-200"
        >
          {{ item.title }}
        </a>
        <span v-else class="font-medium text-slate-300">{{ item.title }}</span>
        <p v-if="item.snippet" class="mt-1 whitespace-pre-wrap text-xs text-slate-400">
          {{ item.snippet }}
        </p>
      </li>
    </ol>
    <div v-if="operation.artifacts.some((artifact) => artifact.status === 'ready')" class="mt-3">
      <Button
        v-for="artifact in operation.artifacts.filter((item) => item.status === 'ready')"
        :key="artifact.id"
        size="sm"
        variant="outline"
        :aria-label="`Download ${artifact.name}`"
        @click="emit('download', artifact.id)"
      >
        Download {{ artifact.name }}
      </Button>
    </div>
  </section>
</template>
