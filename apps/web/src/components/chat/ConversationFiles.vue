<script setup lang="ts">
import { computed } from 'vue'
import Icon from '@/components/common/Icon.vue'
import type { ArtifactSummary, ConversationWorkspaceSummary } from '@/types'

const props = defineProps<{
  conversationId: string
  workspace: ConversationWorkspaceSummary | null
  files: ArtifactSummary[]
  loading: boolean
  loadingMore: boolean
  error: string | null
  nextCursor: string | null
  selectedArtifactId: string | null
  selectedContent: string | null
  selectedNextOffset: number | null
  readingArtifactId: string | null
  deletingArtifactId: string | null
}>()

const emit = defineEmits<{
  'load-more': [conversationId: string]
  'select-file': [conversationId: string, artifactId: string]
  'read-more': [conversationId: string, artifactId: string]
  'download-file': [artifactId: string, name: string]
  'delete-file': [conversationId: string, artifactId: string]
}>()

const fileCount = computed(() => props.workspace?.quota.committed_count ?? props.files.length)
const quotaLabel = computed(() => {
  if (!props.workspace) return ''
  return `${formatBytes(props.workspace.quota.committed_bytes)} of ${formatBytes(props.workspace.quota.max_bytes)} used`
})
const selectedFile = computed(() =>
  props.files.find((file) => file.id === props.selectedArtifactId) ?? null,
)
const selectedDisplayContent = computed(() => {
  const content = props.selectedContent
  const file = selectedFile.value
  if (content === null || !file || !isJsonFile(file)) return content ?? ''

  try {
    return JSON.stringify(JSON.parse(content), null, 2)
  } catch {
    // A limited file read can end in the middle of a JSON document. Keep that
    // chunk readable as text until the rest of the file has been loaded.
    return content
  }
})

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB']
  let amount = value / 1024
  let index = 0
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024
    index += 1
  }
  return `${Number(amount.toFixed(1))} ${units[index]}`
}

function statusLabel(status: ArtifactSummary['status']): string {
  switch (status) {
    case 'ready': return 'Ready'
    case 'unavailable': return 'Unavailable'
    case 'deleting': return 'Deleting'
    case 'deleted': return 'Deleted'
    default: return 'Publishing'
  }
}

function canDelete(file: ArtifactSummary): boolean {
  return file.status === 'ready' || file.status === 'unavailable'
}

function isJsonFile(file: ArtifactSummary): boolean {
  return file.name.toLowerCase().endsWith('.json') || file.media_type.toLowerCase().includes('json')
}
</script>

<template>
  <section
    class="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-4 pb-4"
    data-testid="conversation-files"
  >
    <div class="flex shrink-0 items-center gap-2 border-b border-border pb-3 text-xs text-muted-foreground">
      <span class="font-medium text-foreground">{{ fileCount }} {{ fileCount === 1 ? 'file' : 'files' }}</span>
      <span v-if="quotaLabel" class="ml-auto truncate">{{ quotaLabel }}</span>
    </div>
    <p v-if="error" class="shrink-0 text-xs text-destructive" role="alert">{{ error }}</p>
    <p v-if="workspace?.status === 'unavailable'" class="shrink-0 text-sm text-destructive" role="status">
      Workspace storage is unavailable.
    </p>
    <p v-else-if="workspace?.status === 'deleted'" class="shrink-0 text-sm text-muted-foreground" role="status">
      This conversation workspace has been purged.
    </p>
    <p v-else-if="loading && !workspace" class="shrink-0 py-2 text-sm text-muted-foreground" role="status">
      Loading conversation files…
    </p>
    <p v-else-if="workspace?.status === 'available' && files.length === 0" class="shrink-0 py-2 text-sm text-muted-foreground">
      No saved files in this conversation.
    </p>

    <ul v-if="files.length" data-testid="conversation-file-list" class="max-h-56 shrink-0 space-y-1 overflow-y-auto">
      <li
        v-for="file in files"
        :key="file.id"
        class="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 rounded-md border border-transparent px-2 py-2 hover:border-border hover:bg-muted/50"
        data-testid="conversation-file"
        :data-artifact-id="file.id"
      >
        <span class="col-span-2 min-w-0 break-words text-sm" :title="file.name">{{ file.name }}</span>
        <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span class="shrink-0 text-xs text-muted-foreground">{{ statusLabel(file.status) }}</span>
          <span class="shrink-0 text-xs text-muted-foreground">{{ formatBytes(file.size_bytes) }}</span>
        </div>
        <div class="flex shrink-0 items-center gap-1">
          <button
            v-if="file.status === 'ready'"
            type="button"
            class="inline-flex size-7 items-center justify-center rounded text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :aria-label="selectedArtifactId === file.id ? `Close preview ${file.name}` : `Preview ${file.name}`"
            :title="selectedArtifactId === file.id ? `Close preview ${file.name}` : `Preview ${file.name}`"
            :aria-pressed="selectedArtifactId === file.id"
            @click="emit('select-file', conversationId, file.id)"
          >
            <Icon
              :name="readingArtifactId === file.id ? 'spinner' : selectedArtifactId === file.id ? 'times' : 'eye'"
              :class="{ 'animate-spin': readingArtifactId === file.id }"
            />
          </button>
          <button
            v-if="file.status === 'ready'"
            type="button"
            class="inline-flex size-7 items-center justify-center rounded text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :aria-label="`Download ${file.name}`"
            :title="`Download ${file.name}`"
            @click="emit('download-file', file.id, file.name)"
          >
            <Icon name="download" />
          </button>
          <button
            v-if="canDelete(file)"
            type="button"
            class="inline-flex size-7 items-center justify-center rounded text-destructive hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :aria-label="`Delete ${file.name}`"
            :title="`Delete ${file.name}`"
            :disabled="deletingArtifactId === file.id"
            @click="emit('delete-file', conversationId, file.id)"
          >
            <Icon
              :name="deletingArtifactId === file.id ? 'spinner' : 'trash'"
              :class="{ 'animate-spin': deletingArtifactId === file.id }"
            />
          </button>
        </div>
      </li>
    </ul>

    <div
      v-if="selectedFile && selectedContent !== null"
      class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-md border border-border bg-background p-3"
      data-testid="selected-file-content"
    >
      <div class="mb-2 flex min-w-0 shrink-0 flex-wrap items-baseline justify-between gap-2">
        <h3 class="min-w-0 flex-1 break-words text-sm font-medium">{{ selectedFile.name }}</h3>
        <span class="shrink-0 text-xs text-muted-foreground">{{ selectedFile.size_bytes }} bytes</span>
      </div>
      <pre class="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5">{{ selectedDisplayContent }}</pre>
      <button
        v-if="selectedNextOffset !== null"
        type="button"
        class="mt-2 shrink-0 rounded px-2 py-1 text-xs text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :disabled="readingArtifactId === selectedFile.id"
        data-testid="read-more-file"
        @click="emit('read-more', conversationId, selectedFile.id)"
      >
        {{ readingArtifactId === selectedFile.id ? 'Reading…' : 'Read next section' }}
      </button>
    </div>

    <button
      v-if="nextCursor"
      type="button"
      class="shrink-0 rounded px-2 py-1 text-xs text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      data-testid="load-more-files"
      :disabled="loadingMore"
      @click="emit('load-more', conversationId)"
    >
      {{ loadingMore ? 'Loading…' : 'Load more files' }}
    </button>
  </section>
</template>
