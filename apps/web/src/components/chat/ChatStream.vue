<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import type { ChatMessage } from '@/types'
import UserMessage from './UserMessage.vue'
import AgentMessage from './AgentMessage.vue'

const props = defineProps<{
  messages: ChatMessage[]
  isStreaming: boolean
}>()

const messagesContainer = ref<HTMLElement | null>(null)

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

watch(() => props.messages.length, scrollToBottom)
watch(() => props.isStreaming, scrollToBottom)
</script>

<template>
  <div
    ref="messagesContainer"
    class="flex-1 overflow-y-auto p-4 space-y-4"
  >
    <div v-if="messages.length === 0" class="flex h-full items-center justify-center text-center">
      <div class="space-y-2 text-muted-foreground">
        <p class="text-lg font-medium">Start a conversation</p>
        <p class="text-sm">Send a message to begin chatting with Agent.</p>
      </div>
    </div>

    <template v-for="msg in messages" :key="msg.id || msg.created_at">
      <UserMessage v-if="msg.role === 'user'" :message="msg" />
      <AgentMessage
        v-else
        :message="msg"
        :is-streaming="isStreaming && !msg.id"
      />
    </template>
  </div>
</template>
