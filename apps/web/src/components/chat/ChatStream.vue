<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ChatMessage } from '@/types'
import AgentMessage from './AgentMessage.vue'
import UserMessage from './UserMessage.vue'

const props = defineProps<{
  messages: ChatMessage[]
  isStreaming: boolean
}>()

/**
 * Single scroll owner (plan Task 6.4). Auto-follows streaming content only
 * while the reader is within 48px of the bottom, or on the initial history
 * load; "Jump to latest" restores following. The container is the only
 * scrolling region — header/composer stay pinned by the Task 4 flex chain.
 */
const SCROLL_THRESHOLD = 48

const scrollRef = ref<HTMLElement | null>(null)
const nearBottom = ref(true)
const initialLoad = ref(true)

const standaloneStatus: ChatMessage = {
  id: '',
  role: 'assistant',
  content: '',
  conversation_id: null,
  created_at: '',
}

function scrollToBottom() {
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function onScroll() {
  const el = scrollRef.value
  if (!el) return
  nearBottom.value =
    el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_THRESHOLD
}

async function maybeFollowScroll() {
  if (nearBottom.value || initialLoad.value) {
    await nextTick()
    scrollToBottom()
    nearBottom.value = true
    initialLoad.value = false
  }
}

function jumpToLatest() {
  scrollToBottom()
  nearBottom.value = true
}

watch(
  () => [
    props.messages.length,
    props.messages.at(-1)?.content,
    props.isStreaming,
  ],
  () => {
    void maybeFollowScroll()
  },
)

onMounted(() => {
  scrollRef.value?.addEventListener('scroll', onScroll)
  void maybeFollowScroll()
})

onBeforeUnmount(() => {
  scrollRef.value?.removeEventListener('scroll', onScroll)
})
</script>

<template>
  <div class="relative min-h-0 flex-1" data-testid="stream-wrap">
    <div
      ref="scrollRef"
      class="h-full min-h-0 space-y-6 overflow-y-auto p-4 md:p-6"
      data-testid="stream"
    >
      <template v-for="msg in props.messages" :key="msg.id || msg.created_at">
        <UserMessage v-if="msg.role === 'user'" :message="msg" />
        <AgentMessage
          v-else-if="msg.role === 'assistant'"
          :message="msg"
          :is-streaming="
            isStreaming && msg.id === '' && msg === props.messages.at(-1)
          "
        />
      </template>

      <!-- One standalone status when streaming with no messages at all:
           never a second placeholder. -->
      <AgentMessage
        v-if="isStreaming && props.messages.length === 0"
        :message="standaloneStatus"
        :is-streaming="true"
      />

      <div
        v-if="props.messages.length === 0 && !isStreaming"
        class="flex min-h-full items-center justify-center"
        data-testid="empty-state"
      >
        <p class="text-sm text-muted-foreground">
          No messages yet. Start a conversation!
        </p>
      </div>
    </div>

    <button
      v-if="!nearBottom"
      type="button"
      class="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border border-border bg-card px-3 py-1.5 text-xs font-medium shadow-md transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      data-testid="jump-to-latest"
      @click="jumpToLatest"
    >
      Jump to latest
    </button>
  </div>
</template>
