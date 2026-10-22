<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Conversation, ProjectSummary } from '@/types'
import { Button, Icon } from '@/components/ui'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui'
import { useSidebar } from '@/components/ui/sidebar/utils'
import { RouterLink } from 'vue-router'

const props = withDefaults(
  defineProps<{
    conversations?: Conversation[]
    currentConversationId?: string | null
    busy?: boolean
    username?: string | null
    projects?: ProjectSummary[]
    currentProjectId?: string | null
    createProjectError?: string | null
    projectCreating?: boolean
  }>(),
  {
    conversations: () => [],
    currentConversationId: null,
    busy: false,
    username: null,
    projects: () => [],
    currentProjectId: null,
    createProjectError: null,
    projectCreating: false,
  },
)

const emit = defineEmits<{
  createConversation: [title: string]
  selectConversation: [id: string]
  signOut: []
  selectProject: [id: string]
  createProject: [name: string]
}>()

const { setOpen, state } = useSidebar()

const showUserMenu = ref(false)
const showAllConversations = ref(false)
const showProjectMenu = ref(false)
const projectTrigger = ref<HTMLButtonElement | null>(null)
const projectMenu = ref<HTMLElement | null>(null)
const projectCreateDialog = ref<HTMLElement | null>(null)
const projectNameInput = ref<HTMLInputElement | null>(null)
const showCreateProject = ref(false)
const newProjectName = ref('')
const projectNameError = ref<string | null>(null)
const currentProject = computed(() =>
  props.projects.find((project) => project.id === props.currentProjectId) ?? props.projects[0] ?? null,
)

function expandSidebar() {
  setOpen(true)
}

function toggleUserMenu() {
  showUserMenu.value = !showUserMenu.value
}

function toggleConversations() {
  showAllConversations.value = !showAllConversations.value
}

async function toggleProjectMenu() {
  showProjectMenu.value = !showProjectMenu.value
  if (showProjectMenu.value) {
    await nextTick()
    projectMenu.value?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus()
  }
}

function closeProjectMenu(returnFocus = false) {
  showProjectMenu.value = false
  if (returnFocus) void nextTick(() => projectTrigger.value?.focus())
}

async function openCreateProject() {
  closeProjectMenu()
  showCreateProject.value = true
  projectNameError.value = null
  await nextTick()
  projectNameInput.value?.focus()
}

function closeCreateProject(returnFocus = false) {
  showCreateProject.value = false
  newProjectName.value = ''
  projectNameError.value = null
  if (returnFocus) void nextTick(() => projectTrigger.value?.focus())
}

function submitProjectCreation() {
  const name = newProjectName.value.trim()
  if (!name) {
    projectNameError.value = 'Enter a project name.'
    return
  }
  if (name.length > 255) {
    projectNameError.value = 'Project names can have at most 255 characters.'
    return
  }
  projectNameError.value = null
  emit('createProject', name)
}

function onProjectMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && showCreateProject.value) {
    event.preventDefault()
    closeCreateProject(true)
    return
  }
  if (event.key !== 'Escape' || !showProjectMenu.value) return
  event.preventDefault()
  closeProjectMenu(true)
}

