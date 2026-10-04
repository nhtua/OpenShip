<script setup lang="ts">
import { ref } from 'vue'
import type { Conversation } from '@/types'
import { Button, Icon, Input } from '@/components/ui'
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

const props = withDefaults(
  defineProps<{
    conversations?: Conversation[]
    currentConversationId?: string | null
    busy?: boolean
  }>(),
  {
    conversations: () => [],
    currentConversationId: null,
    busy: false,
  },
)

const emit = defineEmits<{
  createConversation: [title: string]
  selectConversation: [id: string]
}>()

const { setOpen } = useSidebar()

const showCreateForm = ref(false)
const draftTitle = ref('')

function openCreateForm() {
  if (props.busy) return
  draftTitle.value = ''
  showCreateForm.value = true
}

function cancelCreateForm() {
  showCreateForm.value = false
  draftTitle.value = ''
}

function submitCreateForm() {
  if (props.busy || !showCreateForm.value) return
  const title = draftTitle.value.trim() || 'New Conversation'
  showCreateForm.value = false
  draftTitle.value = ''
  emit('createConversation', title)
}

function expandSidebar() {
  setOpen(true)
}
</script>

<template>
  <Sidebar collapsible="icon" class="z-50">
    <SidebarHeader
      class="h-14 shrink-0 items-center justify-start gap-2 border-b border-sidebar-border px-3 py-0 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0"
    >
      <span
        class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground"
        aria-hidden="true"
      >
        OS
      </span>
      <span class="truncate font-semibold group-data-[collapsible=icon]:hidden">
        OpenShip
      </span>
    </SidebarHeader>

    <SidebarContent>
      <!-- New conversation control + local create form (emits once). -->
      <div class="shrink-0 px-2 pt-1 group-data-[collapsible=icon]:px-0">
        <Button
          class="w-full justify-start"
          :disabled="props.busy || showCreateForm"
          aria-label="New Conversation"
          @click="openCreateForm"
        >
          <Icon name="plus" />
          <span class="group-data-[collapsible=icon]:hidden">New Conversation</span>
        </Button>

        <form
          v-if="showCreateForm"
          class="mt-2 flex flex-col gap-2 px-1"
          @submit.prevent="submitCreateForm"
        >
          <Input
            v-model="draftTitle"
            name="new-conversation-title"
            placeholder="Conversation title"
            aria-label="Conversation title"
            class="h-8 text-sm"
          />
          <div class="flex gap-2">
            <Button type="submit" size="sm" class="flex-1" :disabled="props.busy">
              Create
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              class="flex-1"
              @click="cancelCreateForm"
            >
              Cancel
            </Button>
          </div>
        </form>
      </div>

      <!-- Navigation + conversation history -->
      <nav aria-label="Conversations" class="flex min-h-0 flex-1 flex-col">
        <SidebarMenu class="group-data-[collapsible=icon]:hidden">
          <SidebarMenuItem>
            <RouterLink
              :to="{ name: 'chat' }"
              class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 [&.router-link-exact-active]:bg-sidebar-accent [&.router-link-exact-active]:font-medium [&.router-link-exact-active]:text-sidebar-accent-foreground"
            >
              <Icon name="comments" />
              <span>Agent Workspace</span>
            </RouterLink>
          </SidebarMenuItem>
        </SidebarMenu>

        <SidebarMenu class="mt-1 group-data-[collapsible=icon]:hidden">
          <li
            v-if="props.busy && props.conversations.length === 0"
            class="px-3 py-2 text-xs text-sidebar-foreground/60"
          >
            Loading conversations...
          </li>
          <li
            v-else-if="props.conversations.length === 0"
            class="px-3 py-2 text-xs text-sidebar-foreground/60"
          >
            No conversations yet
          </li>
          <template v-else>
            <SidebarMenuItem v-for="conv in props.conversations" :key="conv.id">
              <SidebarMenuButton
                :is-active="conv.id === props.currentConversationId"
                :disabled="props.busy"
                :title="conv.title"
                :aria-label="`Open conversation ${conv.title}`"
                @click="emit('selectConversation', conv.id)"
              >
                <Icon name="comments" />
                <span>{{ conv.title }}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </template>
        </SidebarMenu>
      </nav>
    </SidebarContent>

    <!-- Icon-only mode: labeled control restores the full list. -->
    <SidebarFooter class="hidden shrink-0 group-data-[collapsible=icon]:flex">
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
  </Sidebar>
</template>
