<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ChatMessage, Run, TaskCardState, ToolOperationSummary } from '@/types'
import { buildConversationTimeline } from '@/lib/chatTimeline'
import AgentMessage from './AgentMessage.vue'
import UserMessage from './UserMessage.vue'
import ToolOperationCard from './ToolOperationCard.vue'
import TaskTimelineEntry from './TaskTimelineEntry.vue'

const props = withDefaults(defineProps<{
  messages: ChatMessage[]
  operations?: ToolOperationSummary[]
  tasks?: TaskCardState[]
  activeTaskRunId?: string | null
  isStreaming: boolean
  canResume?: boolean
  readOnly?: boolean
}>(), { operations: () => [], tasks: () => [], canResume: false, readOnly: false })
const emit = defineEmits<{
  download: [artifactId: string, name: string]
  cancel: [runId: string]
  resumed: [run: Run]
  respond: [runId: string, action: string, answer: string | null]
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
const entries = computed(() => {
  const result: Array<(typeof timeline.value)[number] | { kind: 'task'; id: string; card: TaskCardState }> = [...timeline.value]
  for (const card of props.tasks) {
    if (!card.planRevisions.length && !card.pendingDecision && !card.resolvedDecisions.length) continue
    const anchor = result.findIndex((entry) => entry.kind === 'message' && entry.runId === card.runId && entry.message.role === 'user')
    if (anchor >= 0) result.splice(anchor + 1, 0, { kind: 'task', id: card.runId, card })
    else if (card.runId === props.activeTaskRunId) result.push({ kind: 'task', id: card.runId, card })
  }
  return result
})
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
  () => [props.messages, props.operations, props.tasks, props.isStreaming],
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
      <template v-for="entry in entries" :key="`${entry.kind}:${entry.id}`">
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
          :can-resume="canResume"
          @download="(artifactId, name) => emit('download', artifactId, name)"
          @cancel="emit('cancel', $event)"
          @resumed="emit('resumed', $event)"
          @layout-change="maybeFollowScroll"
        />
        <TaskTimelineEntry
          v-else-if="entry.kind === 'task'"
          :card="entry.card"
          :read-only="readOnly || (activeTaskRunId != null && entry.card.runId !== activeTaskRunId)"
          @respond="(action, answer) => emit('respond', entry.card.runId, action, answer)"
        />
      </template>

      <details v-if="isStreaming && awaitingAssistant" data-testid="typing-indicator"
        class="text-xs text-muted-foreground">
        <summary class="cursor-pointer rounded-md py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" role="status">
          Thinking <span data-testid="thinking-dots" aria-hidden="true">…</span>
        </summary>
        <p class="pl-4">The agent is working.</p>
      </details>

      <!-- One standalone status when streaming with no messages at all:
           never a second placeholder. -->
      <AgentMessage
        v-if="isStreaming && entries.length === 0"
        :message="standaloneStatus"
        :is-streaming="true"
      />

      <div
        v-if="entries.length === 0 && !isStreaming"
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
