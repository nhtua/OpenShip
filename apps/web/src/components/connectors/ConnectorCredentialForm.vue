<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { generateRequestId } from '@/lib/requestId'
import { pendingConnectorRequests } from '@/lib/pendingConnectorRequests'
import { Button, Label } from '@/components/ui'
import type { ConnectorRequestStatus } from '@/types'

const props = withDefaults(defineProps<{
  title: string
  submitLabel: string
  requestScopeKey?: string
  canSubmit?: boolean
  submit: (apiKey: string, requestId: string) => Promise<Record<string, unknown>>
  readStatus: (requestId: string) => Promise<ConnectorRequestStatus>
}>(), {
  canSubmit: true,
})

const emit = defineEmits<{
  complete: [result: Record<string, unknown>]
}>()

const apiKey = ref('')
const requestId = ref<string | null>(null)
const state = ref<'idle' | 'checking' | 'processing' | 'uncertain' | 'not-accepted'>('idle')
const statusMessage = computed(() => {
  if (state.value === 'processing' || state.value === 'checking') {
    return 'The request is still processing. Check its status before entering another key.'
  }
  if (state.value === 'uncertain') {
    return 'The result is still uncertain. Check request status before entering another key.'
  }
  if (state.value === 'not-accepted') {
    return 'The request was not accepted or has expired. Enter the key again to submit a new request.'
  }
  return null
})
const blocked = computed(() => ['checking', 'processing', 'uncertain'].includes(state.value))
const scopeKey = computed(() => props.requestScopeKey ?? `connector-form:${props.title}`)

function isMissingOrExpired(err: unknown): boolean {
  const error = err as {
    response?: { status?: number; data?: { error?: { code?: string } } }
  }
  const code = error.response?.data?.error?.code
  return code === 'connector_not_found' || code === 'connector_request_expired' ||
    error.response?.status === 404 || error.response?.status === 410
}

async function reconcileRequest(): Promise<void> {
  if (!requestId.value) return
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
    }
  }
}

async function handleCredentialSubmit(): Promise<void> {
  if (!apiKey.value || blocked.value) return
  if (props.canSubmit === false) return
  const submittedKey = apiKey.value
  const submittedRequestId = generateRequestId()
  apiKey.value = ''
  await nextTick()
  requestId.value = submittedRequestId
  pendingConnectorRequests.set(scopeKey.value, submittedRequestId)
  state.value = 'checking'
  try {
    const result = await props.submit(submittedKey, submittedRequestId)
    pendingConnectorRequests.delete(scopeKey.value)
    requestId.value = null
    state.value = 'idle'
    emit('complete', result)
  } catch {
    // A lost response may follow a committed write. The component has already
    // cleared the key and now reconciles status; it never resends the payload.
    await reconcileRequest()
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
        :disabled="blocked"
        data-testid="connector-api-key"
        class="flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
      />
    </div>
    <p v-if="statusMessage" role="status" class="text-sm text-muted-foreground">
      {{ statusMessage }}
    </p>
    <div class="flex flex-wrap gap-2">
      <Button type="submit" size="sm" :disabled="!apiKey || blocked || props.canSubmit === false">
        {{ props.submitLabel }}
      </Button>
      <Button
        v-if="blocked"
        type="button"
        size="sm"
        variant="outline"
        data-testid="check-connector-request"
        @click="reconcileRequest"
      >
        Check request status
      </Button>
    </div>
  </form>
</template>
