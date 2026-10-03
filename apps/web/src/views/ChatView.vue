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

function handleExport() {
  // TODO: Export conversation
}

function handleStop() {
  // TODO: Stop current streaming
}

onMounted(() => {
  chat.getConversations()
})
</script>

<template>
  <div class="flex h-screen bg-[#0d1117] text-[#e6edf3]">
    <!-- Sidebar -->
    <aside class="w-64 bg-[#161b22] border-r border-[#30363d] flex flex-col">
      <!-- App Logo -->
      <div class="p-4 border-b border-[#30363d]">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center">
            <i class="pi pi-anchor text-white text-sm"></i>
          </div>
          <span class="font-semibold text-[#e6edf3]">OpenShip</span>
        </div>
      </div>

      <!-- Workspace Selector -->
      <div class="p-3">
        <select class="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-[#e6edf3] focus:outline-none focus:ring-2 focus:ring-[#58a6ff]">
          <option>web-platform</option>
          <option>staging-env</option>
          <option>prod-infra</option>
        </select>
      </div>

      <!-- Navigation -->
      <nav class="flex-1 p-2 space-y-1">
        <div class="flex items-center gap-2 px-3 py-2 rounded text-sm text-[#e6edf3] bg-[#0d1117]">
          <i class="pi pi-home text-[#58a6ff]"></i>
          Home
        </div>
        <div class="flex items-center gap-2 px-3 py-2 rounded text-sm text-[#8b949e] hover:bg-[#0d1117] hover:text-[#e6edf3] cursor-pointer">
          <i class="pi pi-plus"></i>
          New Project
        </div>
        <div class="flex items-center gap-2 px-3 py-2 rounded text-sm text-[#e6edf3] bg-[#0d1117]">
          <i class="pi pi-comments"></i>
          Agent Workspace
        </div>
        <div class="flex items-center gap-2 px-3 py-2 rounded text-sm text-[#8b949e] hover:bg-[#0d1117] hover:text-[#e6edf3] cursor-pointer">
          <i class="pi pi-sitemap"></i>
          Workflow Builder
        </div>
        <div class="flex items-center gap-2 px-3 py-2 rounded text-sm text-[#8b949e] hover:bg-[#0d1117] hover:text-[#e6edf3] cursor-pointer">
          <i class="pi pi-th-large"></i>
          Tool Registry
        </div>
        <div class="flex items-center gap-2 px-3 py-2 rounded text-sm text-[#8b949e] hover:bg-[#0d1117] hover:text-[#e6edf3] cursor-pointer">
          <i class="pi pi-link"></i>
          Connectors
        </div>
      </nav>

      <!-- Settings -->
      <div class="p-3 border-t border-[#30363d]">
        <button @click="handleLogout" class="w-full flex items-center gap-2 px-3 py-2 rounded text-sm text-[#8b949e] hover:bg-[#0d1117] hover:text-[#e6edf3]">
          <i class="pi pi-sign-out"></i>
          Sign Out
        </button>
      </div>
    </aside>

    <!-- Main Chat Area -->
    <main class="flex-1 flex flex-col">
      <!-- Conversation Header -->
      <div class="border-b border-[#30363d] px-6 py-4 bg-[#161b22]">
        <div class="flex items-center justify-between">
          <div>
            <h1 class="text-lg font-semibold text-[#e6edf3]">
              {{ chat.currentConversation?.title || 'Provision & Build' }}
            </h1>
            <p class="text-sm text-[#8b949e]">web-platform • Session #{{ chat.currentConversation?.id?.substring(0, 4) || '1042' }}</p>
          </div>
          <div class="flex items-center gap-3">
            <span class="flex items-center gap-2 text-sm text-green-400">
              <span class="w-2 h-2 bg-green-400 rounded-full animate-pulse"></span>
              Running
            </span>
            <button @click="handleExport" class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-[#e6edf3] px-3 py-1.5 rounded text-sm">
              <i class="pi pi-download mr-1"></i>Export
            </button>
            <button @click="handleStop" class="bg-[#da3633] hover:bg-[#f85149] text-white px-3 py-1.5 rounded text-sm">
              <i class="pi pi-stop mr-1"></i>Stop
            </button>
          </div>
        </div>
      </div>

      <!-- Chat Stream -->
      <ChatStream
        :messages="chat.messages"
        :is-streaming="chat.isStreaming"
      />

      <!-- Chat Input -->
      <ChatInput
        :disabled="chat.isStreaming"
        @send="handleSend"
      />
    </main>
  </div>
</template>