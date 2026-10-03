<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import ChatStream from '@/components/chat/ChatStream.vue'
import ChatInput from '@/components/chat/ChatInput.vue'

const auth = useAuthStore()
const chat = useChatStore()
const newConvTitle = ref('')
const showNewConv = ref(false)

function handleLogout() {
  auth.logout()
}

async function handleSend(content: string) {
  const convId = chat.currentConversation?.id ?? null
  await chat.sendMessage(content, convId)
}

async function handleNewConversation() {
  const title = newConvTitle.value.trim() || 'New Conversation'
  const conv = await chat.createConversation(title)
  if (conv) {
    chat.loadConversation(conv.id)
    showNewConv.value = false
    newConvTitle.value = ''
  }
}

function selectConversation(convId: string) {
  chat.loadConversation(convId)
}

async function handleSelectExisting() {
  await chat.getConversations()
}

onMounted(() => {
  chat.getConversations()
  // If there's already a conversation in router state, load it
})
</script>

<template>
  <div class="flex h-[calc(100vh-5rem)] rounded-lg border bg-background">
    <!-- Conversation sidebar -->
    <aside class="w-64 flex-shrink-0 border-r bg-muted/30 p-4">
      <div class="space-y-4">
        <div>
          <h3 class="mb-2 text-sm font-semibold text-muted-foreground">
            Conversations
          </h3>
          <button
            @click="showNewConv = !showNewConv"
            class="flex w-full items-center gap-2 rounded-md border bg-background px-3 py-2 text-sm font-medium transition-colors hover:bg-muted"
          >
            <span class="text-lg leading-none">+</span> New Conversation
          </button>

          <div v-if="showNewConv" class="mt-2 space-y-2">
            <input
              v-model="newConvTitle"
              type="text"
              placeholder="Conversation name..."
              class="w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              @keyup.enter="handleNewConversation"
            />
            <div class="flex gap-2">
              <button
                @click="handleNewConversation"
                class="flex-1 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Create
              </button>
              <button
                @click="showNewConv = false"
                class="flex-1 rounded-md border bg-background px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>

        <div class="space-y-1">
          <button
            v-if="chat.conversations.length === 0"
            @click="handleSelectExisting"
            class="w-full text-left text-sm text-muted-foreground underline decoration-dotted hover:text-foreground"
          >
            Load existing conversations
          </button>
          <button
            v-for="conv in chat.conversations"
            :key="conv.id"
            @click="selectConversation(conv.id)"
            class="w-full rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-muted"
            :class="{
              'bg-muted font-medium': chat.currentConversation?.id === conv.id,
            }"
          >
            {{ conv.title }}
          </button>
        </div>
      </div>

      <div class="mt-auto pt-8">
        <button
          @click="handleLogout"
          class="inline-flex items-center justify-center rounded-md border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-muted"
        >
          Sign Out ({{ auth.user?.username }})
        </button>
      </div>
    </aside>

    <!-- Main chat area -->
    <main class="flex min-w-0 flex-1 flex-col">
      <!-- Error banner -->
      <div
        v-if="chat.error"
        class="border-b border-destructive/20 bg-destructive/10 px-4 py-2 text-sm text-destructive"
      >
        <div class="mx-auto flex items-center justify-between">
          <span>{{ chat.error }}</span>
          <button
            @click = "chat.error = null"
            class="ml-4 text-destructive/60 hover:text-destructive"
          >
            ✕
          </button>
        </div>
      </div>

      <ChatStream
        :messages="chat.messages"
        :is-streaming="chat.isStreaming"
      />

      <ChatInput
        :disabled="chat.isStreaming"
        @send="handleSend"
      />
    </main>
  </div>
</template>
