<script setup lang="ts">
import { computed } from 'vue'
import { Badge, Button } from '@/components/ui'
import type { ResourceObservationSummary } from '@/types'

const props = defineProps<{ observation: ResourceObservationSummary }>()
const emit = defineEmits<{ download: [artifactId: string, name: string] }>()

const coverage = computed(() => {
  const raw = props.observation.coverage
  const list = (key: string) => Array.isArray(raw[key])
    ? (raw[key] as unknown[]).filter((value): value is string => typeof value === 'string')
    : []
  return {
    queried: list('queried_types'),
    reported: list('reported_types'),
    missing: list('missing_types'),
    partial: list('partial_types'),
    stale: raw.stale === true,
    subtotal: typeof raw.reported_storage_subtotal_bytes === 'number'
      ? raw.reported_storage_subtotal_bytes
      : null,
    truncated: raw.truncated === true,
  }
})
const outcomeLabel = computed(() => {
  if (coverage.value.stale) return 'Stale measurement'
  if (props.observation.outcome === 'partial') return 'Partial coverage'
  if (props.observation.outcome === 'no_data') return 'No measurements returned'
  if (props.observation.outcome === 'failed') return 'Measurement failed'
  return 'Measurement recorded'
})

function formatDate(value: string | null): string {
  if (!value) return 'Not reported'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? 'Not reported' : date.toLocaleString()
}

function formatBytes(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0) return 'Unknown'
  if (value === 0) return '0 bytes'
  const units = ['bytes', 'KB', 'MB', 'GB', 'TB', 'PB']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / (1024 ** index)).toFixed(index === 0 ? 0 : 2)} ${units[index]}`
}
</script>

<template>
  <article class="grid gap-2 rounded-md border border-border bg-background p-3 text-xs" data-testid="resource-observation-card">
    <div class="flex flex-wrap items-center gap-2">
      <strong>{{ formatDate(observation.retrieved_at) }}</strong>
      <Badge :variant="coverage.stale || observation.outcome === 'partial' || observation.outcome === 'failed' ? 'outline' : 'default'">
        {{ outcomeLabel }}
      </Badge>
    </div>
    <dl class="grid gap-2 sm:grid-cols-2">
      <div><dt class="text-muted-foreground">Source</dt><dd>{{ observation.source === 'aws_s3_bucket_storage' ? 'AWS CloudWatch daily storage metrics' : observation.source }}</dd></div>
      <div><dt class="text-muted-foreground">Measurement time</dt><dd>{{ formatDate(observation.measurement_at) }}</dd></div>
      <div><dt class="text-muted-foreground">Reported storage subtotal</dt><dd>{{ formatBytes(coverage.subtotal) }}</dd></div>
      <div><dt class="text-muted-foreground">Coverage</dt><dd>{{ coverage.reported.length }} of {{ coverage.queried.length }} storage types · {{ coverage.missing.length }} missing · {{ coverage.partial.length }} partial</dd></div>
    </dl>
    <p v-if="coverage.missing.length" class="break-words text-muted-foreground">Missing types: {{ coverage.missing.join(', ') }}</p>
    <p v-if="coverage.partial.length" class="break-words text-muted-foreground">Partial types: {{ coverage.partial.join(', ') }}</p>
    <p v-if="coverage.truncated" class="text-muted-foreground">Coverage was shortened at the configured result limit.</p>
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-muted-foreground">
        Evidence: {{ observation.evidence_status ?? 'not retained' }}
      </span>
      <Button
        v-if="observation.evidence && observation.evidence_status === 'ready'"
        type="button"
        size="sm"
        variant="outline"
        aria-label="Download retained S3 storage evidence"
        @click="emit('download', observation.evidence.id, 's3-bucket-storage.json')"
      >
        Download retained evidence
      </Button>
    </div>
  </article>
</template>
