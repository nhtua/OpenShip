<script setup lang="ts">
import type { Conversation, ProjectSummary } from '@/types'
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import { SidebarInset, SidebarProvider } from '@/components/ui'

/**
 * Authenticated responsive shell (plan Task 4.3). Presentational only: no
 * API requests here — all data arrives via props, all actions via emits.
 *
 * Geometry: h-dvh overflow-hidden root; SidebarInset is the single <main>
 * landmark; header/footer never shrink; the default slot is the flex
 * scroll region owned by the chat view (min-h-0 chain).
 */
const props = withDefaults(
  defineProps<{
    title?: string
    isStreaming?: boolean
    username?: string | null
    conversations?: Conversation[]
    currentConversationId?: string | null
    busy?: boolean
    showConversationFiles?: boolean
    conversationFilesOpen?: boolean
    conversationFileCount?: number
    projects?: ProjectSummary[]
    currentProjectId?: string | null
    createProjectError?: string | null
    projectCreating?: boolean
  }>(),
  {
    title: 'New Conversation',
    isStreaming: false,
    username: null,
    conversations: () => [],
    currentConversationId: null,
    busy: false,
    showConversationFiles: false,
    conversationFilesOpen: false,
    conversationFileCount: 0,
    projects: () => [],
    currentProjectId: null,
    createProjectError: null,
    projectCreating: false,
  },
)

const emit = defineEmits<{
  createConversation: [title: string]
  selectConversation: [id: string]
  archiveConversation: [id: string]
  showArchivedConversations: []
  signOut: []
  toggleConversationFiles: []
  selectProject: [id: string]
  createProject: [name: string]
}>()
</script>

<template>
  <div class="h-dvh min-h-0 w-full overflow-hidden bg-background text-foreground">
    <SidebarProvider default-open class="h-full min-h-0">
      <AppSidebar
        :conversations="props.conversations"
        :current-conversation-id="props.currentConversationId"
        :busy="props.busy"
        :username="props.username"
        :projects="props.projects"
        :current-project-id="props.currentProjectId"
        :create-project-error="props.createProjectError"
        :project-creating="props.projectCreating"
        @create-conversation="emit('createConversation', $event)"
        @select-conversation="emit('selectConversation', $event)"
        @archive-conversation="emit('archiveConversation', $event)"
        @show-archived-conversations="emit('showArchivedConversations')"
        @sign-out="emit('signOut')"
        @select-project="emit('selectProject', $event)"
        @create-project="emit('createProject', $event)"
      />
      <SidebarInset class="flex h-full min-h-0 min-w-0 flex-1 flex-col">
        <AppHeader
          :title="props.title"
          :is-streaming="props.isStreaming"
          :show-conversation-files="props.showConversationFiles"
          :conversation-files-open="props.conversationFilesOpen"
          :conversation-file-count="props.conversationFileCount"
          @toggle-conversation-files="emit('toggleConversationFiles')"
        />
        <div class="flex min-h-0 min-w-0 flex-1 flex-col">
          <slot />
        </div>
      </SidebarInset>
    </SidebarProvider>
  </div>
</template>
