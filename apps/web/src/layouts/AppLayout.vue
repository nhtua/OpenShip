<script setup lang="ts">
import type { Conversation } from '@/types'
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
  }>(),
  {
    title: 'New Conversation',
    isStreaming: false,
    username: null,
    conversations: () => [],
    currentConversationId: null,
    busy: false,
  },
)

const emit = defineEmits<{
  createConversation: [title: string]
  selectConversation: [id: string]
  signOut: []
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
        @create-conversation="emit('createConversation', $event)"
        @select-conversation="emit('selectConversation', $event)"
        @sign-out="emit('signOut')"
      />
      <SidebarInset class="flex h-full min-h-0 min-w-0 flex-1 flex-col">
        <AppHeader
          :title="props.title"
          :is-streaming="props.isStreaming"
        />
        <div class="flex min-h-0 min-w-0 flex-1 flex-col">
          <slot />
        </div>
      </SidebarInset>
    </SidebarProvider>
  </div>
</template>
