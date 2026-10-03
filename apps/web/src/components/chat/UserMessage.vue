<script setup lang="ts">
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
}>()
</script>

<template>
  <div class="message flex justify-end">
    <div
      class="max-w-[70%] rounded-2xl bg-primary px-4 py-3 text-sm text-primary-foreground shadow-sm"
    >
      <p class="whitespace-pre-wrap break-words">{{ message.content }}</p>
    </div>
  </div>
</template>
