<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Button, Input, Label } from '@/components/ui'
import { useChatStore } from '@/stores/chat'
import { useConnectorsStore } from '@/stores/connectors'
import { useProjectsStore } from '@/stores/projects'
import { projectsApi } from '@/services/api'
import type { ConnectorKind, ConnectorUsage } from '@/types'

const props = defineProps<{ projectId: string }>()
const router = useRouter()
const projects = useProjectsStore()
const chat = useChatStore()
const connectors = useConnectorsStore()
const activeProject = computed(() => projects.projects.find((item) => item.id === props.projectId) ?? null)
const project = computed(() => activeProject.value
  ?? projects.archivedProjects.find((item) => item.id === props.projectId)
  ?? null)
const isArchivedProject = computed(() => Boolean(project.value?.archived_at))
const name = ref('')
const busy = ref(false)
const archiving = ref(false)
const busyDefault = ref(false)
const pageError = ref<string | null>(null)
const restoreBusyId = ref<string | null>(null)
const lifecycleErrors = ref<Record<string, string>>({})
let scopeGeneration = 0

watch(project, (selected) => {
  if (selected) name.value = selected.name
}, { immediate: true })

async function loadProject(): Promise<void> {
  const generation = ++scopeGeneration
  pageError.value = null
  if (!projects.projects.length) await projects.initialize()
  if (generation !== scopeGeneration) return
  await projects.loadArchivedProjects()
  if (generation !== scopeGeneration) return
  if (!project.value) {
    try {
      const response = await projectsApi.get(props.projectId)
      if (generation !== scopeGeneration) return
      if (response.data.archived_at) {
        projects.archivedProjects = [response.data, ...projects.archivedProjects.filter((item) => item.id !== props.projectId)]
      } else if (!projects.projects.some((item) => item.id === props.projectId)) {
        projects.projects = [...projects.projects, response.data]
      }
    } catch {
      pageError.value = 'This project is not available to your account.'
      return
    }
  }
  if (generation !== scopeGeneration || !project.value) return
  if (!isArchivedProject.value && projects.selectProject(props.projectId)) {
    await connectors.loadProjectContext(props.projectId)
  }
}

watch(() => props.projectId, () => {
  void loadProject()
})

onMounted(() => {
  void loadProject()
})

