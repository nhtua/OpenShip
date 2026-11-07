<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Badge, Button } from '@/components/ui'
import type { ArtifactSummary, ResumeChatPythonRequest, Run, ToolOperationSummary } from '@/types'
import { artifactsApi, runsApi } from '@/services/api'

const props = withDefaults(defineProps<{
  operation: ToolOperationSummary
  canResume?: boolean
}>(), { canResume: false })
const emit = defineEmits<{
  download: [artifactId: string, name: string]
  cancel: [runId: string]
  resumed: [run: Run]
}>()

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

// Only the summary's own committed output references own a presentation: the
// model's own words (stdout, and any URL inside them) stay plain text, and a
// reference that is not part of this operation never becomes a fetch target.
const outputArtifacts = computed(() => {
  const outputs = props.operation.result?.outputs
  const byId = new Map(props.operation.artifacts.map((item) => [item.id, item]))
  const owned: ArtifactSummary[] = []
  const seen = new Set<string>()
  for (const item of Object.values(outputs ?? {})) {
    const artifact = byId.get(item.id)
    if (
      !artifact
      || artifact.status !== 'ready'
      || artifact.sha256 !== item.sha256
      || artifact.size_bytes !== item.size_bytes
      || artifact.media_type !== item.media_type
      || seen.has(item.id)
    ) continue
    seen.add(item.id)
    owned.push(artifact)
  }
  return owned
})
const previewArtifacts = computed(() =>
  props.operation.status === 'succeeded'
    ? outputArtifacts.value.filter(
        (artifact) => artifact.media_type === 'image/png' || artifact.media_type === 'image/jpeg',
      )
    : [],
)
const stdoutText = computed(() =>
  props.operation.status === 'succeeded' ? props.operation.result?.stdout ?? '' : '',
)
const stepSummary = computed(() => {
  const names = outputArtifacts.value.map((artifact) => artifact.name)
  return names.length ? names.join(', ') : 'Python step'
})

const previewUrls = ref<Map<string, string>>(new Map())
let previewGeneration = 0

function revokePreviewUrls(): void {
  const urls = [...previewUrls.value.values()]
  previewUrls.value = new Map()
  urls.forEach((url) => URL.revokeObjectURL(url))
}

async function loadPreviews(): Promise<void> {
  const generation = ++previewGeneration
  const operationId = props.operation.id
  for (const artifact of previewArtifacts.value) {
    // A newer operation replaced this request while bytes were still coming.
    if (generation !== previewGeneration || props.operation.id !== operationId) return
    try {
      const response = await artifactsApi.download(artifact.id)
      // Late bytes for a conversation that moved on are never attached.
      if (generation !== previewGeneration || props.operation.id !== operationId) return
      const urls = new Map(previewUrls.value)
      urls.set(artifact.id, URL.createObjectURL(response.data))
      previewUrls.value = urls
    } catch {
      // No preview for a retained file that could not be fetched; the
      // download button still offers it, and nothing is executed again.
    }
  }
}

const busy = ref(false)
const accepted = ref(false)
const failure = ref('')
const request = ref<ResumeChatPythonRequest | null>(null)

async function resume(mode: ResumeChatPythonRequest['mode']): Promise<void> {
  if (!props.canResume || busy.value || accepted.value || (request.value && request.value.mode !== mode)) return
  request.value ??= { mode, client_request_id: crypto.randomUUID() }
  const runId = props.operation.run_id
  const command = request.value
  busy.value = true
  failure.value = ''
  try {
    const response = await runsApi.resumeChatPython(runId, command)
    if (props.operation.run_id !== runId || request.value !== command) return
    accepted.value = true
    emit('resumed', response.data)
  } catch (error) {
    if (props.operation.run_id !== runId || request.value !== command) return
    const response = (error as { response?: { status?: number; data?: { error?: { code?: string } } } }).response
    const refusals: Record<string, string> = {
      chat_python_inputs_unavailable: 'A captured input is missing or pruned. Start a new chat turn with explicitly selected files; the earlier step will not be recomputed.',
      chat_python_execution_interrupted: 'A sandbox command may still be running. Wait for it to finish before continuing; it will not be sent again automatically.',
      chat_python_state_unavailable: 'This run has no recorded python step to continue. Start a new chat turn with explicitly selected files.',
      chat_python_record_unavailable: 'A recorded step is incomplete. Start a new chat turn with explicitly selected files.',
      run_already_active: 'Another run is active in this conversation. Wait for it to finish before continuing.',
      client_request_id_conflict: 'This continuation was already submitted as a different choice. Reload the conversation before trying again.',
    }
    const message = refusals[response?.data?.error?.code ?? '']
    if (response?.status === 409 && message) {
      failure.value = message
      request.value = null
      busy.value = false
    } else {
      failure.value = 'Continuation was not acknowledged. Retry the same choice; it will be sent identically.'
    }
  } finally {
    if (props.operation.run_id === runId && request.value === command) busy.value = false
  }
}

