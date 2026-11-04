<script setup lang="ts">
import { ref, watch } from 'vue'
import { artifactsApi, runsApi } from '@/services/api'
import type { AnalysisResumeSummary, ResumeAnalysisRequest, Run } from '@/types'

const props = withDefaults(defineProps<{
  runId: string
  analysis: AnalysisResumeSummary
  error?: string | null
  canResume?: boolean
}>(), { canResume: true })
const emit = defineEmits<{ resumed: [run: Run] }>()
const busy = ref(false)
const accepted = ref(false)
const failure = ref('')
const request = ref<ResumeAnalysisRequest | null>(null)
watch(() => props.runId, () => {
  request.value = null
  busy.value = false
  accepted.value = false
  failure.value = ''
})

async function resume(mode: ResumeAnalysisRequest['mode']) {
  if (!props.canResume || busy.value || accepted.value || (request.value && request.value.mode !== mode)) return
  request.value ??= { mode, client_request_id: crypto.randomUUID() }
  const runId = props.runId
  const command = request.value
  busy.value = true
  failure.value = ''
  try {
    const response = await runsApi.resumeAnalysis(runId, command)
    if (props.runId !== runId || request.value !== command) return
    accepted.value = true
    emit('resumed', response.data)
  } catch (error) {
    if (props.runId !== runId || request.value !== command) return
    const response = (error as { response?: { status?: number; data?: { error?: { code?: string } } } }).response
    const refusals: Record<string, string> = {
      analysis_inputs_unavailable: 'A saved analysis file is missing or pruned. Select current files explicitly; earlier steps will not be recomputed.',
      analysis_execution_interrupted: 'An earlier command was interrupted or may still be running. Wait for definite provider availability before starting from beginning. It will not be sent again automatically.',
      analysis_state_unavailable: 'Saved analysis state is unavailable. Submit a new analysis with explicitly selected files.',
      run_already_active: 'Another run is active in this conversation. Wait for it to finish before resuming.',
    }
    const message = refusals[response?.data?.error?.code ?? '']
    if (response?.status === 409 && message) {
      failure.value = message
      request.value = null
      busy.value = false
    } else {
      failure.value = 'Resume was not acknowledged. Please retry the same choice; missing inputs or an interrupted command require attention and will not be recomputed.'
    }
  } finally {
    if (props.runId === runId && request.value === command) busy.value = false
  }
}

async function download(id: string) {
  try {
    const response = await artifactsApi.download(id)
    const url = URL.createObjectURL(response.data)
    const link = document.createElement('a')
    link.href = url
    link.download = `analysis-${id}`
    link.click()
    URL.revokeObjectURL(url)
  } catch {
    failure.value = 'The retained result is unavailable. Execution will not be repeated.'
  }
}
</script>

<template>
  <section class="grid gap-2 border-t border-border px-3 py-2" aria-label="Resume analysis">
    <div v-if="canResume" class="flex flex-wrap gap-2">
      <button
        type="button" data-testid="resume-a"
        class="rounded border px-2 py-1 disabled:opacity-50"
        :disabled="busy || accepted || (!!request && request.mode !== 'last_step')"
        @click="resume('last_step')"
      >A: Resume from last step</button>
      <button
        type="button" data-testid="resume-b"
        class="rounded border px-2 py-1 disabled:opacity-50"
        :disabled="busy || accepted || (!!request && request.mode !== 'from_beginning')"
        @click="resume('from_beginning')"
      >B: Start from beginning</button>
    </div>
    <a v-for="id in analysis.output_ids" :key="id" :href="`#artifact-${id}`" data-testid="retained-result" class="underline" @click.prevent="download(id)">Retained result {{ id }}</a>
    <a v-if="error || analysis.retained_error_code" :href="`#analysis-error-${runId}`" data-testid="retained-error" class="underline">Retained error details</a>
    <p v-if="error || analysis.retained_error_code" :id="`analysis-error-${runId}`">{{ error || analysis.retained_error_code }}</p>
    <p v-if="failure" role="alert">{{ failure }}</p>
  </section>
</template>
