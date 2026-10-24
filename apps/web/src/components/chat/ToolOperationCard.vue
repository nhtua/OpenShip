<script setup lang="ts">
import { computed, ref } from 'vue'
import { Badge, Button } from '@/components/ui'
import type { SearchResultItem, ToolOperationSummary } from '@/types'
import S3StorageObservation from './S3StorageObservation.vue'
import S3MetadataObservation from './S3MetadataObservation.vue'
import AwsConnectionCheck from './AwsConnectionCheck.vue'
import AwsReadObservation from './AwsReadObservation.vue'

const props = defineProps<{ operation: ToolOperationSummary }>()
const emit = defineEmits<{
  download: [artifactId: string, name: string]
  cancel: [runId: string]
  'layout-change': []
}>()

const expanded = ref(false)
const fullSnippets = ref(false)
const isStorageObservation = computed(() => props.operation.capability_id === 'aws_s3_bucket_storage')
const isMetadataObservation = computed(() => props.operation.capability_id === 'aws_s3_bucket_metadata')
const isConnectionCheck = computed(() => props.operation.capability_id === 'aws_check_connection')
const isAwsRead = computed(() => props.operation.capability_id === 'aws_read')

function toolLabel(id: string): string {
  if (id === 'brave_web_search') return 'Brave Search'
  if (id === 'tavily_web_search') return 'Tavily Search'
  if (id === 'aws_s3_bucket_storage') return 'S3 bucket storage'
  if (id === 'aws_s3_bucket_metadata') return 'S3 bucket metadata'
  if (id === 'aws_check_connection') return 'AWS connection check'
  if (id === 'aws_read') return 'AWS read'
  return id
}

const query = computed(() => props.operation.query ?? props.operation.result?.query ?? null)
const results = computed<SearchResultItem[]>(() => props.operation.result?.items ?? [])
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
  if (['failed', 'interrupted'].includes(props.operation.status)) return 'destructive'
  if (props.operation.status === 'cancelled') return 'secondary'
  return props.operation.status === 'succeeded' ? 'default' : 'outline'
})
const canCancel = computed(() =>
  ['admitted', 'dispatching', 'running'].includes(props.operation.status),
)
const summary = computed(() => {
  if (isMetadataObservation.value) return props.operation.result?.bucket_name ?? 'S3 bucket metadata'
  if (isStorageObservation.value) {
    return props.operation.result?.bucket_name ?? 'CloudWatch storage measurement'
  }
  if (isConnectionCheck.value) return props.operation.result?.account_id ?? 'AWS role identity'
  if (isAwsRead.value) {
    const result = props.operation.result
    return result?.service && result.operation ? `${result.service}.${result.operation}` : 'AWS result details'
  }
  const text = query.value || 'Search query unavailable'
  return text
})
const toggleLabel = computed(() =>
  isMetadataObservation.value || isAwsRead.value
    ? `${expanded.value ? 'Hide' : 'Show'} details for ${summary.value}`
    : `${expanded.value ? 'Hide' : 'Show'} results for ${query.value || 'this search'}`,
)

