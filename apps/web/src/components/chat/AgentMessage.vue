<script setup lang="ts">
import type { ChatMessage } from '@/types'

withDefaults(
  defineProps<{
    message: ChatMessage
    isStreaming?: boolean
  }>(),
  {
    isStreaming: false,
  },
)
</script>

<template>
  <!-- Left-aligned assistant row: AI mark + card bubble (plan Task 6.6).
       The Agent label + indicator are single-sourced here (Task 6.5):
       thinking dots while empty, a cursor dot once chunks exist. -->
  <div class="message flex min-w-0 items-start gap-4" data-testid="bubble-agent">
    <div
      class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground"
      aria-hidden="true"
    >
      OS
    </div>
    <div class="min-w-0 max-w-full flex-1 space-y-3 md:max-w-2xl">
      <div
        v-if="isStreaming"
        class="flex items-center gap-2 text-xs font-medium text-muted-foreground"
        role="status"
      >
        <span>Agent</span>
        <span
          v-if="!message.content"
          class="flex gap-1"
          data-testid="thinking-dots"
          aria-hidden="true"
        >
          <span class="size-1.5 animate-bounce rounded-full bg-muted-foreground"></span>
          <span
            class="size-1.5 animate-bounce rounded-full bg-muted-foreground"
            style="animation-delay: 150ms"
          ></span>
          <span
            class="size-1.5 animate-bounce rounded-full bg-muted-foreground"
            style="animation-delay: 300ms"
          ></span>
        </span>
        <span
          v-else
          class="size-1.5 animate-pulse rounded-full bg-primary"
          aria-hidden="true"
        ></span>
      </div>
      <div
        v-if="message.content || !isStreaming"
        class="rounded-lg border border-border bg-card px-4 py-3"
      >
        <p
          class="min-w-0 max-w-full whitespace-pre-wrap text-sm [overflow-wrap:anywhere]"
        >
          {{ message.content }}
        </p>
      </div>
    </div>
  </div>
</template>
