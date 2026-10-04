<script setup lang="ts">
import { computed, ref } from 'vue'
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
    username?: string | null
  }>(),
  {
    conversations: () => [],
    currentConversationId: null,
    busy: false,
    username: null,
  },
)

const emit = defineEmits<{
  createConversation: [title: string]
  selectConversation: [id: string]
  signOut: []
}>()

const { setOpen, state } = useSidebar()

const showCreateForm = ref(false)
const draftTitle = ref('')
const showUserMenu = ref(false)
const showAllConversations = ref(false)

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

function toggleUserMenu() {
  showUserMenu.value = !showUserMenu.value
}

function toggleConversations() {
  showAllConversations.value = !showAllConversations.value
}

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

      <!-- Top-level navigation -->
      <nav aria-label="Conversations" class="flex min-h-0 flex-1 flex-col">
        <SidebarMenu class="mt-1 group-data-[collapsible=icon]:hidden">
          <!-- Primary action -->
          <SidebarMenuItem>
            <RouterLink
              :to="{ name: 'chat' }"
              class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 [&.router-link-exact-active]:bg-sidebar-accent [&.router-link-exact-active]:font-medium [&.router-link-exact-active]:text-sidebar-accent-foreground"
            >
              <Icon name="comments" />
              <span>Agent Workspace</span>
            </RouterLink>
          </SidebarMenuItem>

          <!-- Placeholder / soon items -->
          <SidebarMenuItem>
            <button
              type="button"
              disabled
              class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 [&.router-link-exact-active]:bg-sidebar-accent [&.router-link-exact-active]:font-medium [&.router-link-exact-active]:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50"
            >
              <Icon name="info-circle" />
              <span>Settings</span>
            </button>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <button
              type="button"
              disabled
              class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 [&.router-link-exact-active]:bg-sidebar-accent [&.router-link-exact-active]:font-medium [&.router-link-exact-active]:text-sidebar-accent-foreground disabled:pointer-events-none disabled:opacity-50"
            >
              <Icon name="info-circle" />
              <span>Templates</span>
            </button>
          </SidebarMenuItem>
        </SidebarMenu>

        <!-- Conversation history -->
        <div class="mt-2 px-2 group-data-[collapsible=icon]:px-0">
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
            <SidebarMenu>
              <SidebarMenuItem
                v-for="conv in visibleConversations"
                :key="conv.id"
              >
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
            </SidebarMenu>

            <!-- Expand / collapse toggle -->
            <template v-if="extraCount > 0">
              <Button
                variant="ghost"
                size="sm"
                class="w-full text-xs text-sidebar-foreground/60 hover:text-sidebar-foreground"
                @click="toggleConversations"
              >
                <Icon :name="showAllConversations ? 'chevron-up' : 'chevron-down'" />
                <span class="group-data-[collapsible=icon]:hidden">
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

    <!-- Footer: expanded-mode user profile (only when sidebar is expanded) -->
    <SidebarFooter
      v-if="username && state !== 'collapsed'"
      class="shrink-0"
    >
      <div
        class="relative"
      >
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
            Signed in
          </div>
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
