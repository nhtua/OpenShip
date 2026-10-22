<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Input, Label } from '@/components/ui'
import { useConnectorsStore } from '@/stores/connectors'
import { useProjectsStore } from '@/stores/projects'
import type { ConnectorKind, ConnectorUsage } from '@/types'

const props = defineProps<{ projectId: string }>()
const router = useRouter()
const projects = useProjectsStore()
const connectors = useConnectorsStore()
const project = computed(() => projects.projects.find((item) => item.id === props.projectId) ?? null)
const name = ref('')
const busy = ref(false)
const deleting = ref(false)
const busyDefaults = ref(new Set<ConnectorKind>())
const pageError = ref<string | null>(null)
let scopeGeneration = 0

watch(project, (selected) => {
  if (selected) name.value = selected.name
}, { immediate: true })

async function loadProject(): Promise<void> {
  const generation = ++scopeGeneration
  pageError.value = null
  if (!projects.projects.length) await projects.initialize()
  if (generation !== scopeGeneration || !project.value) return
  projects.selectProject(props.projectId)
  await connectors.loadProjectContext(props.projectId)
}

watch(() => props.projectId, () => {
  void loadProject()
})

onMounted(() => {
  void loadProject()
})

async function saveName(): Promise<void> {
  if (!project.value || busy.value) return
  busy.value = true
  pageError.value = null
  try {
    await projects.renameProject(props.projectId, name.value)
  } catch {
    pageError.value = projects.error ?? 'The project name could not be updated.'
  } finally {
    busy.value = false
  }
}

async function deleteCurrentProject(): Promise<void> {
  if (!project.value || deleting.value || projects.projects.length <= 1) return
  if (!window.confirm(`Delete ${project.value.name}? Projects with conversations or retained data cannot be deleted.`)) return
  deleting.value = true
  pageError.value = null
  try {
    await projects.deleteProject(props.projectId)
    const nextProjectId = projects.currentProjectId
    if (nextProjectId) {
      await connectors.loadProjectContext(nextProjectId)
      await router.replace({ name: 'project-settings', params: { projectId: nextProjectId } })
    } else {
      await connectors.loadProjectContext(null)
      await router.replace({ name: 'global-settings' })
    }
  } catch {
    pageError.value = projects.error ?? 'The project could not be deleted. It may contain retained data.'
  } finally {
    deleting.value = false
  }
}

function globalChoices(kind: ConnectorKind): ConnectorUsage[] {
  return connectors.connectors.filter((connector) =>
    connector.kind === kind &&
    connector.visibility === 'global' &&
    connector.status === 'configured',
  )
}

function currentDefaultConnector(kind: ConnectorKind): ConnectorUsage | undefined {
  const id = connectors.defaults[kind]?.connector_id
  return id ? connectors.connectors.find((connector) => connector.id === id) : undefined
}

function defaultUnavailable(kind: ConnectorKind): boolean {
  const id = connectors.defaults[kind]?.connector_id
  return !globalChoices(kind).length && !id
}

async function updateDefault(kind: ConnectorKind, event: Event): Promise<void> {
  if (!project.value || busyDefaults.value.has(kind)) return
  const connectorId = (event.target as HTMLSelectElement).value || null
  busyDefaults.value = new Set(busyDefaults.value).add(kind)
  pageError.value = null
  try {
    await connectors.setDefault(props.projectId, kind, connectorId)
  } catch {
    pageError.value = 'The project search default could not be updated. Reload and try again.'
    await connectors.loadProjectContext(props.projectId)
  } finally {
    const next = new Set(busyDefaults.value)
    next.delete(kind)
    busyDefaults.value = next
  }
}

const defaultKinds: ConnectorKind[] = ['brave', 'tavily']
const displayName = (kind: ConnectorKind) => kind === 'brave' ? 'Brave Search' : 'Tavily Search'
</script>

