<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { generateRequestId } from '@/lib/requestId'
import { logicalFilesApi, workspaceApi } from '@/services/api'
import type { ArtifactSummary, LogicalFileSummary } from '@/types'

const props = defineProps<{
  conversationId: string
  artifacts: ArtifactSummary[]
  readOnly: boolean
}>()

const files = ref<LogicalFileSummary[]>([])
const selectedFileId = ref<string | null>(null)
const selectedArtifactId = ref('')
const source = ref('')
const baseVersionId = ref<string | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const status = ref<string | null>(null)
const selectedFile = computed(() => files.value.find((file) => file.id === selectedFileId.value) ?? null)
const textArtifacts = computed(() => props.artifacts.filter(
  (artifact) => artifact.status === 'ready' && artifact.media_type === 'text/plain',
))
const alreadyCaptured = computed(() => new Set(files.value.map((file) => file.current_artifact.id)))

function messageFor(err: unknown, fallback: string): string {
  const detail = err as { response?: { data?: { detail?: { error?: { message?: string } } } } }
  return detail.response?.data?.detail?.error?.message ?? fallback
}

async function loadFiles(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const response = await logicalFilesApi.list(props.conversationId)
    files.value = response.data.items
    if (selectedFileId.value && !files.value.some((file) => file.id === selectedFileId.value)) {
      selectedFileId.value = null
      baseVersionId.value = null
      source.value = ''
    }
  } catch (err) {
    error.value = messageFor(err, 'Source files could not be loaded.')
  } finally {
    loading.value = false
  }
}

async function readArtifact(artifactId: string, expectedDigest: string): Promise<string> {
  let offset = 0
  let content = ''
  let contentBytes = 0
  while (true) {
    const response = await workspaceApi.readFile(props.conversationId, artifactId, {
      offset,
      max_bytes: 16 * 1024,
    })
    if (response.data.artifact_id !== artifactId || response.data.sha256 !== expectedDigest) {
      throw new Error('The captured source changed while it was being read.')
    }
    content += response.data.content
    contentBytes += new TextEncoder().encode(response.data.content).byteLength
    if (contentBytes > 256 * 1024) throw new Error('This source exceeds the 256 KiB editor limit.')
    if (response.data.next_offset === null) return content
    offset = response.data.next_offset
  }
}

async function selectFile(file: LogicalFileSummary): Promise<void> {
  selectedFileId.value = file.id
  baseVersionId.value = file.current_version_id
  source.value = ''
  error.value = null
  status.value = null
  try {
    source.value = await readArtifact(file.current_artifact.id, file.current_artifact.sha256)
  } catch (err) {
    error.value = messageFor(err, 'The captured source could not be read.')
    baseVersionId.value = null
  }
}

async function createFromSelectedArtifact(): Promise<void> {
  if (!selectedArtifactId.value || props.readOnly) return
  const artifact = textArtifacts.value.find((item) => item.id === selectedArtifactId.value)
  if (!artifact || alreadyCaptured.value.has(artifact.id)) return
  saving.value = true
  error.value = null
  status.value = null
  try {
    const response = await logicalFilesApi.createFromArtifact(
      props.conversationId,
      artifact.id,
      generateRequestId(),
    )
    await loadFiles()
    await selectFile(files.value.find((item) => item.id === response.data.file.id)!)
    status.value = 'A new immutable source version was captured.'
  } catch (err) {
    error.value = messageFor(err, 'The artifact could not be captured as a source file.')
  } finally {
    saving.value = false
  }
}

async function saveSource(): Promise<void> {
  const file = selectedFile.value
  const base = baseVersionId.value
  if (!file || !base || props.readOnly || saving.value) return
  saving.value = true
  error.value = null
  status.value = null
  try {
    const response = await logicalFilesApi.replaceContent(
      props.conversationId,
      file.id,
      base,
      generateRequestId(),
      source.value,
    )
    baseVersionId.value = response.data.id
    await loadFiles()
    status.value = 'Saved as a new immutable version.'
  } catch (err) {
    error.value = messageFor(err, 'The source could not be saved. Reload the current version and try again.')
  } finally {
    saving.value = false
  }
}

