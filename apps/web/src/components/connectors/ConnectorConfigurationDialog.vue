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
import type { ConnectorCatalogEntry } from '@/lib/connectorCatalog'

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
const busy = ref(false)
const modelOpen = computed({
  get: () => props.open,
  set: (open: boolean) => emit('update:open', open),
})
const canManage = computed(() => props.scope === 'project' || props.canManageGlobal)
const isCreate = computed(() => props.scope === 'global' && props.connector === null)
const title = computed(() => {
  if (props.scope === 'project') return `Legacy Project connector · ${props.connector?.name ?? props.entry.name}`
  return props.connector ? props.connector.name : `Configure ${props.entry.name}`
})
const credentialScopeKey = computed(() => props.scope === 'global'
  ? props.connector ? `global:credential:${props.connector.id}` : `global:create:${props.entry.kind}`
  : `project:${props.projectId ?? 'missing-project'}:credential:${props.connector?.id ?? 'missing-connector'}`,
)

watch(() => props.selectedProjectId, (next, previous) => {
  if (previous !== undefined && next !== previous && props.open) emit('update:open', false)
})

watch(() => [props.open, props.scope, props.projectId, props.connector?.id, props.entry.kind], (next, previous) => {
  const wasOpen = previous[0]
  const identityChanged = next.slice(1).some((value, index) => value !== previous[index + 1])
  if (next[0] && wasOpen && identityChanged) {
    emit('update:open', false)
  }
})

watch(() => [props.open, props.entry.kind, props.connector?.id], (next) => {
  if (next[0]) connectorName.value = props.connector?.name ?? props.entry.name
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

function submitCredential() {
  const scope = props.scope
  const projectId = props.projectId
  const connector = props.connector
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

    if (connector) {
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

async function updateConnector(change: 'toggle' | 'revoke'): Promise<void> {
  const connector = props.connector
  if (!connector || busy.value || !canManage.value) return
  if (change === 'revoke') {
    const impact = props.scope === 'global'
      ? 'This affects every project using this global connector.'
      : 'This affects searches in this project.'
    if (!window.confirm(`Revoke ${connector.name}? ${impact}`)) return
  }

  busy.value = true
  try {
    const requestId = generateRequestId()
    const payload = {
      expected_revision: connector.revision,
      ...(change === 'revoke'
        ? { revoke: true }
        : { enabled: connector.status === 'disabled' }),
    }
    if (props.scope === 'project' && props.projectId) {
      await connectorsApi.updateProjectConnector(props.projectId, connector.id, requestId, payload)
    } else {
      await connectorsApi.updateGlobalConnector(connector.id, requestId, payload)
    }
    emit('complete')
  } catch {
    emit('error', 'The connector could not be updated. Reload connector settings and try again.')
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
              <span v-if="connector" class="rounded-full bg-muted px-2 py-1">{{ connector.status }}</span>
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

          <section v-else-if="connector && canManage" class="grid gap-3 rounded-lg border border-border p-4">
            <div class="grid gap-1">
              <h3 class="font-medium">{{ connector.name }}</h3>
              <p class="text-sm text-muted-foreground">
                {{ scope === 'global' ? 'Shared by eligible projects.' : 'Retained under the original project scope.' }}
                Revision {{ connector.revision }}.
              </p>
            </div>
            <ConnectorCredentialForm
              :key="credentialScopeKey"
              :title="`${connector.name} (${entry.name})`"
              :request-scope-key="credentialScopeKey"
              :submit-label="scope === 'global' ? 'Rotate API key' : 'Replace API key'"
              :can-submit="credentialStoreReady"
              :submit="submitCredential()"
              :read-status="statusReader()"
              @complete="emit('complete')"
            />
            <div class="flex flex-wrap gap-2">
              <Button
                v-if="connector.status !== 'revoked'"
                type="button"
                size="sm"
                variant="outline"
                :disabled="busy || !credentialStoreReady"
                @click="updateConnector('toggle')"
              >
                {{ connector.status === 'disabled' ? 'Enable' : 'Disable' }}
              </Button>
              <Button
                v-if="connector.status !== 'revoked'"
                type="button"
                size="sm"
                variant="destructive"
                :disabled="busy || !credentialStoreReady"
                @click="updateConnector('revoke')"
              >
                {{ scope === 'global' ? 'Revoke globally' : 'Revoke legacy connector' }}
              </Button>
            </div>
          </section>

          <section v-else-if="isCreate && canManage" class="grid gap-3 rounded-lg border border-border p-4">
            <div class="grid gap-1.5">
              <Label for="catalog-connector-name">Connector name</Label>
              <Input id="catalog-connector-name" v-model="connectorName" aria-label="Connector name" maxlength="255" :disabled="!credentialStoreReady" />
            </div>
            <ConnectorCredentialForm
              :key="credentialScopeKey"
              :title="entry.name"
              :request-scope-key="credentialScopeKey"
              submit-label="Add global connector"
              :can-submit="Boolean(connectorName.trim()) && credentialStoreReady"
              :submit="submitCredential()"
              :read-status="statusReader()"
              @complete="emit('complete')"
            />
            <p v-if="!credentialStoreReady" role="status" class="text-sm text-muted-foreground">
              Connector credential storage is unavailable. Ask the operator to check installation setup.
            </p>
          </section>

          <section v-else-if="scope === 'project' && !connector" role="status" class="text-sm text-muted-foreground">
            Legacy Project connector details are unavailable. New search connectors must use Global visibility.
          </section>
        </DialogContent>
      </template>
    </DialogPortal>
  </DialogRoot>
</template>
