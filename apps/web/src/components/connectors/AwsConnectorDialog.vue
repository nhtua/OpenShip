<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui'
import { Button, Label } from '@/components/ui'
import { connectorsApi } from '@/services/api'
import { generateRequestId } from '@/lib/requestId'
import { pendingConnectorRequests } from '@/lib/pendingConnectorRequests'
import { readConnectorError } from '@/lib/connectorErrors'
import {
  connectorStatusChipClass,
  connectorStatusDotClass,
  connectorStatusPresentation,
} from '@/lib/connectorCatalog'
import type { ConnectorUsage } from '@/types'

const props = defineProps<{
  open: boolean
  projectId: string | null
  connector: ConnectorUsage | null
  credentialStoreReady: boolean
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  saved: []
  check: [connectorId: string]
  error: [message: string]
}>()

type AwsConfiguration = {
  region: string
  expected_account_id: string
  basic_role_arn: string
}

const name = ref('AWS account')
const configuration = ref<AwsConfiguration>({
  region: 'us-east-1',
  expected_account_id: '',
  basic_role_arn: '',
})
const accessKeyId = ref('')
const secretAccessKey = ref('')
const sessionToken = ref('')
const sourceExpiresAt = ref('')
const loadingDetails = ref(false)
const saving = ref(false)
const revoking = ref(false)
const checkingRequest = ref(false)
const checkError = ref<string | null>(null)
const errorMessage = ref<string | null>(null)
const requestId = ref<string | null>(null)
const requestState = ref<'idle' | 'processing' | 'uncertain' | 'not-accepted'>('idle')
let detailGeneration = 0

const existing = computed(() => props.connector !== null)
const title = computed(() => existing.value ? props.connector?.name ?? 'AWS Connector' : 'Add AWS Connector')
const modelOpen = computed({
  get: () => props.open,
  set: (open: boolean) => emit('update:open', open),
})
const statusPresentation = computed(() => connectorStatusPresentation(props.connector ?? undefined))
const pendingKey = computed(() => `project:${props.projectId ?? 'missing'}:aws:${props.connector?.id ?? 'create'}`)
const waiting = computed(() => ['processing', 'uncertain'].includes(requestState.value))
const sourceExpiryValid = computed(() => {
  if (!sessionToken.value.trim()) return true
  const expiry = Date.parse(sourceExpiresAt.value)
  return Number.isFinite(expiry) && expiry > Date.now()
})
const canSave = computed(() => Boolean(
  props.open && props.projectId && props.credentialStoreReady && !loadingDetails.value &&
  !saving.value && !waiting.value && accessKeyId.value.trim() && secretAccessKey.value &&
  (existing.value || (
    name.value.trim() && configuration.value.region.trim() &&
    /^\d{12}$/.test(configuration.value.expected_account_id) &&
    /^arn:aws:iam::\d{12}:role\//.test(configuration.value.basic_role_arn)
  )) && sourceExpiryValid.value,
))

function clearSecretFields(): void {
  accessKeyId.value = ''
  secretAccessKey.value = ''
  sessionToken.value = ''
  sourceExpiresAt.value = ''
}

function settle(): void {
  if (requestId.value) pendingConnectorRequests.delete(pendingKey.value)
  requestId.value = null
  requestState.value = 'idle'
  errorMessage.value = null
  emit('saved')
  emit('update:open', false)
}

async function reconcileRequest(): Promise<void> {
  if (!props.projectId || !requestId.value || checkingRequest.value) return
  checkingRequest.value = true
  errorMessage.value = null
  try {
    const response = await connectorsApi.projectRequestStatus(props.projectId, requestId.value)
    if (response.data.status === 'complete') {
      settle()
    } else {
      requestState.value = 'processing'
    }
  } catch (error: unknown) {
    const safe = readConnectorError(error)
    if (safe.code === 'connector_not_found' || safe.code === 'connector_request_expired' || safe.status === 404 || safe.status === 410) {
      pendingConnectorRequests.delete(pendingKey.value)
      requestId.value = null
      requestState.value = 'not-accepted'
      errorMessage.value = safe.message
    } else {
      requestState.value = 'uncertain'
      errorMessage.value = safe.message
    }
  } finally {
    checkingRequest.value = false
  }
}

