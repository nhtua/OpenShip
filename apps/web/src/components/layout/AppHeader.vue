<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'
import { useThemeStore } from '@/stores/theme'
import { Button, Icon, Separator, SidebarTrigger } from '@/components/ui'

const props = withDefaults(
  defineProps<{
    title?: string
    isStreaming?: boolean
    showConversationFiles?: boolean
    conversationFilesOpen?: boolean
    conversationFileCount?: number
  }>(),
  {
    title: 'New Conversation',
    isStreaming: false,
    showConversationFiles: false,
    conversationFilesOpen: false,
    conversationFileCount: 0,
  },
)

const emit = defineEmits<{
  toggleConversationFiles: []
}>()

const theme = useThemeStore()

// Status is conveyed by text + badge color (never a pulsing dot), so
// reduced-motion users are not relying on animation.
const statusClass = computed(() =>
  props.isStreaming
    ? 'bg-success/15 text-success'
    : 'bg-secondary text-muted-foreground',
)
const dotClass = computed(() =>
  props.isStreaming ? 'bg-success' : 'bg-muted-foreground',
)
const conversationFilesLabel = computed(() => {
  const count = props.conversationFileCount
  const countLabel = count ? `, ${count} ${count === 1 ? 'file' : 'files'}` : ''
  return `${props.conversationFilesOpen ? 'Close' : 'Open'} conversation files${countLabel}`
})
</script>

<template>
  <header
    class="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4 md:gap-4 md:px-6"
  >
    <SidebarTrigger class="-ml-2" />
    <Separator orientation="vertical" class="h-6" />

    <div class="flex min-w-0 flex-1 items-center gap-3">
      <h1 class="truncate text-lg font-semibold leading-7">
        {{ props.title }}
      </h1>
      <span
        role="status"
        :class="
          cn(
            'inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium',
            statusClass,
          )
        "
      >
        <span
          class="h-1.5 w-1.5 rounded-full"
          :class="dotClass"
          aria-hidden="true"
        />
        {{ props.isStreaming ? 'Responding' : 'Idle' }}
      </span>
    </div>

    <div class="flex shrink-0 items-center gap-2">
      <Button
        v-if="props.showConversationFiles"
        variant="ghost"
        size="icon-sm"
        :aria-label="conversationFilesLabel"
        aria-controls="conversation-files-panel"
        :aria-expanded="props.conversationFilesOpen"
        title="Conversation files"
        data-testid="conversation-files-trigger"
        @click="emit('toggleConversationFiles')"
      >
        <Icon name="folder-open" />
        <span v-if="props.conversationFileCount" class="sr-only">{{ props.conversationFileCount }}</span>
      </Button>
      <Button
        variant="ghost"
        size="icon"
        :aria-label="
          theme.mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'
        "
        @click="theme.toggleTheme()"
      >
        <Icon :name="theme.mode === 'dark' ? 'sun' : 'moon'" />
      </Button>
    </div>
  </header>
</template>
