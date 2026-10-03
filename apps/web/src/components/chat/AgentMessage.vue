<script setup lang="ts">
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
  isStreaming: boolean
}>()
</script>

<template>
  <div class="flex gap-3">
    <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
      <span class="text-white text-xs font-bold">AI</span>
    </div>
    <div class="max-w-2xl space-y-3">
      <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
        <p class="text-sm text-[#e6edf3] whitespace-pre-wrap">{{ message.content }}</p>
      </div>
    </div>
  </div>
</template>