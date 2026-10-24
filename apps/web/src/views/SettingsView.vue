<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import ProjectSettings from '@/components/settings/ProjectSettings.vue'
import GlobalSettings from '@/components/settings/GlobalSettings.vue'
import AppLayout from '@/layouts/AppLayout.vue'
import { useArtifactsStore } from '@/stores/artifacts'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useConnectorsStore } from '@/stores/connectors'
import { useProjectsStore } from '@/stores/projects'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const artifacts = useArtifactsStore()
const projects = useProjectsStore()
const connectors = useConnectorsStore()
const projectCreating = ref(false)
const projectCreationError = ref<string | null>(null)
const isGlobal = computed(() => route.name === 'global-settings')
const routeProjectId = computed(() =>
  typeof route.params.projectId === 'string' ? route.params.projectId : null,
)
const currentSettingsProjectId = computed(() => {
  if (routeProjectId.value && projects.projects.some((item) => item.id === routeProjectId.value)) {
    return routeProjectId.value
  }
  return projects.currentProjectId
})
const visibleConversations = computed(() => chat.conversations.filter(
  (conversation) => !conversation.project_id || conversation.project_id === projects.currentProjectId,
))
const busy = computed(() => projects.loading || projectCreating.value)

let generation = 0
async function syncSettingsContext(): Promise<void> {
  const current = ++generation
  if (!projects.projects.length && !projects.loading) await projects.initialize()
  if (!chat.conversations.length) await chat.getConversations()
  if (current !== generation) return
  if (isGlobal.value) {
    await connectors.loadGlobalManagement()
    return
  }
  const projectId = routeProjectId.value
  if (projectId && projects.projects.some((project) => project.id === projectId)) {
    projects.selectProject(projectId)
  }
}

watch(() => [route.name, route.params.projectId], () => {
  void syncSettingsContext()
}, { immediate: true })

async function selectProject(projectId: string): Promise<void> {
  if (projectId === projects.currentProjectId || !projects.selectProject(projectId)) return
  projectCreationError.value = null
  const currentConversationProject = chat.currentConversation?.project_id
  if (currentConversationProject && currentConversationProject !== projectId) {
    chat.deselectConversation()
  }
  await connectors.loadProjectContext(projectId)
  if (route.name === 'project-settings') {
    await router.replace({ name: 'project-settings', params: { projectId } })
  }
}

async function createProject(name: string): Promise<void> {
  if (projectCreating.value) return
  projectCreating.value = true
  projectCreationError.value = null
  try {
    const project = await projects.createProject(name)
    await connectors.loadProjectContext(project.id)
    if (route.name === 'project-settings') {
      await router.replace({ name: 'project-settings', params: { projectId: project.id } })
    }
  } catch {
    projectCreationError.value = projects.error ?? 'Failed to create project'
  } finally {
    projectCreating.value = false
  }
}

async function createConversation(title: string): Promise<void> {
  const conversation = await chat.createConversation(title, projects.currentProjectId)
  if (conversation) await router.push({ name: 'conversation', params: { conversationId: conversation.id } })
}

async function selectConversation(id: string): Promise<void> {
  await router.push({ name: 'conversation', params: { conversationId: id } })
}

async function signOut(): Promise<void> {
  auth.logout()
  chat.clearMessages()
  chat.conversations = []
  artifacts.clear()
  connectors.clear()
  projects.clear()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <AppLayout
    title="Settings"
    :username="auth.user?.username ?? null"
    :conversations="visibleConversations"
    :current-conversation-id="chat.currentConversation?.id ?? null"
    :busy="busy"
    :projects="projects.projects"
    :current-project-id="projects.currentProjectId"
    :create-project-error="projectCreationError"
    :project-creating="projectCreating"
    @create-conversation="createConversation"
    @select-conversation="selectConversation"
    @select-project="selectProject"
    @create-project="createProject"
    @sign-out="signOut"
  >
    <div class="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
      <div class="mx-auto grid w-full max-w-5xl gap-6">
        <header class="grid gap-1">
          <h1 class="text-2xl font-semibold">Settings</h1>
          <p class="text-sm text-muted-foreground">Project choices stay with each project. Installation setup is managed globally.</p>
        </header>

        <nav aria-label="Settings sections" class="flex flex-wrap gap-2 border-b border-border">
          <RouterLink
            v-if="currentSettingsProjectId"
            :to="{ name: 'project-settings', params: { projectId: currentSettingsProjectId } }"
            aria-label="Project Settings tab"
            :aria-current="!isGlobal ? 'page' : undefined"
            class="border-b-2 px-3 py-2 text-sm font-medium"
            :class="!isGlobal ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
          >
            Project Settings
          </RouterLink>
          <span v-else class="px-3 py-2 text-sm text-muted-foreground" aria-disabled="true">Project Settings</span>
          <RouterLink
            to="/settings/global"
            aria-label="Global Settings tab"
            :aria-current="isGlobal ? 'page' : undefined"
            class="border-b-2 px-3 py-2 text-sm font-medium"
            :class="isGlobal ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'"
          >
            Global Settings
          </RouterLink>
        </nav>

        <ProjectSettings v-if="!isGlobal && routeProjectId" :project-id="routeProjectId" />
        <GlobalSettings v-else-if="isGlobal" />
        <section v-else class="grid gap-2" role="status">
          <p class="text-sm text-muted-foreground">Select a project to view its settings.</p>
        </section>
      </div>
    </div>
  </AppLayout>
</template>
