<script setup lang="ts">
import {
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogRoot,
  DialogTitle,
} from 'reka-ui'
import { Button } from '@/components/ui'
import type { Conversation } from '@/types'

defineProps<{
  open: boolean
  conversations: Conversation[]
  loading: boolean
  restoringId: string | null
  error: string | null
}>()

const emit = defineEmits<{
  'update:open': [open: boolean]
  openConversation: [id: string]
  restoreConversation: [id: string]
  restoreProject: [id: string]
}>()
</script>

<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-50 bg-black/50" />
      <DialogContent
        class="fixed left-1/2 top-1/2 z-50 grid max-h-[85vh] w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 gap-4 overflow-hidden rounded-xl border border-border bg-background p-5 shadow-lg focus:outline-none"
        aria-label="Archived conversations"
      >
        <header class="grid gap-1 pr-8">
          <DialogTitle class="text-lg font-semibold">Archived conversations</DialogTitle>
          <DialogDescription class="text-sm text-muted-foreground">
            Archived history stays available. Restore a conversation before continuing work.
          </DialogDescription>
        </header>
        <DialogClose as-child>
          <button
            type="button"
            aria-label="Close archived conversations"
            class="absolute right-4 top-4 inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span aria-hidden="true">×</span>
          </button>
        </DialogClose>
        <p v-if="error" role="alert" class="rounded-md border border-destructive/40 p-3 text-sm text-destructive">
          {{ error }}
        </p>
        <p v-if="loading" role="status" class="text-sm text-muted-foreground">Loading archived conversations…</p>
        <p v-else-if="!conversations.length" class="py-6 text-center text-sm text-muted-foreground">
          No archived conversations.
        </p>
        <ul v-else class="grid min-h-0 gap-2 overflow-y-auto">
          <li
            v-for="conversation in conversations"
            :key="conversation.id"
            class="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
          >
            <button
              type="button"
              class="min-w-0 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              :aria-label="`Open archived conversation ${conversation.title}`"
              @click="emit('openConversation', conversation.id)"
            >
              <span class="block truncate text-sm font-medium">{{ conversation.title }}</span>
              <span class="block truncate text-xs text-muted-foreground">{{ conversation.project_name }}</span>
              <span v-if="conversation.project_archived_at" class="block text-xs text-amber-700 dark:text-amber-300">
                {{ conversation.project_name }} is archived
              </span>
            </button>
            <Button
              v-if="conversation.project_archived_at"
              type="button"
              variant="outline"
              :disabled="restoringId === conversation.project_id"
              :aria-label="`Restore project ${conversation.project_name}`"
              @click="emit('restoreProject', conversation.project_id ?? '')"
            >
              {{ restoringId === conversation.project_id ? 'Restoring…' : 'Restore project' }}
            </Button>
            <Button
              v-else
              type="button"
              variant="outline"
              :disabled="restoringId === conversation.id"
              :aria-label="`Restore conversation ${conversation.title}`"
              @click="emit('restoreConversation', conversation.id)"
            >
              {{ restoringId === conversation.id ? 'Restoring…' : 'Restore' }}
            </Button>
          </li>
        </ul>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
