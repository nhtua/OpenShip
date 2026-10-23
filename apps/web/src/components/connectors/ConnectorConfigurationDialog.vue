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
import { Button, Input, Label } from '@/components/ui'
import ConnectorCredentialForm from '@/components/connectors/ConnectorCredentialForm.vue'
import { generateRequestId } from '@/lib/requestId'
import { connectorsApi } from '@/services/api'
import type { ConnectorKind, ConnectorUsage } from '@/types'
import {
  connectorStatusChipClass,
  connectorStatusDotClass,
  connectorStatusPresentation,
  type ConnectorCatalogEntry,
} from '@/lib/connectorCatalog'

const props = defineProps<{
  open: boolean
  entry: ConnectorCatalogEntry
  scope: 'global' | 'project'
  connector: ConnectorUsage | null
  projectId: string | null
  selectedProjectId: string | null
  canManageGlobal: boolean
  credentialStoreReady: boolean
  returnFocusElement?: HTMLElement | null
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  complete: []
  error: [message: string]
}>()

const connectorName = ref('')
const currentConnector = ref<ConnectorUsage | null>(props.connector)
const busy = ref(false)
const credentialBusy = ref(false)
const checkingStatus = ref(false)
const addingAnother = ref(false)
const checkResult = ref<{ status: 'valid' | 'invalid' | 'unavailable'; message: string } | null>(null)
const confirmedStatus = ref<ConnectorUsage['status'] | null>(null)
const modelOpen = computed({
  get: () => props.open,
  set: (open: boolean) => emit('update:open', open),
})
const canManage = computed(() => props.scope === 'project' || props.canManageGlobal)
const isCreate = computed(() => props.scope === 'global' && currentConnector.value === null)
const statusPresentation = computed(() => {
  const status = confirmedStatus.value ?? (addingAnother.value ? null : currentConnector.value?.status)
  return status ? connectorStatusPresentation({ status }) : null
})
const title = computed(() => {
  if (addingAnother.value) return `Add another ${props.entry.name}`
  if (props.scope === 'project') return `Legacy Project connector · ${currentConnector.value?.name ?? props.entry.name}`
  return currentConnector.value ? currentConnector.value.name : `Configure ${props.entry.name}`
})
const credentialScopeKey = computed(() => props.scope === 'global'
  ? currentConnector.value && !addingAnother.value ? `global:credential:${currentConnector.value.id}` : `global:create:${props.entry.kind}`
  : `project:${props.projectId ?? 'missing-project'}:credential:${currentConnector.value?.id ?? 'missing-connector'}`,
)

watch(() => [props.entry.kind, props.scope, props.projectId, props.connector] as const, ([, , , connector]) => {
  currentConnector.value = connector
}, { immediate: true })

watch(() => props.selectedProjectId, (next, previous) => {
  if (previous !== undefined && next !== previous && props.open) emit('update:open', false)
})

watch(() => [props.open, props.scope, props.projectId, props.connector?.id, props.entry.kind], (next, previous) => {
  const wasOpen = previous[0]
  const identityChanged = next.slice(1).some((value, index) => value !== previous[index + 1])
  if (!next[0] || identityChanged) {
    confirmedStatus.value = null
    checkResult.value = null
    addingAnother.value = false
  }
  if (next[0] && wasOpen && identityChanged) {
    emit('update:open', false)
  }
})

watch(() => [props.open, props.entry.kind, props.connector?.id], (next) => {
  if (next[0]) connectorName.value = currentConnector.value?.name ?? props.entry.name
  else connectorName.value = ''
}, { immediate: true })

function statusReader() {
  const projectId = props.projectId
  const scope = props.scope
  return async (requestId: string) => {
    const response = scope === 'project' && projectId
      ? await connectorsApi.projectRequestStatus(projectId, requestId)
      : await connectorsApi.globalRequestStatus(requestId)
    return response.data
  }
}

function readConnectorStatus(status: unknown): ConnectorUsage['status'] | null {
  return status === 'configured' || status === 'disabled' || status === 'expired' ||
    status === 'revoked' || status === 'unavailable'
    ? status
    : null
}

function submitCredential() {
  const scope = props.scope
  const projectId = props.projectId
  const connector = currentConnector.value
  const addAnother = addingAnother.value
  const kind: ConnectorKind = props.entry.kind
  const name = connectorName.value.trim()

  return async (apiKey: string, requestId: string): Promise<Record<string, unknown>> => {
    if (scope === 'project') {
      if (!projectId || !connector) throw new Error('The legacy connector scope is unavailable')
      const response = await connectorsApi.replaceProjectCredential(
        projectId,
        connector.id,
        requestId,
        { expected_revision: connector.revision, api_key: apiKey },
      )
      return response.data as Record<string, unknown>
    }

    if (connector && !addAnother) {
      const response = await connectorsApi.replaceGlobalCredential(
        connector.id,
        requestId,
        { expected_revision: connector.revision, api_key: apiKey },
      )
      return response.data as Record<string, unknown>
    }

    if (!name) throw new Error('Enter a connector name')
    const response = await connectorsApi.createGlobalConnector(requestId, {
      kind,
      name,
      configuration: {},
      api_key: apiKey,
    })
    return response.data as Record<string, unknown>
  }
}

