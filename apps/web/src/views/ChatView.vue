<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import AppLayout from '@/layouts/AppLayout.vue'
import ChatStream from '@/components/chat/ChatStream.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
} from '@/components/ui'

const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()

// View-local async state (plan Task 5.3): released in finally blocks.
const isLoadingConversations = ref(false)
const isCreatingConversation = ref(false)
const isLoadingConversation = ref(false)
// Driven from the resulting store error, never inferred from an empty list.
const historyLoadFailed = ref(false)

const busy = computed(
  () =>
    chat.isStreaming ||
    isCreatingConversation.value ||
    isLoadingConversation.value,
)

const title = computed(
  () => chat.currentConversation?.title || 'New Conversation',
)

async function loadHistory() {
  isLoadingConversations.value = true
  historyLoadFailed.value = false
  chat.error = null
  await chat.getConversations()
  historyLoadFailed.value = chat.error !== null
  isLoadingConversations.value = false
}

async function loadConversationById(id: string) {
  isLoadingConversation.value = true
  try {
    await chat.loadConversation(id)
  } finally {
    isLoadingConversation.value = false
  }
}

async function handleCreateConversation(newTitle: string) {
  if (busy.value) return
  isCreatingConversation.value = true
  chat.error = null
  try {
    const conv = await chat.createConversation(newTitle)
    if (conv) {
      await loadConversationById(conv.id)
    }
  } finally {
    isCreatingConversation.value = false
  }
}

async function handleSelectConversation(id: string) {
  if (busy.value || id === chat.currentConversation?.id) return
  chat.error = null
  await loadConversationById(id)
}

async function handleSend(content: string) {
  if (busy.value) return
  chat.error = null
  await chat.sendMessage(content, chat.currentConversation?.id ?? null)
}

function dismissError() {
  chat.error = null
  historyLoadFailed.value = false
}

async function handleSignOut() {
  auth.logout()
  chat.clearMessages()
  chat.conversations = []
  chat.error = null
  historyLoadFailed.value = false
  await router.replace({ name: 'login' })
}

onMounted(() => {
  void loadHistory()
})
</script>

<template>
  <AppLayout
    :title="title"
    :is-streaming="chat.isStreaming"
    :username="auth.user?.username ?? null"
    :conversations="chat.conversations"
    :current-conversation-id="chat.currentConversation?.id ?? null"
    :busy="busy"
    @create-conversation="handleCreateConversation"
    @select-conversation="handleSelectConversation"
    @sign-out="handleSignOut"
  >
    <!-- Error banner lives outside the stream's scrolling content so a
         failure never disappears below the history. -->
    <div v-if="chat.error" class="shrink-0 px-4 pt-4 md:px-6">
      <Alert variant="destructive">
        <AlertTitle>Something went wrong</AlertTitle>
        <AlertDescription>{{ chat.error }}</AlertDescription>
        <div class="mt-3 flex gap-2">
          <Button
            v-if="historyLoadFailed"
            size="sm"
            @click="loadHistory"
          >
            Retry
          </Button>
          <Button size="sm" variant="outline" @click="dismissError">
            Dismiss
          </Button>
        </div>
      </Alert>
    </div>

    <div class="flex min-h-0 flex-1 flex-col" data-testid="chat-main">
      <!-- The stream/composer components own their data-testid hooks. -->
      <ChatStream :messages="chat.messages" :is-streaming="chat.isStreaming" />
      <ChatInput :disabled="busy" @send="handleSend" />
    </div>
  </AppLayout>
</template>
