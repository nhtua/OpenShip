<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useArtifactsStore } from '@/stores/artifacts'
import { useConnectorsStore } from '@/stores/connectors'
import AppLayout from '@/layouts/AppLayout.vue'
import ChatStream from '@/components/chat/ChatStream.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import RunCard from '@/components/chat/RunCard.vue'
import ConversationFiles from '@/components/chat/ConversationFiles.vue'
import Icon from '@/components/common/Icon.vue'
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const chat = useChatStore()
const artifacts = useArtifactsStore()
const connectors = useConnectorsStore()

// View-local async state (plan Task 5.3): released in finally blocks.
const isLoadingConversations = ref(false)
const isCreatingConversation = ref(false)
const isLoadingConversation = ref(false)
const isToolSubmitting = ref(false)
const conversationFilesOpen = ref(false)
// Driven from the resulting store error, never inferred from an empty list.
const historyLoadFailed = ref(false)
const routeReady = ref(false)
let disposed = false
let routeLoadGeneration = 0
let routeLoad: Promise<void> = Promise.resolve()

chat.deselectConversation()

const hasActiveRun = computed(() => {
  const conversationId = chat.currentConversation?.id
  const status = conversationId ? chat.runCards.get(conversationId)?.status : null
  return status ? ['queued', 'running', 'paused'].includes(status) : false
})

const busy = computed(
  () =>
    !routeReady.value ||
    chat.isStreaming ||
    isCreatingConversation.value ||
    isLoadingConversation.value,
)

const title = computed(
  () => chat.currentConversation?.title || 'New Conversation',
)
const projectConversations = computed(() => chat.conversations.filter(
  (conversation) => !conversation.project_id || conversation.project_id === connectors.currentProjectId,
))

const conversationOperations = computed(() => {
  const conversationId = chat.currentConversation?.id
  return conversationId
    ? [...(chat.toolOperationsByConversation.get(conversationId)?.values() ?? [])]
    : []
})

const conversationFiles = computed(() => {
  const conversationId = chat.currentConversation?.id
  return conversationId ? artifacts.byConversation.get(conversationId) ?? [] : []
})
const conversationFileCount = computed(() =>
  conversationWorkspace.value?.quota.committed_count ?? conversationFiles.value.length,
)
const conversationWorkspace = computed(() => {
  const conversationId = chat.currentConversation?.id
  return conversationId ? artifacts.workspaceByConversation.get(conversationId) ?? null : null
})
const selectedArtifactId = computed(() => {
  const conversationId = chat.currentConversation?.id
  return conversationId ? artifacts.selectedByConversation.get(conversationId) ?? null : null
})
const selectedFileRead = computed(() =>
  selectedArtifactId.value ? artifacts.contentByArtifact.get(selectedArtifactId.value) ?? null : null,
)

watch(() => chat.currentConversation?.id, () => {
  conversationFilesOpen.value = false
})