function onDocumentClick(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  if (
    projectMenu.value?.contains(target) ||
    projectCreateDialog.value?.contains(target) ||
    projectTrigger.value?.contains(target)
  ) return
  closeProjectMenu()
  closeCreateProject()
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
watch(() => props.currentProjectId, (next, previous) => {
  if (next !== previous && showCreateProject.value) closeCreateProject()
})

/** Max conversations shown before expand is needed. */
const COLLAPSED_CONVERSATION_LIMIT = 8

const visibleConversations = computed(() => {
  const all = props.conversations
  if (showAllConversations.value || all.length <= COLLAPSED_CONVERSATION_LIMIT) {
    return all
  }
  return all.slice(0, COLLAPSED_CONVERSATION_LIMIT)
})

const extraCount = computed(() => {
  if (showAllConversations.value) return 0
  return props.conversations.length - COLLAPSED_CONVERSATION_LIMIT
})
</script>

<template>
  <Sidebar collapsible="icon" class="z-50">
    <SidebarHeader
      class="h-14 shrink-0 flex-row items-center justify-start gap-2 border-b border-sidebar-border px-3 py-0 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
    >
      <span
        class="flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground"
        aria-hidden="true"
      >
        OS
      </span>
      <span class="text-base font-semibold tracking-tight group-data-[collapsible=icon]:hidden">
        OpenShip
      </span>
    </SidebarHeader>

    <div
      class="relative shrink-0 border-b border-sidebar-border px-3 py-2 group-data-[collapsible=icon]:hidden"
      @keydown="onProjectMenuKeydown"
    >
      <div class="text-xs text-sidebar-foreground/60">Project</div>
      <button
        ref="projectTrigger"
        type="button"
        data-testid="project-selector-trigger"
        aria-controls="project-selector-menu"
        aria-label="Select project"
        aria-haspopup="menu"
        :aria-expanded="showProjectMenu"
        class="flex w-full items-center gap-2 rounded-md text-left text-sm font-medium text-sidebar-foreground transition-colors hover:text-sidebar-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
        @click="toggleProjectMenu"
      >
        <Icon name="box" class="size-3 shrink-0" />
        <span class="min-w-0 flex-1 truncate">{{ currentProject?.name ?? 'No project available' }}</span>
        <Icon name="chevron-down" class="size-3 shrink-0 text-sidebar-foreground/60" />
      </button>
      <div
        v-if="showProjectMenu"
        ref="projectMenu"
        id="project-selector-menu"
        data-testid="project-selector-menu"
        role="menu"
        aria-label="Project choices"
        class="absolute left-2 right-2 top-full z-40 mt-1 rounded-lg border border-sidebar-border bg-popover p-1 text-popover-foreground shadow-lg"
      >
        <button
          v-for="project in props.projects"
          :key="project.id"
          type="button"
          role="menuitemradio"
          :aria-checked="project.id === currentProject?.id"
          :class="project.id === currentProject?.id ? 'text-primary' : 'text-popover-foreground'"
          class="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          @click="emit('selectProject', project.id); closeProjectMenu(true)"
        >
          <Icon name="box" class="size-3 shrink-0" />
          <span class="min-w-0 flex-1 truncate">{{ project.name }}</span>
          <Icon v-if="project.id === currentProject?.id" name="check" class="size-3 shrink-0" />
        </button>
        <p v-if="!props.projects.length" class="px-2 py-2 text-xs text-muted-foreground">No owned projects are available.</p>
        <div class="my-1 border-t border-border" />
        <button
          type="button"
          role="menuitem"
          data-testid="create-project-trigger"
          class="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          @click="openCreateProject"
        >
          <Icon name="plus" class="size-3 shrink-0" />
          <span>Create project</span>
        </button>
      </div>
      <div
        v-if="showCreateProject"
        ref="projectCreateDialog"
        role="dialog"
        aria-label="Create project"
        class="absolute left-2 right-2 top-full z-40 mt-1 grid gap-2 rounded-lg border border-sidebar-border bg-popover p-3 text-popover-foreground shadow-lg"
        @keydown="onProjectMenuKeydown"
      >
        <label for="new-project-name" class="text-sm font-medium">New project name</label>
        <input
          id="new-project-name"
          ref="projectNameInput"
          v-model="newProjectName"
          aria-label="New project name"
          maxlength="255"
          class="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.projectCreating"
          @keydown.enter.prevent="submitProjectCreation"
        />
        <p v-if="projectNameError || props.createProjectError" role="alert" class="text-xs text-destructive">
          {{ projectNameError ?? props.createProjectError }}
        </p>
        <div class="flex justify-end gap-2">
          <Button type="button" variant="ghost" size="sm" :disabled="props.projectCreating" @click="closeCreateProject(true)">Cancel</Button>
          <Button
            type="button"
            size="sm"
            aria-label="Create project"
            :disabled="props.projectCreating"
            @click="submitProjectCreation"
          >
            {{ props.projectCreating ? 'Creating…' : 'Create project' }}
          </Button>
        </div>
      </div>
    </div>

    <SidebarContent>
      <!-- New conversation control: immediately create, no title input. -->
      <div class="shrink-0 px-2 pt-1 group-data-[collapsible=icon]:px-0">
        <Button
          class="w-full justify-start"
          :disabled="props.busy"
          aria-label="New Conversation"
          @click="emit('createConversation', 'New Conversation')"
        >
          <Icon name="plus" />
          <span class="group-data-[collapsible=icon]:hidden">New Conversation</span>
        </Button>
      </div>

      <!-- Agent Workspace: conversation history -->
      <nav aria-label="Conversations" class="flex min-h-0 flex-1 flex-col">
        <!-- Primary workspace link (expanded mode only) -->
        <SidebarMenu class="mt-1 group-data-[collapsible=icon]:hidden">
          <SidebarMenuItem>
            <RouterLink
              :to="{ name: 'chat' }"
              class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 [&.router-link-exact-active]:bg-sidebar-accent [&.router-link-exact-active]:font-medium [&.router-link-exact-active]:text-sidebar-accent-foreground"
            >
              <Icon name="bolt" />
              <span>Agent Workspace</span>
            </RouterLink>
          </SidebarMenuItem>
        </SidebarMenu>

        <!-- Conversation history (expanded mode only) -->
        <div class="mt-1 px-2 group-data-[collapsible=icon]:px-0">
          <div
            v-if="props.busy && props.conversations.length === 0"
            class="px-3 py-2 text-xs text-sidebar-foreground/60"
          >
            Loading conversations...
          </div>
          <div
            v-else-if="props.conversations.length === 0"
            class="px-3 py-2 text-xs text-sidebar-foreground/60"
          >
            No conversations yet
          </div>
          <template v-else>
            <SidebarMenu class="group-data-[collapsible=icon]:hidden">
              <SidebarMenuItem
                v-for="conv in visibleConversations"
                :key="conv.id"
              >
                <SidebarMenuButton
                  :is-active="conv.id === props.currentConversationId"
                  :disabled="props.busy"
                  class="text-[13px]"
                  :title="conv.title"
                  :aria-label="`Open conversation ${conv.title}`"
                  @click="emit('selectConversation', conv.id)"
                >
                  <Icon name="comments" />
                  <span
                    data-testid="conversation-title"
                    :class="conv.id === props.currentConversationId ? 'font-semibold text-primary' : ''"
                  >{{ conv.title }}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>

            <!-- Expand / collapse toggle for conversation list -->
            <template v-if="extraCount > 0">
              <Button
                variant="ghost"
                size="sm"
                class="w-full text-xs text-sidebar-foreground/60 hover:text-sidebar-foreground group-data-[collapsible=icon]:hidden"
                @click="toggleConversations"
              >
                <Icon :name="showAllConversations ? 'chevron-up' : 'chevron-down'" />
                <span>
                  {{ showAllConversations ? 'Show less' : `Show ${extraCount} more` }}
                </span>
              </Button>
            </template>
          </template>
        </div>
      </nav>
    </SidebarContent>

    <!-- Footer: icon-mode expand button (only when sidebar is collapsed) -->
    <SidebarFooter
      v-if="state === 'collapsed'"
      class="shrink-0"
    >
      <Button
        variant="ghost"
        size="icon"
        class="w-full"
        aria-label="Expand sidebar"
        @click="expandSidebar"
      >
        <Icon name="bars" />
        <span class="sr-only">Expand sidebar</span>
      </Button>
    </SidebarFooter>

    <!-- Footer: expanded-mode user profile + bottom navigation -->
    <SidebarFooter
      v-if="state !== 'collapsed'"
      class="shrink-0"
    >
      <!-- Bottom navigation: roadmap features -->
      <SidebarMenu class="mb-2">
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="sitemap" />
            <span>Workflows</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="wrench" />
            <span>Tools</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="book" />
            <span>Skills</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="box" />
            <span>Artifacts</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <RouterLink
            to="/connectors"
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="link" />
            <span>Connectors</span>
          </RouterLink>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="server" />
            <span>Resources</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="code" />
            <span>Git</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="cloud-upload" />
            <span>Terraform</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="bell" />
            <span>Alarms</span>
          </button>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <RouterLink
            v-if="props.currentProjectId"
            :to="{ name: 'project-settings', params: { projectId: props.currentProjectId } }"
            aria-label="Project Settings"
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="cog" />
            <span>Settings</span>
          </RouterLink>
          <RouterLink
            v-else
            to="/settings/global"
            aria-label="Global Settings"
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0"
          >
            <Icon name="cog" />
            <span>Settings</span>
          </RouterLink>
        </SidebarMenuItem>
      </SidebarMenu>

      <!-- User profile with dropdown -->
      <div class="relative border-t border-sidebar-border">
        <Button
          variant="ghost"
          size="sm"
          class="w-full justify-between"
          @click="toggleUserMenu"
        >
          <span class="flex items-center gap-2">
            <Icon name="user" class="size-4 shrink-0" />
            <span class="truncate">{{ username }}</span>
          </span>
          <Icon
            :name="showUserMenu ? 'chevron-up' : 'chevron-down'"
            class="size-3 shrink-0 text-sidebar-foreground/60"
          />
        </Button>
        <div
          v-if="showUserMenu"
          class="absolute bottom-full left-0 right-0 mb-1 overflow-hidden rounded-md border border-border bg-card shadow-lg"
        >
          <div class="px-3 py-2 text-xs text-muted-foreground border-b border-border">
            Signed in as {{ username }}
          </div>
          <Button
            variant="ghost"
            size="sm"
            class="w-full justify-start gap-2"
          >
            <Icon name="user" />
            Profile
          </Button>
          <Button
            variant="ghost"
            size="sm"
            class="w-full justify-start gap-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
            @click="emit('signOut'); showUserMenu = false"
          >
            <Icon name="sign-out" />
            Sign Out
          </Button>
        </div>
      </div>
    </SidebarFooter>
  </Sidebar>
</template>
