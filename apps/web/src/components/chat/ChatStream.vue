<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ChatMessage, ToolOperationSummary } from '@/types'
import { buildConversationTimeline } from '@/lib/chatTimeline'
import AgentMessage from './AgentMessage.vue'
import UserMessage from './UserMessage.vue'
import ToolOperationCard from './ToolOperationCard.vue'

const props = withDefaults(defineProps<{
  messages: ChatMessage[]
  operations?: ToolOperationSummary[]
  isStreaming: boolean
}>(), { operations: () => [] })
const emit = defineEmits<{
  download: [artifactId: string, name: string]
  cancel: [runId: string]
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
const timeline = computed(() => buildConversationTimeline(props.messages, props.operations))
const awaitingAssistant = computed(() => {
  const latest = timeline.value.at(-1)
  return latest?.kind === 'message' && latest.message.role === 'user'
})

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
  () => [props.messages, props.operations, props.isStreaming],
  () => { void maybeFollowScroll() },
  { deep: true },
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
      <template v-for="entry in timeline" :key="`${entry.kind}:${entry.id}`">
        <UserMessage v-if="entry.kind === 'message' && entry.message.role === 'user'" :message="entry.message" />
        <AgentMessage
          v-else-if="entry.kind === 'message' && entry.message.role === 'assistant'"
          :message="entry.message"
          :is-streaming="
            isStreaming && entry.message.id === '' && entry.message === props.messages.at(-1)
          "
        />
        <ToolOperationCard
          v-else-if="entry.kind === 'tool'"
          :operation="entry.operation"
          @download="(artifactId, name) => emit('download', artifactId, name)"
          @cancel="emit('cancel', $event)"
          @layout-change="maybeFollowScroll"
        />
      </template>

      <!-- Typing indicator: agent is working (streaming but no assistant message yet) -->
      <div
        v-if="isStreaming && awaitingAssistant"
        class="message flex min-w-0 items-start gap-4"
        data-testid="typing-indicator"
      >
        <div
          class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground"
          aria-hidden="true"
        >
          OS
        </div>
        <div class="min-w-0 max-w-full flex-1 space-y-3 md:max-w-2xl">
          <div
            class="flex items-center gap-2 text-xs font-medium text-muted-foreground"
            role="status"
          >
            <span>Agent</span>
            <span
              class="flex gap-1"
              data-testid="thinking-dots"
              aria-hidden="true"
            >
              <span class="size-1.5 animate-bounce rounded-full bg-muted-foreground"></span>
              <span
                class="size-1.5 animate-bounce rounded-full bg-muted-foreground"
                style="animation-delay: 150ms"
              ></span>
              <span
                class="size-1.5 animate-bounce rounded-full bg-muted-foreground"
                style="animation-delay: 300ms"
              ></span>
            </span>
          </div>
        </div>
      </div>

      <!-- One standalone status when streaming with no messages at all:
           never a second placeholder. -->
      <AgentMessage
        v-if="isStreaming && timeline.length === 0"
        :message="standaloneStatus"
        :is-streaming="true"
      />

      <div
        v-if="timeline.length === 0 && !isStreaming"
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