watch(() => props.conversationId, () => {
  files.value = []
  selectedFileId.value = null
  baseVersionId.value = null
  source.value = ''
  void loadFiles()
})

onMounted(() => void loadFiles())
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden border-t border-border pt-3" data-testid="logical-files">
    <div class="flex shrink-0 items-center justify-between gap-2">
      <h3 class="text-sm font-medium">Source files</h3>
      <span class="text-xs text-muted-foreground">{{ files.length }} saved</span>
    </div>

    <p v-if="error" class="shrink-0 text-xs text-destructive" role="alert">{{ error }}</p>
    <p v-if="status" class="shrink-0 text-xs text-muted-foreground" role="status">{{ status }}</p>
    <p v-if="loading" class="text-xs text-muted-foreground" role="status">Loading source files…</p>

    <div v-if="!readOnly && textArtifacts.length" class="flex shrink-0 gap-2">
      <label class="sr-only" for="source-artifact">Choose captured source</label>
      <select
        id="source-artifact"
        v-model="selectedArtifactId"
        class="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-xs"
      >
        <option value="">Choose a captured text artifact</option>
        <option v-for="artifact in textArtifacts" :key="artifact.id" :value="artifact.id">
          {{ artifact.name }}
        </option>
      </select>
      <button
        type="button"
        data-testid="capture-source"
        class="shrink-0 rounded-md border border-border px-2 py-1.5 text-xs text-primary hover:bg-muted disabled:opacity-50"
        :disabled="!selectedArtifactId || alreadyCaptured.has(selectedArtifactId) || saving"
        @click="createFromSelectedArtifact"
      >
        Save as source
      </button>
    </div>

    <ul v-if="files.length" class="shrink-0 space-y-1 overflow-y-auto" aria-label="Saved source files">
      <li v-for="file in files" :key="file.id">
        <button
          type="button"
          class="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :aria-pressed="selectedFileId === file.id"
          @click="selectFile(file)"
        >
          <span class="min-w-0 truncate">{{ file.name }}</span>
          <span class="shrink-0 text-muted-foreground">{{ file.current_artifact.sha256.slice(0, 12) }}</span>
        </button>
      </li>
    </ul>
    <p v-else-if="!loading" class="text-xs text-muted-foreground" role="status">
      Capture a text artifact to start a versioned source file.
    </p>

    <div v-if="selectedFile" class="flex min-h-0 flex-1 flex-col gap-2">
      <label class="sr-only" for="source-editor">Edit {{ selectedFile.name }}</label>
      <textarea
        id="source-editor"
        v-model="source"
        class="min-h-32 flex-1 resize-y rounded-md border border-border bg-background p-2 font-mono text-xs leading-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :aria-label="`Edit ${selectedFile.name}`"
        :readonly="readOnly || baseVersionId === null"
        spellcheck="false"
      />
      <div class="flex shrink-0 flex-wrap items-center gap-2">
        <button
          v-if="!readOnly"
          type="button"
          data-testid="save-source-version"
          class="rounded-md border border-border px-3 py-1.5 text-xs text-primary hover:bg-muted disabled:opacity-50"
          :disabled="saving || baseVersionId === null || source === ''"
          @click="saveSource"
        >
          {{ saving ? 'Saving…' : 'Save new version' }}
        </button>
        <button
          type="button"
          class="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground disabled:opacity-50"
          disabled
          aria-describedby="sandbox-unavailable"
        >
          Run this version
        </button>
      </div>
      <p id="sandbox-unavailable" class="shrink-0 text-xs text-muted-foreground">
        Execution is unavailable until the OpenSandbox gVisor profile passes host qualification.
      </p>
    </div>
  </section>
</template>
