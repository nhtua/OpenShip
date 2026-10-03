<script setup lang="ts">
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
}>()

const isStreaming = defineModel<boolean>({ default: false })
</script>

<template>
  <div class="message flex justify-start">
    <div
      class="max-w-[70%] space-y-1 rounded-2xl border bg-card px-4 py-3 shadow-sm"
    >
      <div class="flex items-center gap-2 text-xs text-muted-foreground">
        <span class="font-medium">Agent</span>
        <span v-if="isStreaming" class="flex gap-1">
          <span
            class="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60"
            style="animation-delay: 0ms"
          ></span>
          <span
            class="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60"
            style="animation-delay: 150ms"
          ></span>
          <span
            class="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground/60"
            style="animation-delay: 300ms"
          ></span>
        </span>
      </div>
      <p class="whitespace-pre-wrap break-words text-sm">
        {{ message.content }}<span v-if="isStreaming" class="inline-block h-[1em] w-0.5 animate-pulse bg-foreground"></span>
      </p>
    </div>
  </div>
</template>
