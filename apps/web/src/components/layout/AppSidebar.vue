<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Conversation } from '@/types'
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

const showUserMenu = ref(false)
const showAllConversations = ref(false)

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

    <!-- Project indicator (Phase 2: single default project) -->
    <div class="shrink-0 border-b border-sidebar-border px-3 py-2 group-data-[collapsible=icon]:hidden">
      <div class="text-xs text-sidebar-foreground/60">Project</div>
      <div class="flex items-center gap-1 text-sm font-medium text-sidebar-foreground">
        <Icon name="bolt" class="size-3" />
        Personal Workspace
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
                  :title="conv.title"
                  :aria-label="`Open conversation ${conv.title}`"
                  @click="emit('selectConversation', conv.id)"
                >
                  <Icon name="comments" />
                  <span>{{ conv.title }}</span>
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
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="link" />
            <span>Connectors</span>
          </button>
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
          <button
            type="button"
            disabled
            class="flex w-full items-center gap-3 rounded-md border-1 border-transparent px-3 py-2 text-sm outline-hidden transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring [&>i]:shrink-0 disabled:pointer-events-none disabled:opacity-50"
          >
            <Icon name="cog" />
            <span>Settings</span>
          </button>
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