function startAddingAnother(): void {
  addingAnother.value = true
  connectorName.value = ''
  confirmedStatus.value = null
  checkResult.value = null
}

function returnToConnector(): void {
  addingAnother.value = false
  connectorName.value = currentConnector.value?.name ?? props.entry.name
}

async function checkConnectorStatus(): Promise<void> {
  const connector = currentConnector.value
  if (!connector || checkingStatus.value || !canManage.value || !props.credentialStoreReady) return
  checkingStatus.value = true
  checkResult.value = null
  try {
    const response = props.scope === 'project' && props.projectId
      ? await connectorsApi.checkProjectConnector(props.projectId, connector.id)
      : await connectorsApi.checkGlobalConnector(connector.id)
    if (response.data.revision !== connector.revision) {
      checkResult.value = {
        status: 'unavailable',
        message: 'The connector changed while it was being checked. Reload settings and check again.',
      }
    } else {
      checkResult.value = { status: response.data.status, message: response.data.message }
    }
  } catch {
    checkResult.value = {
      status: 'unavailable',
      message: 'The connector key could not be checked. Reload settings and try again.',
    }
  } finally {
    checkingStatus.value = false
  }
}

async function revokeConnector(): Promise<void> {
  const connector = currentConnector.value
  if (!connector || busy.value || !canManage.value) return
  const impact = props.scope === 'global'
    ? 'This removes the shared setup for every project and clears project defaults that use it.'
    : 'This removes the setup and credential from this project.'
  if (!window.confirm(`Revoke ${connector.name}? ${impact} This cannot be undone.`)) return

  busy.value = true
  try {
    const requestId = generateRequestId()
    const response = await (props.scope === 'project' && props.projectId
      ? connectorsApi.updateProjectConnector(props.projectId, connector.id, requestId, {
        expected_revision: connector.revision,
        revoke: true,
      })
      : connectorsApi.updateGlobalConnector(connector.id, requestId, {
        expected_revision: connector.revision,
        revoke: true,
      }))
    if (!response.data?.deleted) throw new Error('Connector removal was not confirmed')
    emit('complete')
    emit('update:open', false)
  } catch {
    emit('error', 'The connector could not be removed. Reload connector settings and try again.')
  } finally {
    busy.value = false
  }
}

function restoreFocus(event: Event): void {
  event.preventDefault()
  queueMicrotask(() => {
    if (props.returnFocusElement?.isConnected) props.returnFocusElement.focus()
  })
}

function completeCredentialUpdate(result: Record<string, unknown>): void {
  const resultId = result.id
  const revision = result.revision
  if (typeof resultId === 'string' && typeof revision === 'number') {
    const status = readConnectorStatus(result.status)
    if (currentConnector.value?.id === resultId) {
      currentConnector.value = {
        ...currentConnector.value,
        revision,
        status: status ?? currentConnector.value.status,
      }
    } else if (!currentConnector.value && typeof result.kind === 'string' &&
      typeof result.visibility === 'string' && typeof result.name === 'string' && status) {
      currentConnector.value = result as unknown as ConnectorUsage
    }
  }
  confirmedStatus.value = readConnectorStatus(result.status) ?? 'configured'
  emit('complete')
  if (addingAnother.value) emit('update:open', false)
}
</script>

