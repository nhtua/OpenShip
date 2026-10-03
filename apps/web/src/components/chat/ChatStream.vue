<script setup lang="ts">
import type { ChatMessage } from '@/types'
import UserMessage from './UserMessage.vue'
import AgentMessage from './AgentMessage.vue'

defineProps<{
  messages: ChatMessage[]
  isStreaming: boolean
}>()
</script>

<template>
  <div class="flex-1 overflow-y-auto p-6 space-y-4">
    <template v-for="msg in messages" :key="msg.id || msg.created_at">
      <UserMessage v-if="msg.role === 'user'" :message="msg" />
      <AgentMessage v-else-if="msg.role === 'assistant'" :message="msg" :is-streaming="isStreaming && msg.id === ''" />
    </template>

    <div v-if="isStreaming && !messages.at(-1)?.id" class="flex gap-3">
      <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
        <span class="text-white text-xs font-bold">AI</span>
      </div>
      <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
        <div class="flex gap-1">
          <span class="w-2 h-2 bg-[#58a6ff] rounded-full animate-bounce" style="animation-delay: 0ms"></span>
          <span class="w-2 h-2 bg-[#58a6ff] rounded-full animate-bounce" style="animation-delay: 150ms"></span>
          <span class="w-2 h-2 bg-[#58a6ff] rounded-full animate-bounce" style="animation-delay: 300ms"></span>
        </div>
      </div>
    </div>

    <div v-if="messages.length === 0 && !isStreaming" class="flex-1 flex items-center justify-center text-[#8b949e]">
      <p>No messages yet. Start a conversation!</p>
    </div>
  </div>
</template>