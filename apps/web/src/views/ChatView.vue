<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import AppLayout from '@/layouts/AppLayout.vue'
import ChatStream from '@/components/chat/ChatStream.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import RunCard from '@/components/chat/RunCard.vue'
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

const pendingSubmissionId = computed(() => {
  const conversationId = chat.currentConversation?.id
  if (!conversationId) return null
  return [...chat.pendingRequests.entries()].find(
    ([, pending]) => pending.conversation_id === conversationId,
  )?.[0] ?? null
})

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

  // If no conversation is selected, create one first
  if (!chat.currentConversation) {
    isCreatingConversation.value = true
    try {
      const conv = await chat.createConversation('New Conversation')
      if (conv) {
        await loadConversationById(conv.id)
      }
    } finally {
      isCreatingConversation.value = false
    }
  }

  await chat.sendMessage(content, chat.currentConversation?.id ?? null)
}

// A run that has reached an end has nothing left to stop, and a run that has
// no id was never submitted: offering cancel for either would be a control
// that does nothing. The statuses here are the same ones the card still
// calls live, so the control and the card never disagree about a run.
const activeRunId = computed(() => {
  const conversationId = chat.currentConversation?.id
  if (!conversationId) return null
  const card = chat.runCards.get(conversationId)
  if (!card?.run_id) return null
  return ['queued', 'running', 'paused'].includes(card.status)
    ? card.run_id
    : null
})

async function handleCancelRun() {
  // Deliberately not guarded by `busy`: stopping a run is the thing a person
  // does while one is streaming. The store owns what a cancellation leaves
  // behind - the fence, the card, and the reason a turn has no answer.
  const runId = activeRunId.value
  if (!runId) return
  await chat.cancelRun(runId)
}

function dismissError() {
  chat.error = null
  historyLoadFailed.value = false
}

async function handleRetrySubmission() {
  const requestId = pendingSubmissionId.value
  if (requestId) await chat.retrySubmission(requestId)
}

// The composer's selector names a profile; the store decides what the next
// turn uses. A run that already exists keeps its own provider either way.
function handleSelectProfile(profileId: string | null, fallbackIds: string[]) {
  chat.selectProfile(profileId, fallbackIds)
}

async function handleSignOut() {
  auth.logout()
  chat.clearMessages()
  chat.conversations = []
  chat.error = null
  historyLoadFailed.value = false
  await router.replace({ name: 'login' })
}

onMounted(async () => {
  // What this installation offers is part of being able to answer at all, so
  // it is loaded when the page is. A missing catalog is stated by the store
  // rather than guessed at here, and the next turn simply names no profile.
  await chat.loadModelProfiles()
  await loadHistory()
  // Auto-create a conversation if none exist so the user can immediately start chatting
  if (!isLoadingConversations.value && chat.conversations.length === 0 && !historyLoadFailed.value) {
    isCreatingConversation.value = true
    try {
      const conv = await chat.createConversation('New Conversation')
      if (conv) {
        await loadConversationById(conv.id)
      }
    } finally {
      isCreatingConversation.value = false
    }
  }
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
      <!-- Run card shows durable progress for the current conversation.
           Cancel lives beside the run it names, and only while that run can
           still be stopped - a run that reached an end has nothing left to
           stop, and offering it would be a control that does nothing. -->
      <div v-if="chat.currentConversation && chat.runCards.has(chat.currentConversation.id)" class="shrink-0 px-4 pt-4">
        <RunCard :card="chat.runCards.get(chat.currentConversation.id)!" />
        <div v-if="pendingSubmissionId" class="shrink-0 px-4 pt-2">
          <Button size="sm" @click="handleRetrySubmission">
            Retry submission
          </Button>
        </div>
        <div v-if="activeRunId" class="shrink-0 px-4 pt-2">
          <Button
            size="sm"
            variant="destructive"
            @click="handleCancelRun"
          >
            Cancel run
          </Button>
        </div>
      </div>
      <!-- The stream/composer components own their data-testid hooks. -->
      <ChatStream :messages="chat.messages" :is-streaming="chat.isStreaming" />
      <ChatInput
        :disabled="busy"
        :selection-disabled="chat.isSubmitting"
        :profiles="chat.modelProfiles?.profiles ?? []"
        :default-profile-id="chat.modelProfiles?.default_profile_id ?? null"
        :selected-profile-id="chat.selectedProfileId"
        :fallback-ids="chat.selectedFallbackIds"
        :catalog-error="chat.profileError"
        @send="handleSend"
        @select="handleSelectProfile"
      />
    </div>
  </AppLayout>
</template>