<template>
  <DialogRoot v-model:open="modelOpen">
    <DialogPortal>
      <template v-if="open">
        <DialogOverlay class="fixed inset-0 z-50 bg-black/50" />
        <DialogContent
          class="fixed left-1/2 top-1/2 z-50 grid max-h-[85vh] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 gap-5 overflow-y-auto rounded-xl border border-border bg-background p-5 shadow-xl focus:outline-none md:p-6"
          @close-auto-focus="restoreFocus"
        >
          <header class="grid gap-2">
            <div class="flex items-start justify-between gap-4">
              <div class="grid gap-1">
                <DialogTitle class="text-lg font-semibold">{{ title }}</DialogTitle>
                <DialogDescription class="text-sm text-muted-foreground">
                  <template v-if="scope === 'global'">
                    This search connector has fixed Global visibility and can be used by eligible projects.
                  </template>
                  <template v-else>
                    This existing Project connector remains private to its original project during cutover.
                  </template>
                </DialogDescription>
              </div>
              <DialogClose as-child>
                <Button variant="ghost" size="sm" aria-label="Close connector details">Close</Button>
              </DialogClose>
            </div>
            <div class="flex flex-wrap items-center gap-2 text-xs">
              <span class="rounded-full border border-border px-2 py-1">{{ scope === 'global' ? 'Global' : 'Project' }} visibility</span>
              <span class="rounded-full border border-border px-2 py-1">Published by {{ entry.publisher }}</span>
              <span
                v-if="statusPresentation"
                data-testid="connector-status"
                :class="['inline-flex items-center gap-1.5 rounded-full border px-2 py-1', connectorStatusChipClass(statusPresentation.status)]"
              >
                <span aria-hidden="true" :class="['size-2 rounded-full', connectorStatusDotClass(statusPresentation.status)]" />
                <span>{{ statusPresentation.label }}</span>
              </span>
            </div>
          </header>

          <section v-if="!canManage && connector" class="grid gap-3 rounded-lg border border-border p-4">
            <div class="grid gap-1">
              <h3 class="font-medium">Shared connector details</h3>
              <p class="text-sm text-muted-foreground">{{ connector.name }} is available to this project. Its API key is managed by the installation operator.</p>
            </div>
            <p class="text-xs text-muted-foreground">Selected project: {{ selectedProjectId ?? 'None selected' }}</p>
          </section>

          <section v-else-if="scope === 'global' && !canManage" class="grid gap-2 rounded-lg border border-border p-4">
            <h3 class="font-medium">Operator-managed setup</h3>
            <p class="text-sm text-muted-foreground">Ask the installation operator to configure a shared {{ entry.name }} connector.</p>
          </section>

          <section v-else-if="currentConnector && canManage && !addingAnother" class="grid gap-3 rounded-lg border border-border p-4">
            <div class="grid gap-1">
              <h3 class="font-medium">{{ currentConnector.name }}</h3>
              <p class="text-sm text-muted-foreground">
                {{ scope === 'global' ? 'Shared by eligible projects.' : 'Retained under the original project scope.' }}
                Revision {{ currentConnector.revision }}.
              </p>
            </div>
            <ConnectorCredentialForm
              :key="credentialScopeKey"
              :title="`${currentConnector.name} (${entry.name})`"
              :request-scope-key="credentialScopeKey"
              submit-label="Save"
              :can-submit="credentialStoreReady"
              :disabled="busy || checkingStatus"
              :submit="submitCredential()"
              :read-status="statusReader()"
              @complete="completeCredentialUpdate"
              @busy-change="credentialBusy = $event"
            >
              <template #actions="{ blocked: credentialBlocked, submitting: credentialSubmitting }">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  :disabled="busy || checkingStatus || credentialBusy || credentialBlocked || credentialSubmitting || !credentialStoreReady"
                  @click="checkConnectorStatus"
                >
                  {{ checkingStatus ? 'Checking…' : 'Check status' }}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  class="border-destructive bg-background text-destructive hover:bg-destructive/10 hover:text-destructive"
                  :disabled="busy || checkingStatus || credentialBusy || credentialBlocked || credentialSubmitting || !credentialStoreReady"
                  @click="revokeConnector"
                >
                  Revoke
                </Button>
                <Button
                  v-if="scope === 'global' && canManageGlobal"
                  type="button"
                  size="sm"
                  variant="outline"
                  :disabled="busy || checkingStatus || credentialBusy || credentialBlocked || credentialSubmitting || !credentialStoreReady"
                  @click="startAddingAnother"
                >
                  Add another {{ entry.name }}
                </Button>
              </template>
            </ConnectorCredentialForm>
            <p class="text-xs text-muted-foreground">
              Check status sends one minimal search request to the provider; it may count toward your provider quota.
            </p>
            <p
              v-if="checkResult"
              role="status"
              data-testid="connector-check-result"
              :class="[
                'flex items-center gap-2 text-sm',
                checkResult.status === 'valid' ? 'text-emerald-600' : 'text-destructive',
              ]"
            >
              <span
                aria-hidden="true"
                :class="['size-2 shrink-0 rounded-full', checkResult.status === 'valid' ? 'bg-emerald-500' : 'bg-destructive']"
              />
              {{ checkResult.message }}
            </p>
          </section>

          <section v-else-if="(isCreate || addingAnother) && canManage" class="grid gap-3 rounded-lg border border-border p-4">
            <div class="flex items-center justify-between gap-3">
              <h3 class="font-medium">{{ addingAnother ? `Add another ${entry.name}` : `Configure ${entry.name}` }}</h3>
              <Button
                v-if="addingAnother"
                type="button"
                size="sm"
                variant="ghost"
                @click="returnToConnector"
              >
                Back to connector
              </Button>
            </div>
            <div class="grid gap-1.5">
              <Label for="catalog-connector-name">Connector name</Label>
              <Input id="catalog-connector-name" v-model="connectorName" aria-label="Connector name" maxlength="255" :disabled="!credentialStoreReady" />
            </div>
            <ConnectorCredentialForm
              :key="credentialScopeKey"
              :title="entry.name"
              :request-scope-key="credentialScopeKey"
              :submit-label="addingAnother ? 'Add connector' : 'Add global connector'"
              :can-submit="Boolean(connectorName.trim()) && credentialStoreReady"
              :submit="submitCredential()"
              :read-status="statusReader()"
              @complete="completeCredentialUpdate"
            />
            <p v-if="!credentialStoreReady" role="status" class="text-sm text-muted-foreground">
              Connector credential storage is unavailable. Ask the operator to check installation setup.
            </p>
          </section>

          <section v-else-if="scope === 'project' && !currentConnector" role="status" class="text-sm text-muted-foreground">
            Legacy Project connector details are unavailable. New search connectors must use Global visibility.
          </section>
        </DialogContent>
      </template>
    </DialogPortal>
  </DialogRoot>
</template>
