<script setup lang="ts">
import { computed, ref } from 'vue'
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

const expanded = ref(false)
const fileCount = computed(() => props.workspace?.quota.committed_count ?? props.files.length)
const quotaLabel = computed(() => {
  if (!props.workspace) return ''
  return `${formatBytes(props.workspace.quota.committed_bytes)} of ${formatBytes(props.workspace.quota.max_bytes)} used`
})
const selectedFile = computed(() =>
  props.files.find((file) => file.id === props.selectedArtifactId) ?? null,
)

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
</script>

<template>
  <section
    class="mx-4 mb-2 min-w-0 rounded-lg border border-border bg-card text-card-foreground md:mx-6"
    data-testid="conversation-files"
  >
    <button
      type="button"
      class="flex min-h-10 w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      :aria-expanded="expanded"
      aria-controls="conversation-files-panel"
      data-testid="conversation-files-toggle"
      @click="expanded = !expanded"
    >
      <i class="pi pi-folder-open shrink-0 text-muted-foreground" aria-hidden="true" />
      <span class="font-medium">Conversation files</span>
      <span class="text-xs text-muted-foreground">{{ fileCount }}</span>
      <span v-if="quotaLabel" class="ml-auto hidden truncate text-xs text-muted-foreground sm:inline">
        {{ quotaLabel }}
      </span>
      <span class="ml-auto text-xs text-muted-foreground sm:ml-0">
        {{ expanded ? 'Hide' : 'Show' }}
      </span>
      <i
        class="pi shrink-0 text-muted-foreground"
        :class="expanded ? 'pi-chevron-down' : 'pi-chevron-up'"
        aria-hidden="true"
      />
    </button>

    <div v-if="expanded" id="conversation-files-panel" class="border-t border-border px-3 py-3">
      <p v-if="error" class="mb-2 text-xs text-destructive" role="alert">{{ error }}</p>
      <p v-if="workspace?.status === 'unavailable'" class="mb-3 text-xs text-destructive" role="status">
        Workspace storage is unavailable.
      </p>
      <p v-else-if="workspace?.status === 'deleted'" class="mb-3 text-xs text-muted-foreground" role="status">
        This conversation workspace has been purged.
      </p>
      <p v-else-if="loading && !workspace" class="py-2 text-sm text-muted-foreground" role="status">
        Loading conversation files…
      </p>
      <p v-else-if="workspace?.status === 'available' && files.length === 0" class="py-2 text-sm text-muted-foreground">
        No saved files in this conversation.
      </p>

      <ul v-if="files.length" data-testid="conversation-file-list" class="max-h-56 space-y-1 overflow-y-auto">
        <li
          v-for="file in files"
          :key="file.id"
          class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-2 py-2 hover:bg-muted/50"
          :data-testid="`conversation-file`"
          :data-artifact-id="file.id"
        >
          <span class="min-w-0 flex-1 truncate text-sm" :title="file.name">{{ file.name }}</span>
          <span class="shrink-0 text-xs text-muted-foreground">{{ statusLabel(file.status) }}</span>
          <span class="shrink-0 text-xs text-muted-foreground">{{ formatBytes(file.size_bytes) }}</span>
          <code class="max-w-32 truncate text-[10px] text-muted-foreground" :title="file.id">{{ file.id }}</code>
          <button
            v-if="file.status === 'ready'"
            type="button"
            class="rounded px-2 py-1 text-xs text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :aria-label="`Read ${file.name} version ${file.id}`"
            :aria-pressed="selectedArtifactId === file.id"
            :disabled="readingArtifactId === file.id"
            @click="emit('select-file', conversationId, file.id)"
          >
            {{ readingArtifactId === file.id ? 'Reading…' : selectedArtifactId === file.id ? 'Selected' : 'Read' }}
          </button>
          <button
            v-if="file.status === 'ready'"
            type="button"
            class="rounded px-2 py-1 text-xs text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :aria-label="`Download ${file.name} version ${file.id}`"
            @click="emit('download-file', file.id, file.name)"
          >
            Download
          </button>
          <button
            v-if="canDelete(file)"
            type="button"
            class="rounded px-2 py-1 text-xs text-destructive hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :aria-label="`Delete ${file.name} version ${file.id}`"
            :disabled="deletingArtifactId === file.id"
            @click="emit('delete-file', conversationId, file.id)"
          >
            {{ deletingArtifactId === file.id ? 'Deleting…' : 'Delete' }}
          </button>
        </li>
      </ul>

      <div
        v-if="selectedFile && selectedContent !== null"
        class="mt-3 min-w-0 rounded-md border border-border bg-background p-3"
        data-testid="selected-file-content"
      >
        <div class="mb-2 flex min-w-0 flex-wrap items-baseline justify-between gap-2">
          <h3 class="min-w-0 truncate text-sm font-medium">{{ selectedFile.name }}</h3>
          <span class="text-xs text-muted-foreground">{{ selectedFile.size_bytes }} bytes</span>
        </div>
        <pre class="max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs leading-5">{{ selectedContent }}</pre>
        <div class="mt-2 flex min-w-0 flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
          <span>Version {{ selectedFile.id }}</span>
          <code class="min-w-0 break-all">SHA-256 {{ selectedFile.sha256 }}</code>
        </div>
        <button
          v-if="selectedNextOffset !== null"
          type="button"
          class="mt-2 rounded px-2 py-1 text-xs text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
        class="mt-3 rounded px-2 py-1 text-xs text-primary hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        data-testid="load-more-files"
        :disabled="loadingMore"
        @click="emit('load-more', conversationId)"
      >
        {{ loadingMore ? 'Loading…' : 'Load more files' }}
      </button>
    </div>
  </section>
</template>
