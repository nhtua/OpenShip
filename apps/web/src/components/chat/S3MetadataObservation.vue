<script setup lang="ts">
import { computed } from 'vue'
import { Badge, Button } from '@/components/ui'
import type { ArtifactSummary, ToolOperationSummary } from '@/types'

const props = defineProps<{
  result: NonNullable<ToolOperationSummary['result']>
  artifacts: ArtifactSummary[]
}>()
const emit = defineEmits<{ download: [artifactId: string, name: string] }>()

const evidence = computed(() => props.artifacts.filter((artifact) => artifact.status === 'ready'))

function stateLabel(value: string | undefined): string {
  switch (value) {
    case 'enabled': return 'Enabled'
    case 'suspended': return 'Suspended'
    case 'disabled': return 'Disabled'
    case 'not_enabled': return 'Not enabled'
    case 'not_reported': return 'Not reported'
    default: return 'Unknown'
  }
}

function formatDate(value?: string): string {
  if (!value) return 'Not reported'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? 'Not reported' : parsed.toLocaleString()
}
</script>

<template>
  <section
    class="grid gap-3 rounded-md border border-border bg-background p-3"
    data-testid="s3-metadata-observation"
    aria-label="Retained S3 bucket metadata observation"
  >
    <div class="flex flex-wrap items-center gap-2">
      <strong class="text-sm">S3 bucket metadata</strong>
      <Badge>Metadata recorded</Badge>
    </div>
    <dl class="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
      <div><dt class="text-muted-foreground">Bucket</dt><dd class="break-all">{{ result.bucket_name || 'Not recorded' }}</dd></div>
      <div><dt class="text-muted-foreground">Account and region</dt><dd>{{ result.account_id || 'Unknown' }} · {{ result.region || 'Unknown' }}</dd></div>
      <div><dt class="text-muted-foreground">Versioning</dt><dd>Versioning {{ stateLabel(result.versioning_status) }}</dd></div>
      <div><dt class="text-muted-foreground">MFA Delete</dt><dd>MFA Delete {{ stateLabel(result.mfa_delete_status) }}</dd></div>
      <div><dt class="text-muted-foreground">Retrieved</dt><dd>{{ formatDate(result.retrieved_at) }}</dd></div>
      <div><dt class="text-muted-foreground">AWS request count</dt><dd>{{ result.request_count ?? 'Not reported' }}</dd></div>
      <div><dt class="text-muted-foreground">Measurement time</dt><dd>Not reported</dd></div>
    </dl>
    <div class="flex flex-wrap items-center gap-2" data-testid="metadata-evidence">
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