async function loadDetails(): Promise<void> {
  const generation = ++detailGeneration
  if (!props.open || !props.projectId || !props.connector) return
  loadingDetails.value = true
  errorMessage.value = null
  try {
    const response = await connectorsApi.getProjectConnector(props.projectId, props.connector.id)
    if (generation !== detailGeneration) return
    const value = response.data as { configuration?: Partial<AwsConfiguration>; name?: string }
    configuration.value = {
      region: value.configuration?.region ?? '',
      expected_account_id: value.configuration?.expected_account_id ?? '',
      basic_role_arn: value.configuration?.basic_role_arn ?? '',
    }
    name.value = value.name ?? props.connector.name
  } catch {
    if (generation === detailGeneration) errorMessage.value = 'AWS connector details could not be loaded. Reload settings and try again.'
  } finally {
    if (generation === detailGeneration) loadingDetails.value = false
  }
}

function resetForOpen(): void {
  errorMessage.value = null
  checkError.value = null
  if (!props.connector) {
    name.value = 'AWS account'
    configuration.value = { region: 'us-east-1', expected_account_id: '', basic_role_arn: '' }
  }
  clearSecretFields()
  const pending = pendingConnectorRequests.get(pendingKey.value)
  if (pending) {
    requestId.value = pending
    requestState.value = 'uncertain'
    void reconcileRequest()
  } else {
    requestId.value = null
    requestState.value = 'idle'
  }
}

watch(() => [props.open, props.projectId, props.connector?.id] as const, ([open], previous) => {
  const identityChanged = previous !== undefined && (
    props.projectId !== previous[1] || props.connector?.id !== previous[2]
  )
  const changedWhileOpen = identityChanged && previous?.[0] === true
  if (changedWhileOpen) emit('update:open', false)
  if (open && !changedWhileOpen) {
    resetForOpen()
    void loadDetails()
  }
  if (!open || changedWhileOpen) detailGeneration += 1
}, { immediate: true })

async function save(): Promise<void> {
  if (!canSave.value || !props.projectId) return
  const auth = {
    access_key_id: accessKeyId.value.trim(),
    secret_access_key: secretAccessKey.value,
    ...(sessionToken.value.trim()
      ? { session_token: sessionToken.value, expires_at: new Date(sourceExpiresAt.value).toISOString() }
      : {}),
  }
  const submittedRequestId = generateRequestId()
  const payload = existing.value
    ? { expected_revision: props.connector!.revision, auth }
    : {
        name: name.value.trim(),
        configuration: {
          schema_version: 1 as const,
          kind: 'aws' as const,
          region: configuration.value.region.trim(),
          expected_account_id: configuration.value.expected_account_id.trim(),
          basic_role_arn: configuration.value.basic_role_arn.trim(),
        },
        auth,
      }
  clearSecretFields()
  requestId.value = submittedRequestId
  pendingConnectorRequests.set(pendingKey.value, submittedRequestId)
  requestState.value = 'uncertain'
  saving.value = true
  errorMessage.value = null
  try {
    if (existing.value) {
      await connectorsApi.replaceProjectAwsCredential(
          props.projectId,
          props.connector!.id,
          submittedRequestId,
          payload as { expected_revision: number; auth: typeof auth },
      )
    } else {
      await connectorsApi.createProjectAwsConnector(
          props.projectId,
          submittedRequestId,
          payload as {
            name: string
            configuration: { schema_version: 1; kind: 'aws'; region: string; expected_account_id: string; basic_role_arn: string }
            auth: typeof auth
          },
      )
    }
    settle()
  } catch (error: unknown) {
    const safe = readConnectorError(error)
    if (safe.outcome === 'rejected') {
      pendingConnectorRequests.delete(pendingKey.value)
      requestId.value = null
      requestState.value = 'not-accepted'
      errorMessage.value = safe.message
    } else {
      requestState.value = 'uncertain'
      errorMessage.value = safe.message
      await reconcileRequest()
    }
  } finally {
    saving.value = false
  }
}

