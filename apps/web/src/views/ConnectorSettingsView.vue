<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import AppLayout from '@/layouts/AppLayout.vue'
import ConnectorCard from '@/components/connectors/ConnectorCard.vue'
import ConnectorConfigurationDialog from '@/components/connectors/ConnectorConfigurationDialog.vue'
import { Button } from '@/components/ui'
import { connectorCatalog, type ConnectorCatalogEntry } from '@/lib/connectorCatalog'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useConnectorsStore } from '@/stores/connectors'
import { useProjectsStore } from '@/stores/projects'
import type { ConnectorUsage } from '@/types'

const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const connectors = useConnectorsStore()
const projects = useProjectsStore()
const pageError = ref<string | null>(null)
const projectCreationError = ref<string | null>(null)
const projectCreating = ref(false)
const returnFocusElement = ref<HTMLElement | null>(null)

interface DialogSelection {
  entry: ConnectorCatalogEntry
  connector: ConnectorUsage | null
  scope: 'global' | 'project'
  projectId: string | null
}
interface CatalogCard {
  entry: ConnectorCatalogEntry
  connector: ConnectorUsage | null
}

const selectedDialog = ref<DialogSelection | null>(null)
const currentProject = computed(() =>
  projects.projects.find((project) => project.id === projects.currentProjectId) ?? null,
)
const projectConversations = computed(() => chat.conversations.filter(
  (conversation) => !conversation.project_id || conversation.project_id === projects.currentProjectId,
))
const globalInstances = computed(() => {
  const source = connectors.canManageGlobal ? connectors.globalConnectors : connectors.connectors
  return source.filter((connector) => connector.visibility === 'global')
})
const catalogCards = computed<CatalogCard[]>(() => connectorCatalog.flatMap<CatalogCard>((entry) => {
  const instances = globalInstances.value.filter((connector) => connector.kind === entry.kind)
  return instances.length
    ? instances.map((connector) => ({ entry, connector }))
    : [{ entry, connector: null }]
}))
const legacyProjectConnectors = computed(() => connectors.connectors.filter(
  (connector) => connector.visibility === 'project' && connector.kind !== 'aws',
))
const catalogLoading = computed(() => connectors.loading || connectors.globalLoading)
const catalogError = computed(() => pageError.value ?? connectors.error ?? connectors.globalError)
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

function selectGlobalConnector(
  entry: ConnectorCatalogEntry,
  connector: ConnectorUsage | null,
  event: Event,
): void {
  returnFocusElement.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  selectedDialog.value = { entry, connector, scope: 'global', projectId: null }
}

function selectLegacyConnector(connector: ConnectorUsage, event: Event): void {
  const entry = connectorCatalog.find((item) => item.kind === connector.kind)
  if (!entry || !projects.currentProjectId) return
  returnFocusElement.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  selectedDialog.value = {
    entry,
    connector,
    scope: 'project',
    projectId: projects.currentProjectId,
  }
}

function closeDialog(open: boolean): void {
  if (!open) selectedDialog.value = null
}

async function refreshConnectorViews(): Promise<void> {
  pageError.value = null
  await connectors.reload(projects.currentProjectId)
}

function showDialogError(message: string): void {
  pageError.value = message
}

async function selectProject(projectId: string): Promise<void> {
  if (!projects.selectProject(projectId)) return
  await connectors.loadProjectContext(projectId)
}

async function goToConversation(id: string): Promise<void> {
  await router.push({ name: 'conversation', params: { conversationId: id } })
}

async function createConversation(title: string): Promise<void> {
  const conversation = await chat.createConversation(title, projects.currentProjectId)
  if (conversation) {
    await router.push({ name: 'conversation', params: { conversationId: conversation.id } })
  }
}