watch(
  () => [
    props.operation.id,
    props.operation.conversation_id,
    props.operation.status,
    previewArtifacts.value.map((artifact) => `${artifact.id}:${artifact.media_type}`),
  ],
  () => {
    revokePreviewUrls()
    void loadPreviews()
  },
)
watch(() => props.operation.run_id, () => {
  request.value = null
  busy.value = false
  accepted.value = false
  failure.value = ''
})

onMounted(() => void loadPreviews())
onBeforeUnmount(revokePreviewUrls)
</script>

<template>
  <section
    data-testid="python-operation"
    :data-operation-id="operation.id"
    class="overflow-hidden rounded-lg border border-border bg-card text-card-foreground"
  >
    <div class="flex min-w-0 items-center gap-2 px-3 py-2">
      <span class="flex min-w-0 flex-1 items-center gap-2 text-sm">
        <span class="shrink-0 font-medium">Python execution</span>
        <span aria-hidden="true" class="shrink-0 text-muted-foreground">·</span>
        <span class="min-w-0 flex-1 truncate">{{ stepSummary }}</span>
      </span>
      <Badge :variant="statusVariant" data-testid="python-operation-status" class="h-5 shrink-0 text-[10px]">
        {{ statusLabel }}
      </Badge>
      <Button
        v-if="canCancel"
        type="button"
        size="sm"
        variant="outline"
        class="h-7 shrink-0 px-2 text-xs"
        :aria-label="`Cancel python execution`"
        @click.stop="emit('cancel', operation.run_id)"
      >
        Cancel
      </Button>
    </div>

    <pre
      v-if="stdoutText"
      data-testid="python-stdout"
      class="mx-3 whitespace-pre-wrap break-words font-mono text-xs leading-5 text-muted-foreground"
    >
      {{ stdoutText }}
    </pre>

    <div v-if="previewArtifacts.length" class="flex flex-wrap gap-2 px-3 py-2">
      <template v-for="artifact in previewArtifacts" :key="artifact.id">
        <img
          v-if="previewUrls.get(artifact.id)"
          :src="previewUrls.get(artifact.id)"
          :alt="`${artifact.name} preview`"
          data-testid="chart-preview"
          class="max-w-64 h-auto rounded-md border border-border"
        />
        <p v-else data-testid="preview-unavailable" class="text-xs text-muted-foreground">
          {{ artifact.name }} preview is unavailable.
        </p>
      </template>
    </div>

    <div v-if="outputArtifacts.length" class="mt-3 flex flex-wrap items-center gap-2 px-3 pb-2">
      <Button
        v-for="artifact in outputArtifacts"
        :key="artifact.id"
        type="button"
        size="sm"
        variant="outline"
        class="h-7 shrink-0 px-2 text-xs"
        :aria-label="`Download ${artifact.name}`"
        @click="emit('download', artifact.id, artifact.name)"
      >
        Download {{ artifact.name }}
      </Button>
    </div>

    <div v-if="canResume" class="mt-3 flex flex-wrap items-center gap-2 px-3 pb-2">
      <button
        type="button"
        data-testid="resume-last-step"
        class="rounded border border-input px-2 py-1 text-xs disabled:opacity-50"
        :disabled="busy || accepted || (!!request && request.mode !== 'last_step')"
        @click="resume('last_step')"
      >
        A: Continue from last step
      </button>
      <button
        type="button"
        data-testid="resume-beginning"
        class="rounded border border-input px-2 py-1 text-xs disabled:opacity-50"
        :disabled="busy || accepted || (!!request && request.mode !== 'from_beginning')"
        @click="resume('from_beginning')"
      >
        B: Restart from selected files
      </button>
    </div>

    <p v-if="operation.error" class="px-3 pb-2 text-xs text-destructive" role="alert">
      {{ operation.error.message }}
    </p>
    <p v-if="failure" class="px-3 pb-2 text-xs text-destructive" role="alert">
      {{ failure }}
    </p>
  </section>
</template>
