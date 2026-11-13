<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { AgentMode } from '@/types'

interface Props {
  mode?: AgentMode | null
  pendingMode?: AgentMode | null
  revision?: number
  loading?: boolean
  saving?: boolean
  conflict?: boolean
  blocked?: boolean
  message?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  mode: null,
  pendingMode: null,
  revision: 0,
  loading: false,
  saving: false,
  conflict: false,
  blocked: false,
  message: null,
})

const emit = defineEmits<{
  choose: [mode: AgentMode]
}>()

const open = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)

async function toggle() {
  open.value = !open.value
  if (open.value) {
    await nextTick()
    menu.value?.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus()
  }
}

function close(returnFocus = false) {
  open.value = false
  if (returnFocus) void nextTick(() => trigger.value?.focus())
}

function onKeydown(event: KeyboardEvent) {
  if (!open.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    close(true)
    return
  }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  const choices = [...(menu.value?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') ?? [])]
  if (!choices.length) return
  event.preventDefault()
  const current = choices.indexOf(document.activeElement as HTMLButtonElement)
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1
    : event.key === 'ArrowDown' ? (current + 1) % choices.length
      : (current <= 0 ? choices.length - 1 : current - 1)
  choices[index].focus()
}

function onDocumentClick(event: MouseEvent) {
  const target = event.target
  if (target instanceof Node && !menu.value?.contains(target) && !trigger.value?.contains(target)) close()
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))

function word(mode: AgentMode): string {
  return mode === 'auto' ? 'Auto' : 'Copilot'
}

const pendingLabel = computed(() => {
  if (!props.pendingMode || props.pendingMode === props.mode) return null
  // While the save is in flight the account has not confirmed anything, so the
  // row says a save is happening rather than naming the new mode as current.
  if (props.saving) return `Saving ${word(props.pendingMode)}…`
  return `${word(props.pendingMode)} · not saved`
})

function choose(mode: AgentMode) {
  if (props.blocked) return
  emit('choose', mode)
  close(true)
}
</script>

<template>
  <div data-testid="agent-mode-selector" class="relative" aria-live="polite" @keydown="onKeydown">
    <button ref="trigger" type="button" aria-label="Agent execution mode" data-testid="agent-mode-current"
      aria-haspopup="menu" :aria-expanded="open" title="Applies to new tasks"
      class="inline-flex h-8 max-w-48 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      @click="toggle">
      <span class="truncate">{{ props.mode ? word(props.mode) : 'Mode unknown' }}</span>
      <span aria-hidden="true" class="text-xs">⌄</span>
    </button>
    <div v-if="open" ref="menu" role="menu" aria-label="Agent mode choices"
      class="absolute bottom-10 right-0 z-30 w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-popover p-2 text-popover-foreground shadow-lg">
      <p class="px-2 pb-2 pt-1 text-xs font-medium text-muted-foreground">Applies to new tasks</p>
      <button v-for="mode in (['copilot', 'auto'] as AgentMode[])" :key="mode" type="button"
        role="menuitemradio" :aria-checked="props.mode === mode" :disabled="props.blocked"
        :data-testid="`agent-mode-${mode}-choice`"
        class="flex w-full items-start justify-between gap-3 rounded-md px-2 py-2 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
        @click="choose(mode)">
        <span class="text-sm font-medium">{{ word(mode) }}</span>
        <span v-if="props.mode === mode" class="shrink-0 text-xs text-primary">Selected</span>
      </button>
    </div>
    <span class="sr-only" data-testid="agent-mode-scope">Applies to new tasks</span>
    <span v-if="pendingLabel" data-testid="agent-mode-pending" class="text-muted-foreground">{{ pendingLabel }}</span>
    <p v-if="props.message" data-testid="agent-mode-message" role="alert" class="text-destructive">{{ props.message }}</p>
  </div>
</template>