<template>
  <section v-if="project" class="grid gap-6" aria-labelledby="project-settings-heading">
    <header class="grid gap-1">
      <h1 id="project-settings-heading" class="text-2xl font-semibold">Project Settings</h1>
      <p class="text-sm text-muted-foreground">Manage {{ project.name }} and its search defaults.</p>
    </header>

    <p v-if="pageError || projects.error || connectors.error" role="alert" class="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
      {{ pageError ?? projects.error ?? connectors.error }}
    </p>

    <section class="grid gap-4 rounded-xl border border-border bg-card p-4 md:p-6" aria-labelledby="project-identity-heading">
      <div>
        <h2 id="project-identity-heading" class="text-lg font-semibold">Project</h2>
        <p class="text-sm text-muted-foreground">The project ID keeps its conversations and settings together.</p>
      </div>
      <form class="grid max-w-xl gap-3" @submit.prevent="saveName">
        <div class="grid gap-1.5">
          <Label for="project-name">Project name</Label>
          <Input id="project-name" v-model="name" aria-label="Project name" maxlength="255" :disabled="busy" />
        </div>
        <div class="flex flex-wrap gap-2">
          <Button type="submit" :disabled="busy || !name.trim() || name.trim() === project.name">
            {{ busy ? 'Saving…' : 'Save name' }}
          </Button>
          <Button
            type="button"
            variant="destructive"
            aria-label="Delete project"
            :disabled="deleting || projects.projects.length <= 1"
            @click="deleteCurrentProject"
          >
            {{ deleting ? 'Deleting…' : 'Delete project' }}
          </Button>
        </div>
        <p v-if="projects.projects.length <= 1" class="text-xs text-muted-foreground">
          Keep at least one project in your account.
        </p>
        <p class="text-xs text-muted-foreground">
          Projects with conversations, workspace files, connectors, or retained history cannot be deleted.
        </p>
      </form>
    </section>

    <section class="grid gap-4 rounded-xl border border-border bg-card p-4 md:p-6" aria-labelledby="search-defaults-heading">
      <div>
        <h2 id="search-defaults-heading" class="text-lg font-semibold">Search defaults</h2>
        <p class="text-sm text-muted-foreground">
          Choose which shared connector this project uses for each search service. These choices are independent per project.
        </p>
      </div>
      <div v-if="connectors.loading" role="status" class="text-sm text-muted-foreground">Loading project search settings…</div>
      <div v-for="kind in defaultKinds" :key="kind" class="grid max-w-xl gap-2">
        <Label :for="`${kind}-project-default`">{{ displayName(kind) }} default</Label>
        <select
          :id="`${kind}-project-default`"
          :aria-label="`${displayName(kind)} default`"
          :value="connectors.defaults[kind]?.connector_id ?? ''"
          :disabled="connectors.loading || busyDefaults.has(kind) || defaultUnavailable(kind)"
          class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          @change="updateDefault(kind, $event)"
        >
          <option value="">No default — choose for each search</option>
          <option
            v-if="currentDefaultConnector(kind) && currentDefaultConnector(kind)?.visibility === 'project'"
            :value="connectors.defaults[kind]?.connector_id ?? ''"
            disabled
          >
            {{ currentDefaultConnector(kind)?.name }} · legacy Project connector
          </option>
          <option
            v-else-if="currentDefaultConnector(kind) && currentDefaultConnector(kind)?.status !== 'configured'"
            :value="connectors.defaults[kind]?.connector_id ?? ''"
            disabled
          >
            {{ currentDefaultConnector(kind)?.name }} · {{ currentDefaultConnector(kind)?.status }} Global connector
          </option>
          <option
            v-else-if="connectors.defaults[kind]?.connector_id && !currentDefaultConnector(kind)"
            :value="connectors.defaults[kind]?.connector_id ?? ''"
            disabled
          >
            Current default unavailable
          </option>
          <option
            v-for="connector in globalChoices(kind)"
            :key="connector.id"
            :value="connector.id"
          >
            {{ connector.name }} · Global
          </option>
        </select>
        <p v-if="currentDefaultConnector(kind)?.visibility === 'project'" class="text-xs text-amber-700 dark:text-amber-300">
          Choose a Global connector to replace this legacy Project default. Existing project access remains unchanged until you select one.
        </p>
        <p v-else-if="currentDefaultConnector(kind)?.status === 'disabled'" class="text-xs text-muted-foreground">
          The current Global connector is disabled. Select another connector or clear this default.
        </p>
        <p v-else-if="defaultUnavailable(kind)" class="text-xs text-muted-foreground">
          No global {{ displayName(kind) }} connector is available. Ask the installation operator to configure one.
        </p>
        <p v-else class="text-xs text-muted-foreground">
          If this project has no default, the search asks you to choose when more than one connector is eligible.
        </p>
      </div>
    </section>
  </section>

  <p v-else-if="projects.loading" role="status" class="text-sm text-muted-foreground">Loading project…</p>
  <section v-else class="grid gap-2" role="alert">
    <h1 class="text-2xl font-semibold">Project unavailable</h1>
    <p class="text-sm text-muted-foreground">This project is not available to your account.</p>
  </section>
</template>
