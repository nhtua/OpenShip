<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '@/layouts/AppLayout.vue'
import ConnectorCredentialForm from '@/components/connectors/ConnectorCredentialForm.vue'
import { Button, Input, Label } from '@/components/ui'
import { generateRequestId } from '@/lib/requestId'
import { connectorsApi } from '@/services/api'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useConnectorsStore } from '@/stores/connectors'
import { useProjectsStore } from '@/stores/projects'
import type { ConnectorKind, ConnectorUsage } from '@/types'

const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const connectors = useConnectorsStore()
const projects = useProjectsStore()
const kinds: ConnectorKind[] = ['brave', 'tavily']
const displayName = (kind: ConnectorKind) => kind === 'brave' ? 'Brave Search' : 'Tavily Search'
const createNames = reactive<Record<ConnectorKind, string>>({
  brave: 'Brave Search',
  tavily: 'Tavily Search',
})
const busyConnectors = ref(new Set<string>())
const pageError = ref<string | null>(null)
const projectCreationError = ref<string | null>(null)
const projectCreating = ref(false)
const setupUnavailableMessage = computed(() => {
  const reason = connectors.setupUnavailableReason
  if (reason === 'installation_id_missing') {
    return 'Managed connector changes are unavailable because the installation identity is not configured.'
  }
  if (reason === 'encryption_key_unavailable') {
    return 'Managed connector changes are unavailable because the encryption key file is missing or invalid.'
  }
  if (reason === 'idempotency_key_unavailable') {
    return 'Managed connector changes and request recovery are unavailable because the idempotency HMAC key file is missing or invalid.'
  }
  return null
})
const currentProject = computed(() =>
  projects.projects.find((project) => project.id === projects.currentProjectId) ?? null,
)
const projectConversations = computed(() => chat.conversations.filter(
  (conversation) => !conversation.project_id || conversation.project_id === projects.currentProjectId,
))
const scopedConnectors = (visibility: 'project' | 'global', kind?: ConnectorKind) =>
  (visibility === 'project' ? connectors.connectors : connectors.globalConnectors).filter(
    (connector) => connector.visibility === visibility && (!kind || connector.kind === kind),
  )
function requestStatus(scope: 'project' | 'global', projectId: string | null) {
  return async (requestId: string) => {
    const response = scope === 'project' && projectId
      ? await connectorsApi.projectRequestStatus(projectId, requestId)
      : await connectorsApi.globalRequestStatus(requestId)
    return response.data
  }
}

function createConnector(scope: 'project' | 'global', kind: ConnectorKind) {
  return async (apiKey: string, requestId: string): Promise<Record<string, unknown>> => {
    const name = createNames[kind].trim()
    if (!name) throw new Error('Enter a connector name')
    if (scope === 'project') {
      const projectId = projects.currentProjectId
      if (!projectId) throw new Error('Choose a project first')
      const response = await connectorsApi.createProjectConnector(projectId, requestId, {
        kind,
        name,
        configuration: {},
        api_key: apiKey,
      })
      return response.data as Record<string, unknown>
    }
    const response = await connectorsApi.createGlobalConnector(requestId, {
      kind,
      name,
      configuration: {},
      api_key: apiKey,
    })
    return response.data as Record<string, unknown>
  }
}

function replaceCredential(scope: 'project' | 'global', connector: ConnectorUsage) {
  return async (apiKey: string, requestId: string): Promise<Record<string, unknown>> => {
    if (scope === 'project') {
      const projectId = projects.currentProjectId
      if (!projectId) throw new Error('Choose a project first')
      const response = await connectorsApi.replaceProjectCredential(
        projectId,
        connector.id,
        requestId,
        { expected_revision: connector.revision, api_key: apiKey },
      )
      return response.data as Record<string, unknown>
    }
    const response = await connectorsApi.replaceGlobalCredential(
      connector.id,
      requestId,
      { expected_revision: connector.revision, api_key: apiKey },
    )
    return response.data as Record<string, unknown>
  }
}

async function onCredentialComplete() {
  await connectors.reload(projects.currentProjectId)
}

async function updateConnector(scope: 'project' | 'global', connector: ConnectorUsage, change: 'toggle' | 'revoke') {
  if (change === 'revoke') {
    const impact = scope === 'global'
      ? 'This affects every project currently using this global connector.'
      : 'This affects searches in this project.'
    if (!window.confirm(`Revoke ${connector.name}? ${impact}`)) return
  }
  const busyKey = `${scope}:${connector.id}`
  busyConnectors.value.add(busyKey)
  pageError.value = null
  try {
    const payload = {
      expected_revision: connector.revision,
      ...(change === 'revoke'
        ? { revoke: true }
        : { enabled: connector.status === 'disabled' }),
    }
    if (scope === 'project' && projects.currentProjectId) {
      await connectorsApi.updateProjectConnector(
        projects.currentProjectId,
        connector.id,
        generateRequestId(),
        payload,
      )
    } else {
      await connectorsApi.updateGlobalConnector(connector.id, generateRequestId(), payload)
    }
    await connectors.reload(projects.currentProjectId)
  } catch {
    pageError.value = 'The connector could not be updated. Reload connector settings and try again.'
  } finally {
    busyConnectors.value.delete(busyKey)
  }
}