async function saveName(): Promise<void> {
  if (!project.value || isArchivedProject.value || busy.value) return
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

async function archiveCurrentProject(): Promise<void> {
  if (!activeProject.value || archiving.value || projects.projects.length <= 1) return
  if (!window.confirm(`Archive ${activeProject.value.name}? You can restore it later.`)) return
  archiving.value = true
  pageError.value = null
  try {
    await projects.archiveProject(props.projectId)
    await chat.getConversations()
    const nextProjectId = projects.currentProjectId
    if (nextProjectId) {
      await connectors.loadProjectContext(nextProjectId)
      await router.replace({ name: 'project-settings', params: { projectId: nextProjectId } })
    } else {
      await connectors.loadProjectContext(null)
      await router.replace({ name: 'global-settings' })
    }
  } catch {
    pageError.value = projects.error ?? 'The project could not be archived. Reload and try again.'
  } finally {
    archiving.value = false
  }
}

async function restoreProject(projectId: string): Promise<void> {
  if (restoreBusyId.value) return
  restoreBusyId.value = projectId
  delete lifecycleErrors.value[projectId]
  try {
    await projects.restoreProject(projectId)
    if (
      props.projectId === projectId &&
      router.currentRoute.value.name === 'project-settings' &&
      projects.currentProjectId !== projectId
    ) {
      if (projects.currentProjectId) {
        await router.replace({ name: 'project-settings', params: { projectId: projects.currentProjectId } })
      } else {
        await router.replace({ name: 'global-settings' })
      }
    }
  } catch {
    lifecycleErrors.value[projectId] = projects.error ?? 'The project could not be restored. Reload and try again.'
  } finally {
    restoreBusyId.value = null
  }
}

function globalChoices(): ConnectorUsage[] {
  return connectors.connectors.filter((connector) =>
    (connector.kind === 'brave' || connector.kind === 'tavily') &&
    connector.visibility === 'global' &&
    connector.status === 'configured',
  )
}

function currentDefaultId(): string | null {
  return connectors.defaults.brave?.connector_id ?? connectors.defaults.tavily?.connector_id ?? null
}

function currentDefaultConnector(): ConnectorUsage | undefined {
  const id = currentDefaultId()
  return id ? connectors.connectors.find((connector) => connector.id === id) : undefined
}

function currentDefaultKind(): ConnectorKind | null {
  const connector = currentDefaultConnector()
  if (connector?.kind === 'brave' || connector?.kind === 'tavily') return connector.kind
  if (connectors.defaults.brave?.connector_id) return 'brave'
  if (connectors.defaults.tavily?.connector_id) return 'tavily'
  return null
}

function defaultUnavailable(): boolean {
  return !globalChoices().length && !currentDefaultId()
}

async function updateDefault(event: Event): Promise<void> {
  if (!project.value || isArchivedProject.value || busyDefault.value) return
  const connectorId = (event.target as HTMLSelectElement).value || null
  const selected = connectorId
    ? connectors.connectors.find((connector) => connector.id === connectorId)
    : undefined
  const kind = selected?.kind === 'brave' || selected?.kind === 'tavily'
    ? selected.kind
    : currentDefaultKind()
  if (!kind) return
  busyDefault.value = true
  pageError.value = null
  try {
    await connectors.setDefault(props.projectId, kind, connectorId)
  } catch {
    pageError.value = 'The project search default could not be updated. Reload and try again.'
    await connectors.loadProjectContext(props.projectId)
  } finally {
    busyDefault.value = false
  }
}

function displayName(kind: ConnectorUsage['kind']): string {
  return kind === 'brave' ? 'Brave Search' : kind === 'tavily' ? 'Tavily Search' : 'AWS'
}
</script>

<template>
  <section v-if="project" class="grid gap-6" aria-labelledby="project-settings-heading">
    <header class="grid gap-1">
      <h1 id="project-settings-heading" class="text-2xl font-semibold">Project Settings</h1>
      <p class="text-sm text-muted-foreground">
        {{ isArchivedProject ? `${project.name} is archived. Its conversations and resources are retained.` : `Manage ${project.name} and its search default.` }}
      </p>
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
          <Input id="project-name" v-model="name" aria-label="Project name" maxlength="255" :disabled="busy || isArchivedProject" />
        </div>
        <div class="flex flex-wrap gap-2">
          <Button type="submit" :disabled="busy || isArchivedProject || !name.trim() || name.trim() === project.name">
            {{ busy ? 'Saving…' : 'Save name' }}
          </Button>
          <Button
            v-if="!isArchivedProject"
            type="button"
            variant="outline"
            class="border-destructive bg-background text-destructive hover:bg-destructive/10 hover:text-destructive"
            aria-label="Archive project"
            :disabled="archiving || projects.projects.length <= 1"
            @click="archiveCurrentProject"
          >
            {{ archiving ? 'Archiving…' : 'Archive project' }}
          </Button>
          <Button v-else type="button" :aria-label="`Restore project ${project.name}`" :disabled="restoreBusyId === project.id" @click="restoreProject(project.id)">
            {{ restoreBusyId === project.id ? 'Restoring…' : 'Restore project' }}
          </Button>
        </div>
        <p v-if="!isArchivedProject && projects.projects.length <= 1" class="text-xs text-muted-foreground">
          Keep at least one project in your account.
        </p>
        <p v-if="lifecycleErrors[project.id]" role="alert" class="text-xs text-destructive">{{ lifecycleErrors[project.id] }}</p>
      </form>
    </section>

    <section v-if="!isArchivedProject" class="grid gap-4 rounded-xl border border-border bg-card p-4 md:p-6" aria-labelledby="search-defaults-heading">
      <div>
        <h2 id="search-defaults-heading" class="text-lg font-semibold">Search default</h2>
        <p class="text-sm text-muted-foreground">
          Choose one shared search connector for this project. Projects can use different defaults while sharing the same connector.
        </p>
        <RouterLink
          to="/connectors"
          class="mt-2 inline-block text-sm font-medium text-primary underline-offset-4 hover:underline"
        >
          Browse Global connectors
        </RouterLink>
      </div>
      <div v-if="connectors.loading" role="status" class="text-sm text-muted-foreground">Loading project search settings…</div>
      <div v-else class="grid max-w-xl gap-2">
        <Label for="project-search-default">Search default</Label>
        <select
          id="project-search-default"
          aria-label="Search default"
          :value="currentDefaultId() ?? ''"
          :disabled="busyDefault || defaultUnavailable()"
          class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          @change="updateDefault($event)"
        >
          <option value="">No default — choose when needed</option>
          <option v-if="currentDefaultConnector() && !globalChoices().some((item) => item.id === currentDefaultId())" :value="currentDefaultId() ?? ''" disabled>
            {{ currentDefaultConnector()?.name }} · {{ currentDefaultConnector()?.visibility === 'project' ? 'legacy Project connector' : `${currentDefaultConnector()?.status} Global connector` }}
          </option>
          <option v-else-if="currentDefaultId() && !currentDefaultConnector()" :value="currentDefaultId() ?? ''" disabled>
            Current default unavailable
          </option>
          <option
            v-for="connector in globalChoices()"
            :key="connector.id"
            :value="connector.id"
          >
            {{ displayName(connector.kind) }} · {{ connector.name }} · Global
          </option>
        </select>
        <p v-if="currentDefaultConnector()?.visibility === 'project'" class="text-xs text-amber-700 dark:text-amber-300">
          Choose a Global connector to replace this legacy Project default. Existing project access remains unchanged until you select one.
        </p>
        <p v-else-if="currentDefaultConnector() && currentDefaultConnector()?.status !== 'configured'" class="text-xs text-muted-foreground">
          The current Global connector is disabled. Select another connector or clear this default.
        </p>
        <p v-else-if="defaultUnavailable()" class="text-xs text-muted-foreground">
          No configured global search connector is available. Ask the installation operator to configure one.
        </p>
        <p v-else class="text-xs text-muted-foreground">
          Automatic mode starts with this connector. If it returns an error, the model can try another available search connector. Manual connector selections remain available. With no default, a connector is chosen automatically when only one is eligible; otherwise you can choose manually.
        </p>
      </div>
    </section>

    <section v-if="projects.archivedProjects.length" class="grid gap-3 rounded-xl border border-border bg-card p-4 md:p-6" aria-labelledby="archived-projects-heading">
      <div>
        <h2 id="archived-projects-heading" class="text-lg font-semibold">Archived Projects</h2>
        <p class="text-sm text-muted-foreground">Restore a project to continue working with its conversations and resources.</p>
      </div>
      <ul class="grid gap-2">
        <li v-for="archived in projects.archivedProjects" :key="archived.id" class="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3">
          <RouterLink :to="{ name: 'project-settings', params: { projectId: archived.id } }" class="font-medium hover:underline">{{ archived.name }}</RouterLink>
          <Button type="button" variant="outline" :aria-label="`Restore ${archived.name}`" :disabled="restoreBusyId === archived.id" @click="restoreProject(archived.id)">
            {{ restoreBusyId === archived.id ? 'Restoring…' : 'Restore' }}
          </Button>
          <p v-if="lifecycleErrors[archived.id]" role="alert" class="w-full text-sm text-destructive">{{ lifecycleErrors[archived.id] }}</p>
        </li>
      </ul>
    </section>
  </section>

  <p v-else-if="projects.loading" role="status" class="text-sm text-muted-foreground">Loading project…</p>
  <section v-else class="grid gap-2" role="alert">
    <h1 class="text-2xl font-semibold">Project unavailable</h1>
    <p class="text-sm text-muted-foreground">This project is not available to your account.</p>
  </section>
</template>