async function createProject(name: string): Promise<void> {
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

async function signOut(): Promise<void> {
  auth.logout()
  chat.clearMessages()
  connectors.clear()
  projects.clear()
  await router.replace({ name: 'login' })
}

watch(() => projects.currentProjectId, (next, previous) => {
  if (next !== previous) selectedDialog.value = null
})

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
            Brave Search and Tavily Search are shared Global integrations. Each project keeps its own search defaults.
          </p>
        </header>

        <p v-if="catalogLoading" role="status" class="text-sm text-muted-foreground">Loading connector catalog…</p>
        <p v-if="catalogError" role="alert" class="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
          {{ catalogError }}
        </p>
        <p v-if="setupUnavailableMessage && !catalogError" role="status" class="rounded-md border border-border p-3 text-sm text-muted-foreground">
          {{ setupUnavailableMessage }} Restart the API and worker after correcting their shared configuration.
        </p>
        <p v-if="!currentProject && !catalogLoading && !catalogError" class="rounded-md border border-border p-4 text-sm text-muted-foreground">
          No owned project is available. Global connector use requires a project context.
        </p>

        <template v-if="!catalogLoading && !catalogError && currentProject">
          <section class="grid gap-4" aria-labelledby="global-catalog-heading" data-connector-catalog>
            <header class="flex flex-wrap items-end justify-between gap-3">
              <div class="grid gap-1">
                <h2 id="global-catalog-heading" class="text-lg font-semibold">Global search connectors</h2>
                <p class="text-sm text-muted-foreground">
                  Supported services are shared across eligible projects. Select a card to view details or configure it.
                </p>
              </div>
            </header>

            <div class="grid gap-3 md:grid-cols-2" data-connector-card-grid>
              <ConnectorCard
                v-for="card in catalogCards"
                :key="`${card.entry.kind}:${card.connector?.id ?? 'unconfigured'}`"
                :entry="card.entry"
                :connector="card.connector ?? undefined"
                @select="selectGlobalConnector(card.entry, card.connector, $event)"
              />
            </div>
          </section>

          <section v-if="legacyProjectConnectors.length" class="grid gap-4" aria-labelledby="legacy-connectors-heading" data-legacy-connectors>
            <header class="grid gap-1">
              <h2 id="legacy-connectors-heading" class="text-lg font-semibold">Legacy Project connectors</h2>
              <p class="max-w-3xl text-sm text-muted-foreground">
                These existing private connectors keep their original project scope. Choose a Global connector to replace this legacy Project default in Project Settings before retiring a private key.
              </p>
              <RouterLink
                v-if="projects.currentProjectId"
                :to="{ name: 'project-settings', params: { projectId: projects.currentProjectId } }"
                class="w-fit text-sm font-medium text-primary underline-offset-4 hover:underline"
              >
                Review Project Settings
              </RouterLink>
            </header>
            <ul class="grid gap-3 md:grid-cols-2">
              <li v-for="connector in legacyProjectConnectors" :key="connector.id" class="grid gap-1 rounded-xl border border-border bg-card p-4">
                <div class="flex flex-wrap items-center gap-2">
                  <strong>{{ connector.name }}</strong>
                  <span class="rounded-full border border-amber-500/40 px-2 py-0.5 text-xs">Project visibility</span>
                  <span class="rounded-full bg-muted px-2 py-0.5 text-xs">{{ connector.status }}</span>
                  <span v-if="connector.is_default" class="text-xs text-primary">Current project default</span>
                </div>
                <span class="text-xs text-muted-foreground">{{ connector.kind === 'brave' ? 'Brave Search' : 'Tavily Search' }} · Revision {{ connector.revision }}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  class="w-fit"
                  :aria-label="`Manage legacy ${connector.name}`"
                  @click="selectLegacyConnector(connector, $event)"
                >
                  Manage legacy connector
                </Button>
              </li>
            </ul>
          </section>
        </template>

        <ConnectorConfigurationDialog
          :open="Boolean(selectedDialog)"
          :entry="selectedDialog?.entry ?? connectorCatalog[0]"
          :scope="selectedDialog?.scope ?? 'global'"
          :connector="selectedDialog?.connector ?? null"
          :project-id="selectedDialog?.projectId ?? null"
          :selected-project-id="projects.currentProjectId"
          :can-manage-global="connectors.canManageGlobal"
          :credential-store-ready="connectors.credentialStoreReady"
          :return-focus-element="returnFocusElement"
          @update:open="closeDialog"
          @complete="refreshConnectorViews"
          @error="showDialogError"
        />
      </div>
    </main>
  </AppLayout>
</template>
