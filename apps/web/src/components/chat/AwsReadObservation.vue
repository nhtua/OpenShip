<script setup lang="ts">
import { computed } from 'vue'
import { Button } from '@/components/ui'
import type { ArtifactSummary, ToolOperationSummary } from '@/types'

const props = defineProps<{
  result: NonNullable<ToolOperationSummary['result']>
  artifacts: ArtifactSummary[]
}>()

const emit = defineEmits<{
  download: [artifactId: string, name: string]
  'layout-change': []
}>()

const previewText = computed(() => JSON.stringify(props.result.preview ?? {}, null, 2))
const retrievedAt = computed(() => {
  const value = props.result.retrieved_at
  if (!value) return 'Retrieval time unavailable'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? 'Retrieval time unavailable' : `Retrieved ${date.toLocaleString()}`
})
const outcomeLabel = computed(() => {
  if (props.result.outcome === 'partial') return 'Partial results'
  if (props.result.outcome === 'no_data') return 'No data returned'
  return 'Read completed'
})
const readyEvidence = computed(() => props.artifacts.filter((artifact) => artifact.status === 'ready'))
</script>

<template>
  <section class="grid min-w-0 gap-3 border-t border-border px-3 py-3" data-testid="aws-read-observation">
    <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
      <strong class="font-medium">{{ result.service }}.{{ result.operation }}</strong>
      <span class="rounded-full border border-border px-2 py-1">{{ outcomeLabel }}</span>
    </div>

    <dl class="grid min-w-0 gap-1 rounded-md bg-muted/40 p-2 text-xs sm:grid-cols-2">
      <div class="flex min-w-0 flex-wrap gap-x-2">
        <dt class="text-muted-foreground">Account</dt>
        <dd>{{ result.account_id || 'Unknown' }}</dd>
      </div>
      <div class="flex min-w-0 flex-wrap gap-x-2">
        <dt class="text-muted-foreground">Region</dt>
        <dd>{{ result.region || 'Not applicable' }}</dd>
      </div>
      <div class="flex min-w-0 flex-wrap gap-x-2">
        <dt class="text-muted-foreground">Connector</dt>
        <dd class="break-all">{{ result.connector_id || 'Unknown' }}<template v-if="result.connector_revision"> · revision {{ result.connector_revision }}</template></dd>
      </div>
      <div class="flex min-w-0 flex-wrap gap-x-2">
        <dt class="text-muted-foreground">Retrieved</dt>
        <dd>{{ retrievedAt }}</dd>
      </div>
    </dl>

    <p v-if="result.outcome === 'partial'" class="text-xs text-muted-foreground" data-testid="aws-read-partial">
      AWS returned partial results. Fields or pages that were not returned remain unknown.
    </p>
    <p v-if="result.next_cursor" class="text-xs text-muted-foreground" data-testid="aws-read-continuation">
      More results are available to continue reading.
    </p>
    <p v-if="result.preview_truncated" class="text-xs text-muted-foreground">
      This is a bounded preview. Download the evidence for the complete returned page.
    </p>

    <pre class="max-h-96 min-w-0 overflow-auto rounded-md border border-border bg-background p-3 text-xs leading-5"><code>{{ previewText }}</code></pre>

    <div v-if="readyEvidence.length" class="flex flex-wrap items-center gap-2">
      <Button
        v-for="artifact in readyEvidence"
        :key="artifact.id"
        type="button"
        size="sm"
        variant="outline"
        :aria-label="`Download ${artifact.name}`"
        @click="emit('download', artifact.id, artifact.name)"
      >
        Download {{ artifact.name }}
      </Button>
    </div>
    <p v-else class="text-xs text-muted-foreground">No retained evidence is available.</p>
  </section>
</template>