const observedAt = computed(() => {
  const value = props.operation.result?.retrieved_at ?? props.operation.result?.observed_at
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.valueOf())
    ? ''
    : `${isMetadataObservation.value ? 'Retrieved' : 'Searched'} ${date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`
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

function sourceDomain(value: string): string {
  return safeHref(value)?.replace(/^https?:\/\//, '').split('/')[0].replace(/^www\./, '') ?? ''
}

function toggleExpanded() {
  expanded.value = !expanded.value
  emit('layout-change')
}

function toggleSnippets() {
  fullSnippets.value = !fullSnippets.value
  emit('layout-change')
}
</script>

<template>
  <section
    class="overflow-hidden rounded-lg border border-border bg-card text-card-foreground"
    :data-operation-id="operation.id"
    data-testid="tool-operation-card"
  >
    <div class="flex min-w-0 items-center gap-2 px-3 py-2">
      <button
        type="button"
        class="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :aria-label="toggleLabel"
        :aria-expanded="expanded"
        @click="toggleExpanded"
      >
        <span class="flex min-w-0 flex-1 items-center gap-2 text-sm">
          <span class="shrink-0 font-medium">{{ toolLabel(operation.capability_id) }}</span>
          <span aria-hidden="true" class="shrink-0 text-muted-foreground">·</span>
          <span class="min-w-0 flex-1 truncate">{{ summary }}</span>
        </span>
        <span v-if="operation.status === 'succeeded' && !isStorageObservation && !isMetadataObservation && !isConnectionCheck && !isAwsRead" class="shrink-0 text-xs text-muted-foreground">
          {{ results.length }} {{ results.length === 1 ? 'result' : 'results' }}
        </span>
        <Badge :variant="statusVariant" data-testid="operation-status" class="h-5 shrink-0 text-[10px]">
          {{ statusLabel }}
        </Badge>
      </button>
      <Button
        v-if="canCancel"
        type="button"
        size="sm"
        variant="outline"
        class="h-7 shrink-0 px-2 text-xs"
        :aria-label="`Cancel ${toolLabel(operation.capability_id)}`"
        @click.stop="emit('cancel', operation.run_id)"
      >
        Cancel
      </Button>
    </div>

    <p v-if="operation.error" class="px-3 pb-2 text-xs text-destructive" role="alert">
      {{ operation.error.message }}
    </p>

    <div v-if="expanded" class="border-t border-border px-3 py-3">
      <div v-if="!isStorageObservation && !isAwsRead" class="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>{{ observedAt || 'Search details' }}</span>
        <span v-if="operation.result?.truncated">More results are available from the search service.</span>
      </div>

      <dl v-if="!isAwsRead" data-testid="operation-provenance" class="mb-3 grid min-w-0 gap-1 rounded-md bg-muted/40 p-2 text-xs">
        <div class="flex min-w-0 flex-wrap gap-x-2">
          <dt class="text-muted-foreground">Tool</dt>
          <dd><code>{{ operation.capability_id }}@{{ operation.capability_version }}</code></dd>
        </div>
        <div class="flex min-w-0 flex-wrap gap-x-2">
          <dt class="text-muted-foreground">Manifest</dt>
          <dd class="min-w-0 break-all"><code>{{ operation.manifest_digest }}</code></dd>
        </div>
        <div class="flex min-w-0 flex-wrap gap-x-2">
          <dt class="text-muted-foreground">Observation</dt>
          <dd class="min-w-0 break-all"><code>{{ operation.id }}</code></dd>
        </div>
      </dl>

      <AwsReadObservation
        v-if="isAwsRead && operation.status === 'succeeded' && operation.result"
        :result="operation.result"
        :artifacts="operation.artifacts"
        @download="(artifactId, name) => emit('download', artifactId, name)"
        @layout-change="emit('layout-change')"
      />
      <S3StorageObservation
        v-else-if="isStorageObservation && operation.status === 'succeeded' && operation.result"
        :result="operation.result"
        :artifacts="operation.artifacts"
        @download="(artifactId, name) => emit('download', artifactId, name)"
      />
      <S3MetadataObservation
        v-else-if="isMetadataObservation && operation.status === 'succeeded' && operation.result"
        :result="operation.result"
        :artifacts="operation.artifacts"
        @download="(artifactId, name) => emit('download', artifactId, name)"
      />
      <AwsConnectionCheck
        v-else-if="isConnectionCheck && operation.status === 'succeeded' && operation.result"
        :result="operation.result"
      />

      <p v-else-if="!isStorageObservation && !isMetadataObservation && !isConnectionCheck && !isAwsRead && !query" class="mb-3 text-xs text-muted-foreground">
        The search query is not available in this saved history.
      </p>
      <p
        v-else-if="!isStorageObservation && !isMetadataObservation && !isConnectionCheck && !isAwsRead && operation.status === 'succeeded' && results.length === 0"
        class="py-2 text-sm text-muted-foreground"
      >
        No results returned.
      </p>
      <ol v-else-if="!isStorageObservation && !isMetadataObservation && !isConnectionCheck && !isAwsRead && operation.status === 'succeeded'" class="space-y-3">
        <li v-for="item in results" :key="`${item.rank}-${item.url}`" class="min-w-0">
          <a
            v-if="safeHref(item.url)"
            :href="safeHref(item.url)!"
            target="_blank"
            rel="noopener noreferrer"
            class="break-words text-sm font-medium text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
          >
            {{ item.title }}
          </a>
          <span v-else class="break-words text-sm font-medium">{{ item.title }}</span>
          <span v-if="sourceDomain(item.url)" class="ml-2 text-xs text-muted-foreground">
            {{ sourceDomain(item.url) }}
          </span>
          <p
            v-if="item.snippet"
            class="mt-1 whitespace-pre-wrap text-sm leading-5 text-muted-foreground"
            :class="{ 'line-clamp-2': !fullSnippets }"
          >
            {{ item.snippet }}
          </p>
        </li>
      </ol>

      <div class="mt-3 flex flex-wrap items-center gap-2">
        <Button
          v-if="results.some((item) => item.snippet.length > 180)"
          type="button"
          size="sm"
          variant="ghost"
          class="h-7 px-2 text-xs"
          @click="toggleSnippets"
        >
          {{ fullSnippets ? 'Show shorter snippets' : 'Show full snippets' }}
        </Button>
        <div
          v-if="!isStorageObservation && !isMetadataObservation && !isAwsRead"
          v-for="artifact in operation.artifacts.filter((item) => item.status === 'ready')"
          :key="artifact.id"
          class="flex min-w-0 items-center gap-2"
        >
          <Button
            type="button"
            size="sm"
            variant="outline"
            :aria-label="`Download ${artifact.name}`"
            @click="emit('download', artifact.id, artifact.name)"
          >
            Download {{ artifact.name }}
          </Button>
          <code
            data-testid="artifact-reference"
            :title="artifact.id"
            class="max-w-36 truncate text-[10px] text-muted-foreground"
          >
            {{ artifact.id }}
          </code>
        </div>
      </div>
    </div>
  </section>
</template>