function checkConnection(): void {
  if (!props.connector || !props.projectId) return
  emit('check', props.connector.id)
  emit('update:open', false)
}

async function revoke(): Promise<void> {
  const connector = props.connector
  if (!connector || !props.projectId || revoking.value) return
  if (!window.confirm(`Revoke ${connector.name}? This removes the setup and credential from this project.`)) return
  revoking.value = true
  try {
    const result = await connectorsApi.updateProjectConnector(
      props.projectId,
      connector.id,
      generateRequestId(),
      { expected_revision: connector.revision, revoke: true },
    )
    if (!(result.data as { deleted?: boolean }).deleted) throw new Error('Revoke was not confirmed')
    emit('saved')
    emit('update:open', false)
  } catch {
    emit('error', 'The AWS Connector could not be removed. Reload connector settings and try again.')
  } finally {
    revoking.value = false
  }
}
</script>

<template>
  <DialogRoot v-model:open="modelOpen">
    <DialogPortal>
      <template v-if="open">
        <DialogOverlay class="fixed inset-0 z-50 bg-black/50" />
        <DialogContent class="fixed left-1/2 top-1/2 z-50 grid max-h-[88vh] w-[calc(100%-2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 gap-5 overflow-y-auto rounded-xl border border-border bg-background p-5 shadow-xl focus:outline-none md:p-6">
          <header class="grid gap-2">
            <div class="flex items-start justify-between gap-4">
              <div class="grid gap-1">
                <DialogTitle class="text-lg font-semibold">{{ title }}</DialogTitle>
                <DialogDescription class="text-sm text-muted-foreground">
                  Project-only AWS access uses a bootstrap key to assume the configured read role. OpenShip stores the source key encrypted and uses short-lived role sessions for checks and observations.
                </DialogDescription>
              </div>
              <DialogClose as-child><Button variant="ghost" size="sm" aria-label="Close AWS connector details">Close</Button></DialogClose>
            </div>
            <div v-if="existing && statusPresentation" class="flex flex-wrap items-center gap-2">
              <span class="rounded-full border border-border px-2 py-1 text-xs">Project visibility</span>
              <span :class="['inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-xs', connectorStatusChipClass(statusPresentation.status)]" data-testid="aws-connector-status">
                <span :class="['size-1.5 rounded-full', connectorStatusDotClass(statusPresentation.status)]" aria-hidden="true" />
                {{ statusPresentation.label }}
              </span>
            </div>
          </header>

          <p v-if="!credentialStoreReady" role="alert" class="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
            Encrypted connector storage is unavailable. Check the installation configuration before saving a key.
          </p>
          <p v-if="loadingDetails" role="status" class="text-sm text-muted-foreground">Loading AWS connector details…</p>
          <p v-if="errorMessage" role="alert" class="text-sm text-destructive">{{ errorMessage }}</p>

          <form class="grid gap-4" data-testid="aws-connector-form" @submit.prevent="save">
            <fieldset :disabled="loadingDetails || waiting || saving" class="grid gap-4">
              <div v-if="!existing" class="grid gap-4 sm:grid-cols-2">
                <div class="grid gap-1.5">
                  <Label for="aws-connector-name">Connector name</Label>
                  <input id="aws-connector-name" v-model="name" required maxlength="255" class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                </div>
                <div class="grid gap-1.5">
                  <Label for="aws-region">AWS region</Label>
                  <input id="aws-region" v-model="configuration.region" required maxlength="64" autocomplete="off" class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                </div>
                <div class="grid gap-1.5">
                  <Label for="aws-account">Expected AWS account ID</Label>
                  <input id="aws-account" v-model="configuration.expected_account_id" required inputmode="numeric" pattern="[0-9]{12}" maxlength="12" autocomplete="off" class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                </div>
                <div class="grid gap-1.5">
                  <Label for="aws-role-arn">Basic read role ARN</Label>
                  <input id="aws-role-arn" v-model="configuration.basic_role_arn" required maxlength="2048" autocomplete="off" placeholder="arn:aws:iam::123456789012:role/OpenShipRead" class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
                </div>
              </div>
              <dl v-else class="grid gap-2 rounded-md border border-border bg-muted/30 p-3 text-sm sm:grid-cols-2">
                <div><dt class="text-xs text-muted-foreground">Region</dt><dd>{{ configuration.region }}</dd></div>
                <div><dt class="text-xs text-muted-foreground">Expected account</dt><dd>{{ configuration.expected_account_id }}</dd></div>
                <div class="sm:col-span-2"><dt class="text-xs text-muted-foreground">Basic read role</dt><dd class="break-all">{{ configuration.basic_role_arn }}</dd></div>
              </dl>

              <div class="grid gap-1.5">
                <Label for="aws-access-key">{{ existing ? 'Replacement access key ID' : 'Bootstrap access key ID' }}</Label>
                <input id="aws-access-key" v-model="accessKeyId" required autocomplete="off" spellcheck="false" data-testid="aws-access-key" class="h-9 rounded-md border border-input bg-background px-3 font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60" />
              </div>
              <div class="grid gap-1.5">
                <Label for="aws-secret-key">{{ existing ? 'Replacement secret access key' : 'Bootstrap secret access key' }}</Label>
                <input id="aws-secret-key" v-model="secretAccessKey" required type="password" autocomplete="new-password" data-testid="aws-secret-key" class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60" />
              </div>
              <div class="grid gap-4 sm:grid-cols-2">
                <div class="grid gap-1.5">
                  <Label for="aws-session-token">Temporary source session token (optional)</Label>
                  <input id="aws-session-token" v-model="sessionToken" type="password" autocomplete="new-password" data-testid="aws-session-token" class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60" />
                </div>
                <div class="grid gap-1.5">
                  <Label for="aws-source-expiry">Temporary source expiry</Label>
                  <input id="aws-source-expiry" v-model="sourceExpiresAt" type="datetime-local" :required="Boolean(sessionToken)" data-testid="aws-source-expiry" class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60" />
                </div>
              </div>
              <p class="text-xs text-muted-foreground">
                The source key must be able to assume this role. The role trust policy must trust this installation and grant only the read actions listed in the setup guide. AWS access follows the permissions of this read role; you do not need to register buckets first.
              </p>
            </fieldset>

            <p v-if="requestState === 'processing' || requestState === 'uncertain'" role="status" class="text-sm text-muted-foreground">
              The result is still uncertain. Check request status before entering another key.
            </p>
            <p v-else-if="requestState === 'not-accepted'" role="status" class="text-sm text-muted-foreground">
              The request was not accepted or has expired. Enter the key again to submit a new request.
            </p>

            <div class="flex flex-wrap items-center gap-2 border-t border-border pt-4">
              <Button type="submit" size="sm" :disabled="!canSave" data-testid="save-aws-connector">
                {{ existing ? 'Save' : 'Set up AWS connector' }}
              </Button>
              <Button
                v-if="waiting"
                type="button"
                size="sm"
                variant="outline"
                data-testid="check-aws-request-status"
                :disabled="checkingRequest"
                @click="reconcileRequest"
              >
                Check request status
              </Button>
              <template v-if="existing">
                <Button type="button" size="sm" variant="outline" data-testid="check-aws-connection" :disabled="!credentialStoreReady || !connector || waiting" @click="checkConnection">
                  Check status
                </Button>
                <Button type="button" size="sm" variant="outline" class="border-destructive text-destructive hover:bg-accent" data-testid="revoke-aws-connector" :disabled="revoking || waiting" @click="revoke">
                  {{ revoking ? 'Revoking…' : 'Revoke' }}
                </Button>
              </template>
            </div>
          </form>
        </DialogContent>
      </template>
    </DialogPortal>
  </DialogRoot>
</template>