async function selectProject(projectId: string) {
  if (!projects.selectProject(projectId)) return
  await connectors.loadProjectContext(projectId)
}

async function goToConversation(id: string) {
  await router.push({ name: 'conversation', params: { conversationId: id } })
}

async function createConversation(title: string) {
  const conversation = await chat.createConversation(title, projects.currentProjectId)
  if (conversation) {
    await router.push({ name: 'conversation', params: { conversationId: conversation.id } })
  }
}

async function createProject(name: string) {
  if (projectCreating.value) return
  projectCreating.value = true
  projectCreationError.value = null
  try {
    const project = await projects.createProject(name)
    await connectors.loadProjectContext(project.id)
  } catch {
    projectCreationError.value = projects.error ?? 'Failed to create project'
  } finally {
    projectCreating.value = false
  }
}

async function signOut() {
  auth.logout()
  chat.clearMessages()
  connectors.clear()
  projects.clear()
  await router.replace({ name: 'login' })
}

onMounted(async () => {
  if (!projects.projects.length) await projects.initialize()
  await connectors.initialize(projects.currentProjectId, true)
  if (!chat.conversations.length) await chat.getConversations()
})
</script>

<template>
  <AppLayout
    title="Connectors"
    :username="auth.user?.username ?? null"
    :conversations="projectConversations"
    :current-conversation-id="null"
    :projects="projects.projects"
    :current-project-id="projects.currentProjectId"
    :create-project-error="projectCreationError"
    :project-creating="projectCreating"
    @select-project="selectProject"
    @create-project="createProject"
    @select-conversation="goToConversation"
    @create-conversation="createConversation"
    @sign-out="signOut"
  >
    <main class="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
      <div class="mx-auto grid w-full max-w-5xl gap-6">
        <header class="grid gap-2">
          <h1 class="text-2xl font-semibold">Search connectors</h1>
          <p class="max-w-3xl text-sm text-muted-foreground">
            Connect Brave Search or Tavily to this project. A configured key is write-only and will not be shown again.
          </p>
        </header>

        <p v-if="connectors.loading" role="status" class="text-sm text-muted-foreground">Loading project connector settings…</p>
        <p v-if="connectors.error || pageError" role="alert" class="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
          {{ pageError ?? connectors.error }}
        </p>
        <p v-if="setupUnavailableMessage" role="status" class="rounded-md border border-border p-3 text-sm text-muted-foreground">
          {{ setupUnavailableMessage }} Restart the API and worker after correcting their shared configuration.
        </p>
        <p v-if="!currentProject && !connectors.loading" class="rounded-md border border-border p-4 text-sm text-muted-foreground">
          No owned project is available. Connector setup requires a project.
        </p>

        <template v-if="currentProject">
          <section
            v-for="kind in kinds"
            :key="kind"
            class="grid gap-4 rounded-xl border border-border bg-card p-4 md:p-6"
            :aria-labelledby="`${kind}-connector-heading`"
          >
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 :id="`${kind}-connector-heading`" class="text-lg font-semibold">{{ displayName(kind) }}</h2>
                <p class="text-sm text-muted-foreground">Project: {{ currentProject.name }}</p>
              </div>
              <span class="rounded-full bg-muted px-3 py-1 text-xs">Project visibility</span>
            </div>

            <div class="grid gap-3 rounded-lg border border-border/70 p-3 md:p-4">
              <h3 class="font-medium">Add a project connector</h3>
              <div class="grid gap-1.5">
                <Label :for="`${kind}-project-name`">Connector name</Label>
                <Input :id="`${kind}-project-name`" v-model="createNames[kind]" :aria-label="`${displayName(kind)} project connector name`" />
              </div>
              <ConnectorCredentialForm
                :key="`new-project-${kind}`"
                :title="displayName(kind)"
                :request-scope-key="`project:${projects.currentProjectId}:create:${kind}`"
                submit-label="Add project connector"
                :can-submit="Boolean(createNames[kind].trim()) && connectors.credentialStoreReady"
                :submit="createConnector('project', kind)"
                :read-status="requestStatus('project', projects.currentProjectId)"
                @complete="onCredentialComplete"
              />
            </div>

            <div class="grid gap-3">
              <h3 class="font-medium">Project connectors</h3>
              <p v-if="!scopedConnectors('project', kind).length" class="text-sm text-muted-foreground">No project {{ kind }} connectors are configured.</p>
              <article
                v-for="connector in scopedConnectors('project', kind)"
                :key="connector.id"
                class="grid gap-3 rounded-lg border border-border/70 p-3 md:grid-cols-[1fr_auto] md:items-start md:p-4"
              >
                <div class="grid gap-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <strong>{{ connector.name }}</strong>
                    <span class="rounded-full bg-muted px-2 py-0.5 text-xs">Project</span>
                    <span class="rounded-full bg-muted px-2 py-0.5 text-xs">{{ connector.status }}</span>
                    <span v-if="connector.is_default" class="text-xs text-primary">Project default</span>
                  </div>
                  <span class="text-xs text-muted-foreground">Revision {{ connector.revision }}</span>
                  <ConnectorCredentialForm
                    :key="`replace-project-${connector.id}-${connector.revision}`"
                    :title="`${connector.name} (${displayName(kind)})`"
                    :request-scope-key="`project:${projects.currentProjectId}:credential:${connector.id}`"
                    submit-label="Replace API key"
                    :can-submit="connectors.credentialStoreReady"
                    :submit="replaceCredential('project', connector)"
                    :read-status="requestStatus('project', projects.currentProjectId)"
                    @complete="onCredentialComplete"
                  />
                </div>
                <div class="flex flex-wrap gap-2">
                  <Button
                    v-if="connector.status !== 'revoked'"
                    size="sm"
                    variant="outline"
                    :disabled="busyConnectors.has(`project:${connector.id}`) || !connectors.credentialStoreReady"
                    @click="updateConnector('project', connector, 'toggle')"
                  >
                    {{ connector.status === 'disabled' ? 'Enable' : 'Disable' }}
                  </Button>
                  <Button
                    v-if="connector.status !== 'revoked'"
                    size="sm"
                    variant="destructive"
                    :disabled="busyConnectors.has(`project:${connector.id}`) || !connectors.credentialStoreReady"
                    @click="updateConnector('project', connector, 'revoke')"
                  >
                    Revoke
                  </Button>
                </div>
              </article>
            </div>

            <div v-if="scopedConnectors('global', kind).length" class="grid gap-2 border-t border-border pt-4">
              <h3 class="font-medium">Shared global connectors available to this project</h3>
              <div v-for="connector in scopedConnectors('global', kind)" :key="connector.id" class="flex flex-wrap items-center gap-2 text-sm">
                <span class="font-medium">{{ connector.name }}</span>
                <span class="rounded-full bg-muted px-2 py-0.5 text-xs">Global</span>
                <span class="rounded-full bg-muted px-2 py-0.5 text-xs">{{ connector.status }}</span>
              </div>
            </div>
          </section>

          <section v-if="connectors.canManageGlobal" class="grid gap-5 rounded-xl border border-primary/30 bg-card p-4 md:p-6">
            <div>
              <h2 class="text-lg font-semibold">Global connector management</h2>
              <p class="mt-1 text-sm text-muted-foreground">
                Global connectors are visible to eligible projects. Rotating or revoking one can interrupt searches in every project using it.
              </p>
            </div>
            <div v-for="kind in kinds" :key="`global-${kind}`" class="grid gap-3 border-t border-border pt-4">
              <h3 class="font-medium">Add global {{ displayName(kind) }}</h3>
              <div class="grid gap-1.5">
                <Label :for="`${kind}-global-name`">Connector name</Label>
                <Input :id="`${kind}-global-name`" v-model="createNames[kind]" :aria-label="`${displayName(kind)} global connector name`" />
              </div>
              <ConnectorCredentialForm
                :key="`new-global-${kind}`"
                :title="displayName(kind)"
                :request-scope-key="`global:create:${kind}`"
                submit-label="Add global connector"
                :can-submit="Boolean(createNames[kind].trim()) && connectors.credentialStoreReady"
                :submit="createConnector('global', kind)"
                :read-status="requestStatus('global', null)"
                @complete="onCredentialComplete"
              />
              <article
                v-for="connector in scopedConnectors('global', kind)"
                :key="connector.id"
                class="grid gap-3 rounded-lg border border-border/70 p-3 md:grid-cols-[1fr_auto] md:items-start md:p-4"
              >
                <div class="grid gap-1">
                  <div class="flex flex-wrap items-center gap-2">
                    <strong>{{ connector.name }}</strong>
                    <span class="rounded-full bg-primary/10 px-2 py-0.5 text-xs">Global visibility</span>
                    <span class="rounded-full bg-muted px-2 py-0.5 text-xs">{{ connector.status }}</span>
                  </div>
                  <span class="text-xs text-muted-foreground">Revision {{ connector.revision }}</span>
                  <ConnectorCredentialForm
                    :key="`replace-global-${connector.id}-${connector.revision}`"
                    :title="`${connector.name} (${displayName(kind)})`"
                    :request-scope-key="`global:credential:${connector.id}`"
                    submit-label="Rotate global API key"
                    :can-submit="connectors.credentialStoreReady"
                    :submit="replaceCredential('global', connector)"
                    :read-status="requestStatus('global', null)"
                    @complete="onCredentialComplete"
                  />
                </div>
                <Button
                  v-if="connector.status !== 'revoked'"
                  size="sm"
                  variant="destructive"
                  :disabled="busyConnectors.has(`global:${connector.id}`) || !connectors.credentialStoreReady"
                  @click="updateConnector('global', connector, 'revoke')"
                >
                  Revoke globally
                </Button>
              </article>
            </div>
          </section>
        </template>
      </div>
    </main>
  </AppLayout>
</template>
