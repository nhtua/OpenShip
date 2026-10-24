<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { generateRequestId } from '@/lib/requestId'
import { pendingConnectorRequests } from '@/lib/pendingConnectorRequests'
import { readConnectorError } from '@/lib/connectorErrors'
import { Button, Label } from '@/components/ui'
import type { ConnectorRequestStatus } from '@/types'

const props = withDefaults(defineProps<{
  title: string
  submitLabel: string
  requestScopeKey?: string
  canSubmit?: boolean
  disabled?: boolean
  submit: (apiKey: string, requestId: string) => Promise<Record<string, unknown>>
  readStatus: (requestId: string) => Promise<ConnectorRequestStatus>
}>(), {
  canSubmit: true,
  disabled: false,
})

const emit = defineEmits<{
  complete: [result: Record<string, unknown>]
  busyChange: [busy: boolean]
}>()

const apiKey = ref('')
const requestId = ref<string | null>(null)
const errorMessage = ref<string | null>(null)
const submitting = ref(false)
const state = ref<'idle' | 'checking' | 'processing' | 'uncertain' | 'not-accepted'>('idle')
const statusMessage = computed(() => {
  if (state.value === 'processing' || state.value === 'checking') {
    return 'The request is still processing. Check its status before entering another key.'
  }
  if (state.value === 'uncertain') {
    return errorMessage.value ?? 'The result is still uncertain. Check request status before entering another key.'
  }
  if (state.value === 'not-accepted') {
    return errorMessage.value ?? 'The request was not accepted or has expired. Enter the key again to submit a new request.'
  }
  return null
})
const blocked = computed(() => props.disabled || ['checking', 'processing', 'uncertain'].includes(state.value))
const scopeKey = computed(() => props.requestScopeKey ?? `connector-form:${props.title}`)

function setSubmitting(value: boolean): void {
  if (submitting.value === value) return
  submitting.value = value
  emit('busyChange', value)
}

function isMissingOrExpired(err: unknown): boolean {
  const error = readConnectorError(err)
  return error.code === 'connector_not_found' || error.code === 'connector_request_expired' ||
    error.status === 404 || error.status === 410
}

async function reconcileRequest(manageBusy = true): Promise<void> {
  if (!requestId.value) return
  if (manageBusy && submitting.value) return
  if (manageBusy) setSubmitting(true)
  errorMessage.value = null
  state.value = 'checking'
  try {
    const result = await props.readStatus(requestId.value)
    if (result.status === 'complete') {
      pendingConnectorRequests.delete(scopeKey.value)
      requestId.value = null
      state.value = 'idle'
      emit('complete', result.result ?? {})
    } else {
      state.value = 'processing'
    }
  } catch (err: unknown) {
    if (isMissingOrExpired(err)) {
      pendingConnectorRequests.delete(scopeKey.value)
      requestId.value = null
      state.value = 'not-accepted'
    } else {
      state.value = 'uncertain'
      const safeError = readConnectorError(err)
      if (safeError.code === 'credential_store_unavailable') errorMessage.value = safeError.message
    }
  } finally {
    if (manageBusy) setSubmitting(false)
  }
}

async function handleCredentialSubmit(): Promise<void> {
  if (!apiKey.value || blocked.value || submitting.value) return
  if (props.canSubmit === false) return
  const submittedKey = apiKey.value
  const submittedRequestId = generateRequestId()
  apiKey.value = ''
  errorMessage.value = null
  await nextTick()
  requestId.value = submittedRequestId
  pendingConnectorRequests.set(scopeKey.value, submittedRequestId)
  state.value = 'checking'
  setSubmitting(true)
  try {
    const result = await props.submit(submittedKey, submittedRequestId)
    pendingConnectorRequests.delete(scopeKey.value)
    requestId.value = null
    state.value = 'idle'
    emit('complete', result)
  } catch (err: unknown) {
    // A lost response may follow a committed write. The component has already
    // cleared the key and now reconciles status; it never resends the payload.
    const safeError = readConnectorError(err)
    if (safeError.outcome === 'rejected') {
      pendingConnectorRequests.delete(scopeKey.value)
      requestId.value = null
      state.value = 'not-accepted'
      errorMessage.value = safeError.message
      return
    }
    await reconcileRequest(false)
  } finally {
    setSubmitting(false)
  }
}

function updateApiKey(event: Event): void {
  apiKey.value = (event.target as HTMLInputElement).value
}

onMounted(() => {
  const pendingRequestId = pendingConnectorRequests.get(scopeKey.value)
  if (!pendingRequestId) return
  requestId.value = pendingRequestId
  void reconcileRequest()
})
</script>

<template>
  <form class="grid gap-3" data-testid="connector-credential-form" @submit.prevent="handleCredentialSubmit">
    <div class="grid gap-1.5">
      <Label :for="`connector-key-${props.title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`">{{ props.title }} API key</Label>
      <input
        :id="`connector-key-${props.title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`"
        :value="apiKey"
        @input="updateApiKey"
        type="password"
        autocomplete="new-password"
        :disabled="blocked || submitting"
        data-testid="connector-api-key"
        class="flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
      />
    </div>
    <p v-if="statusMessage" role="status" class="text-sm text-muted-foreground">
      {{ statusMessage }}
    </p>
    <div class="flex flex-wrap gap-2">
      <Button type="submit" size="sm" :disabled="!apiKey || blocked || submitting || props.canSubmit === false">
        {{ props.submitLabel }}
      </Button>
      <Button
        v-if="blocked"
        type="button"
        size="sm"
        variant="outline"
        data-testid="check-connector-request"
        :disabled="submitting"
        @click="reconcileRequest"
      >
        Check request status
      </Button>
      <slot name="actions" :blocked="blocked || submitting" :submitting="submitting" />
    </div>
  </form>
</template>
