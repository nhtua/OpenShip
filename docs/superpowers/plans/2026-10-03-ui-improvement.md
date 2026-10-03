# UI Improvement Plan

**Date:** 2026-10-03
**Scope:** Phase 1 chat UI improvements to match mockup design
**Icon Pack:** Primeicons Vue3

## Overview

Transform the basic chat interface into a polished, production-ready UI matching the mockup's GitHub dark theme design. All icons use Primeicons Vue3.

## Step 1: Install Primeicons

**Command:**
```bash
cd apps/web
pnpm add primeicons
```

**Expected:** Package installed, added to `package.json` dependencies.

## Step 2: Update Theme Colors

**File:** `apps/web/src/assets/index.css`

Replace the current Tailwind dark theme variables with GitHub dark theme colors:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@import '@primeuix/themes/primeicons/primeicons.css';

:root {
  --background: 222 47% 11%;    /* #0d1117 */
  --foreground: 210 40% 96%;    /* #e6edf3 */
  --card: 222 47% 13%;          /* #161b22 */
  --card-foreground: 210 40% 96%;
  --popover: 222 47% 13%;
  --popover-foreground: 210 40% 96%;
  --primary: 205 92% 52%;       /* #58a6ff */
  --primary-foreground: 210 40% 98%;
  --secondary: 222 47% 15%;     /* #21262d */
  --secondary-foreground: 210 40% 96%;
  --muted: 222 47% 15%;
  --muted-foreground: 210 40% 64%; /* #8b949e */
  --accent: 222 47% 15%;
  --accent-foreground: 210 40% 96%;
  --destructive: 0 72% 51%;
  --destructive-foreground: 210 40% 98%;
  --border: 210 40% 16%;        /* #30363d */
  --input: 210 40% 16%;
  --ring: 205 92% 52%;
  --radius: 0.5rem;
  --sidebar-background: 222 47% 13%;  /* #161b22 */
  --sidebar-foreground: 210 40% 96%;
  --sidebar-primary: 205 92% 52%;
  --sidebar-primary-foreground: 210 40% 98%;
  --sidebar-accent: 222 47% 15%;
  --sidebar-accent-foreground: 210 40% 96%;
  --sidebar-border: 210 40% 16%;
  --sidebar-ring: 205 92% 52%;
}
```

## Step 3: Update ChatView Layout

**File:** `apps/web/src/views/ChatView.vue`

Replace the entire template with:

```vue
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
```

## Step 4: Update ChatStream Component

**File:** `apps/web/src/components/chat/ChatStream.vue`

Replace with:

```vue
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
```

## Step 5: Update UserMessage Component

**File:** `apps/web/src/components/chat/UserMessage.vue`

Replace with:

```vue
<script setup lang="ts">
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
}>()
</script>

<template>
  <div class="flex justify-end">
    <div class="max-w-2xl bg-[#238636]/20 border border-[#238636]/30 rounded-lg px-4 py-3">
      <p class="text-sm text-[#e6edf3]">{{ message.content }}</p>
    </div>
  </div>
</template>
```

## Step 6: Update AgentMessage Component

**File:** `apps/web/src/components/chat/AgentMessage.vue`

Replace with:

```vue
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
```

## Step 7: Update ChatInput Component

**File:** `apps/web/src/components/chat/ChatInput.vue`

Replace with:

```vue
<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled: boolean
}>()

const emit = defineEmits<{
  send: [content: string]
}>()

const content = ref('')

function handleSend() {
  const text = content.value.trim()
  if (text && !props.disabled) {
    emit('send', text)
    content.value = ''
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="border-t border-[#30363d] p-4 bg-[#161b22]">
    <div class="flex gap-3">
      <textarea
        v-model="content"
        :disabled="disabled"
        placeholder="Adjust parameters, ask questions, or approve..."
        class="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:ring-2 focus:ring-[#58a6ff] resize-none"
        rows="1"
        @keydown="handleKeydown"
      ></textarea>
      <button
        @click="handleSend"
        :disabled="disabled || !content.trim()"
        class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <i class="pi pi-send"></i>
      </button>
    </div>
  </div>
</template>
```

## Step 8: Update Login/Register Pages Theme

**Files:**
- `apps/web/src/views/LoginView.vue`
- `apps/web/src/views/RegisterView.vue`

Update the background and card styling to use the GitHub dark theme colors:
- Background: `bg-[#0d1117]`
- Card: `bg-[#161b22] border border-[#30363d]`
- Input: `bg-[#0d1117] border border-[#30363d]`
- Button: `bg-[#238636]` (primary), `border border-[#30363d]` (secondary)
- Links: `text-[#58a6ff]`

Use Primeicons for icons: `<i class="pi pi-user"></i>`, `<i class="pi pi-lock"></i>`, `<i class="pi pi-envelope"></i>`, `<i class="pi pi-eye"></i>`, `<i class="pi pi-eye-slash"></i>`

## Step 9: Update Layout Wrapper

**File:** `apps/web/src/layouts/AppLayout.vue` (if it exists) or `apps/web/src/App.vue`

Ensure the root layout uses the dark theme colors and has full-height flex layout.

## Step 10: Verify

1. Run `pnpm dev` in `apps/web`
2. Test login page styling
3. Test register page styling
4. Test chat interface:
   - Create new conversation
   - Send a message
   - Verify user/assistant message styling
   - Verify streaming animation
5. Verify Primeicons display correctly on all buttons and navigation items
