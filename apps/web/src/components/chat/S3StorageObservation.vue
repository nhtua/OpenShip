<script setup lang="ts">
import { computed } from 'vue'
import { Badge, Button } from '@/components/ui'
import type { ArtifactSummary, ToolOperationSummary } from '@/types'

const props = defineProps<{
  result: NonNullable<ToolOperationSummary['result']>
  artifacts: ArtifactSummary[]
}>()
const emit = defineEmits<{
  download: [artifactId: string, name: string]
}>()

const coverage = computed(() => {
  const raw = props.result.coverage ?? {}
  const list = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
  return {
    queried_types: list(raw.queried_types),
    reported_types: list(raw.reported_types),
    missing_types: list(raw.missing_types),
    partial_types: list(raw.partial_types),
    query_count: typeof raw.query_count === 'number' ? raw.query_count : 0,
    truncated: raw.truncated === true,
  }
})
const evidence = computed(() => props.artifacts.filter((artifact) => artifact.status === 'ready'))
const outcomeLabel = computed(() => {
  if (props.result.stale) return 'Stale measurement'
  if (props.result.outcome === 'partial') return 'Partial coverage'
  if (props.result.outcome === 'no_data') return 'No measurements returned'
  return 'Measurement recorded'
})

function formatDate(value?: string | null): string {
  if (!value) return 'Not reported'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? 'Not reported' : parsed.toLocaleString()
}

function formatBytes(value?: number | null): string {
  if (value == null || !Number.isFinite(value) || value < 0) return 'Unknown'
  if (value === 0) return '0 bytes'
  const units = ['bytes', 'KB', 'MB', 'GB', 'TB', 'PB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / (1024 ** index)).toFixed(index === 0 ? 0 : 2)} ${units[index]}`
}
</script>

<template>
  <section
    class="grid gap-3 rounded-md border border-border bg-background p-3"
    data-testid="s3-storage-observation"
    aria-label="Retained S3 storage observation"
  >
    <div class="flex flex-wrap items-center gap-2">
      <strong class="text-sm">S3 bucket storage</strong>
      <Badge :variant="result.stale || result.outcome === 'partial' ? 'outline' : 'default'">
        {{ outcomeLabel }}
      </Badge>
    </div>
    <dl class="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
      <div><dt class="text-muted-foreground">Source</dt><dd>AWS CloudWatch daily storage metrics</dd></div>
      <div><dt class="text-muted-foreground">Bucket</dt><dd class="break-all">{{ result.bucket_name || 'Not recorded' }}</dd></div>
      <div><dt class="text-muted-foreground">Account and region</dt><dd>{{ result.account_id || 'Unknown' }} · {{ result.region || 'Unknown' }}</dd></div>
      <div><dt class="text-muted-foreground">Retrieved</dt><dd>{{ formatDate(result.retrieved_at) }}</dd></div>
      <div><dt class="text-muted-foreground">Measured</dt><dd>{{ formatDate(result.measurement_at) }}</dd></div>
      <div><dt class="text-muted-foreground">Reported storage subtotal</dt><dd>{{ formatBytes(result.reported_storage_subtotal_bytes) }}</dd></div>
      <div><dt class="text-muted-foreground">Coverage</dt><dd>{{ coverage.reported_types?.length ?? 0 }} of {{ coverage.queried_types?.length ?? 0 }} types reported · {{ coverage.query_count ?? 0 }} queries</dd></div>
      <div><dt class="text-muted-foreground">Missing / partial</dt><dd>{{ coverage.missing_types?.length ?? 0 }} missing · {{ coverage.partial_types?.length ?? 0 }} partial</dd></div>
    </dl>
    <p v-if="coverage.missing_types?.length" class="break-words text-xs text-muted-foreground">
      Missing types: {{ coverage.missing_types.join(', ') }}
    </p>
    <p v-if="coverage.partial_types?.length" class="break-words text-xs text-muted-foreground">
      Partial types: {{ coverage.partial_types.join(', ') }}
    </p>
    <p v-if="coverage.truncated" class="text-xs text-muted-foreground">
      The result was truncated at the configured observation limit.
    </p>
    <div class="flex flex-wrap items-center gap-2" data-testid="observation-evidence">
      <span v-if="evidence.length" class="text-xs text-muted-foreground">Retained evidence</span>
      <Button
        v-for="artifact in evidence"
        :key="artifact.id"
        type="button"
        size="sm"
        variant="outline"
        :aria-label="`Download ${artifact.name}`"
        @click="emit('download', artifact.id, artifact.name)"
      >
        Download {{ artifact.name }}
      </Button>
      <span v-if="!evidence.length" class="text-xs text-muted-foreground">
        Evidence is not currently available.
      </span>
    </div>
  </section>
</template>