const pendingSubmissionId = computed(() => {
  const conversationId = chat.currentConversation?.id
  if (!conversationId) return null
  return [...chat.pendingRequests.entries()].find(
    ([, pending]) => pending.conversation_id === conversationId,
  )?.[0] ?? [...chat.pendingToolRequests.entries()].find(
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

async function loadConversationFromRoute() {
  const generation = ++routeLoadGeneration
  chat.deselectConversation()
  conversationFilesOpen.value = false
  const id = route.name === 'conversation' && typeof route.params.conversationId === 'string'
    ? route.params.conversationId
    : null
  isLoadingConversation.value = Boolean(id)
  if (!id) return
  // Router params are decoded; separators must never become API path segments.
  if (!/^[A-Za-z0-9_-]+$/.test(id)) {
    chat.error = 'Invalid conversation link.'
    isLoadingConversation.value = false
    return
  }
  try {
    await chat.loadConversation(id)
    if (generation !== routeLoadGeneration) return
    const projectId = chat.currentConversation?.project_id
    if (projectId && projectId !== connectors.currentProjectId) {
      await connectors.selectProject(projectId)
    }
    if (chat.currentConversation?.id === id) await artifacts.loadConversation(id)
  } finally {
    if (generation === routeLoadGeneration) isLoadingConversation.value = false
  }
}

watch(() => [route.name, route.params.conversationId], () => {
  if (routeReady.value) routeLoad = loadConversationFromRoute()
})

async function createAndOpenConversation(newTitle: string): Promise<string | null> {
  const origin = route.fullPath
  const generation = routeLoadGeneration
  isCreatingConversation.value = true
  chat.error = null
  try {
    const conv = await chat.createConversation(newTitle, connectors.currentProjectId)
    // A pending creation must not navigate away from a newer browser location.
    if (!conv || disposed || route.fullPath !== origin || generation !== routeLoadGeneration) return null
    await router.push({ name: 'conversation', params: { conversationId: conv.id } })
    await nextTick()
    await routeLoad
    return chat.currentConversation?.id === conv.id ? conv.id : null
  } finally {
    isCreatingConversation.value = false
  }
}

async function handleCreateConversation(newTitle: string) {
  if (busy.value) return
  await createAndOpenConversation(newTitle)
}

async function handleSelectConversation(id: string) {
  if (busy.value || id === chat.currentConversation?.id) return
  const generation = routeLoadGeneration
  // Lazy route resolution can finish after another composer interaction.
  isLoadingConversation.value = true
  try {
    await router.push({ name: 'conversation', params: { conversationId: id } })
  } finally {
    if (generation === routeLoadGeneration) isLoadingConversation.value = false
  }
}

async function handleSend(content: string, options?: { enableTools: boolean; connectorSelections?: Partial<Record<'brave' | 'tavily', string>> }) {
  if (busy.value) return
  chat.error = null

  const conversationId = chat.currentConversation?.id
    ?? await createAndOpenConversation('New Conversation')
  if (conversationId) await chat.sendMessage(content, conversationId, options)
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
  if (!requestId) return
  if (chat.pendingRequests.has(requestId)) await chat.retrySubmission(requestId)
  else await chat.retryToolSubmission(requestId)
}

async function handleSearch(command: { capability: { id: string; version: string; manifest_digest: string }; arguments: { query: string; max_results: number }; connector_selections?: Partial<Record<'brave' | 'tavily', string>> }) {
  if (busy.value || hasActiveRun.value) return
  isToolSubmitting.value = true
  try {
    const conversationId = chat.currentConversation?.id
      ?? await createAndOpenConversation('New Conversation')
    if (conversationId) await chat.submitToolSearch(conversationId, command)
  } finally {
    isToolSubmitting.value = false
  }
}

function handleDownloadArtifact(artifactId: string, name: string) {
  void artifacts.download(artifactId, name)
}

function handleSelectFile(conversationId: string, artifactId: string) {
  void artifacts.selectFile(conversationId, artifactId)
}

function handleReadMoreFile(conversationId: string, artifactId: string) {
  void artifacts.readMore(conversationId, artifactId)
}

function handleDeleteFile(conversationId: string, artifactId: string) {
  const file = artifacts.byConversation.get(conversationId)?.find((item) => item.id === artifactId)
  if (!file || !window.confirm(`Delete saved file “${file.name}”?`)) return
  void artifacts.deleteArtifact(conversationId, artifactId)
}

function toggleConversationFiles() {
  conversationFilesOpen.value = !conversationFilesOpen.value
}

function handleCancelOperation(runId: string) {
  if (activeRunId.value === runId) void chat.cancelRun(runId)
}

// The composer's selector names a profile; the store decides what the next
// turn uses. A run that already exists keeps its own provider either way.
function handleSelectProfile(profileId: string | null, fallbackIds: string[]) {
  chat.selectProfile(profileId, fallbackIds)
}

async function handleSignOut() {
  auth.logout()
  chat.clearMessages()
  artifacts.clear()
  connectors.clear()
  chat.conversations = []
  chat.error = null
  historyLoadFailed.value = false
  conversationFilesOpen.value = false
  await router.replace({ name: 'login' })
}

async function handleSelectProject(projectId: string) {
  if (busy.value || projectId === connectors.currentProjectId) return
  await connectors.selectProject(projectId)
  if (
    chat.currentConversation?.project_id &&
    chat.currentConversation.project_id !== projectId
  ) {
    await router.replace({ name: 'chat' })
  }
}

onMounted(async () => {
  // What this installation offers is part of being able to answer at all, so
  // it is loaded when the page is. A missing catalog is stated by the store
  // rather than guessed at here, and the next turn simply names no profile.
  await chat.loadModelProfiles()
  await connectors.initialize()
  await loadHistory()
  if (disposed) return
  routeReady.value = true
  if (route.name === 'conversation') {
    routeLoad = loadConversationFromRoute()
    await routeLoad
  }
})

onBeforeUnmount(() => {
  disposed = true
  routeReady.value = false
  routeLoadGeneration += 1
  chat.deselectConversation()
})
</script>

<template>
  <AppLayout
    :title="title"
    :is-streaming="chat.isStreaming && !isToolSubmitting"
    :username="auth.user?.username ?? null"
    :conversations="projectConversations"
    :current-conversation-id="chat.currentConversation?.id ?? null"
    :show-conversation-files="Boolean(chat.currentConversation)"
    :conversation-files-open="conversationFilesOpen"
    :conversation-file-count="conversationFileCount"
    :busy="busy"
    :projects="connectors.projects"
    :current-project-id="connectors.currentProjectId"
    @create-conversation="handleCreateConversation"
    @select-conversation="handleSelectConversation"
    @select-project="handleSelectProject"
    @sign-out="handleSignOut"
    @toggle-conversation-files="toggleConversationFiles"
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
        <RunCard
          :card="chat.runCards.get(chat.currentConversation.id)!"
          :has-actions="Boolean(pendingSubmissionId || (activeRunId && chat.runCards.get(chat.currentConversation.id)!.kind !== 'tool'))"
        >
          <Button
            v-if="pendingSubmissionId"
            variant="ghost"
            size="icon-sm"
            aria-label="Retry submission"
            title="Retry submission"
            @click="handleRetrySubmission"
          >
            <Icon name="refresh" />
          </Button>
          <Button
            v-if="activeRunId && chat.runCards.get(chat.currentConversation.id)!.kind !== 'tool'"
            size="icon-sm"
            variant="ghost"
            class="text-destructive hover:text-destructive"
            aria-label="Cancel run"
            title="Cancel run"
            @click="handleCancelRun"
          >
            <Icon name="times" />
          </Button>
        </RunCard>
      </div>
      <!-- The stream/composer components own their data-testid hooks. -->
      <ChatStream
        :messages="chat.messages"
        :operations="conversationOperations"
        :is-streaming="chat.isStreaming"
        @download="handleDownloadArtifact"
        @cancel="handleCancelOperation"
      />
      <ChatInput
        :disabled="busy"
        :selection-disabled="chat.isSubmitting"
        :profiles="chat.modelProfiles?.profiles ?? []"
        :default-profile-id="chat.modelProfiles?.default_profile_id ?? null"
        :selected-profile-id="chat.selectedProfileId"
        :fallback-ids="chat.selectedFallbackIds"
        :catalog-error="chat.profileError"
        :capabilities="connectors.capabilities"
        :connectors="connectors.connectors"
        :default-connector-ids="{
          brave: connectors.defaults.brave?.connector_id ?? undefined,
          tavily: connectors.defaults.tavily?.connector_id ?? undefined,
        }"
        :tool-submission-disabled="hasActiveRun"
        :submission-error="chat.error"
        @send="handleSend"
        @search="handleSearch"
        @select="handleSelectProfile"
      />
    </div>

    <Sheet
      :open="conversationFilesOpen"
      @update:open="conversationFilesOpen = $event"
    >
      <SheetContent
        id="conversation-files-panel"
        data-testid="conversation-files-panel"
        class="w-[min(28rem,92vw)] gap-0 p-0 sm:max-w-md"
      >
        <SheetHeader class="shrink-0 border-b border-border px-5 py-4 pr-14 text-left">
          <SheetTitle>Conversation files</SheetTitle>
          <SheetDescription>
            Files saved during this conversation.
          </SheetDescription>
        </SheetHeader>
        <ConversationFiles
          v-if="chat.currentConversation"
          :conversation-id="chat.currentConversation.id"
          :workspace="conversationWorkspace"
          :files="conversationFiles"
          :loading="artifacts.loadingConversation === chat.currentConversation.id"
          :loading-more="artifacts.loadingMoreConversation === chat.currentConversation.id"
          :error="artifacts.error"
          :next-cursor="artifacts.nextCursor.get(chat.currentConversation.id) ?? null"
          :selected-artifact-id="selectedArtifactId"
          :selected-content="selectedFileRead?.content ?? null"
          :selected-next-offset="selectedFileRead?.next_offset ?? null"
          :reading-artifact-id="artifacts.readingArtifactId"
          :deleting-artifact-id="artifacts.deletingArtifactId"
          @load-more="artifacts.loadMore"
          @select-file="handleSelectFile"
          @read-more="handleReadMoreFile"
          @download-file="handleDownloadArtifact"
          @delete-file="handleDeleteFile"
        />
      </SheetContent>
    </Sheet>
  </AppLayout>
</template>
