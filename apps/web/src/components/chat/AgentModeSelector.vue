<script setup lang="ts">
import { computed } from 'vue'
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

function word(mode: AgentMode): string {
  return mode === 'auto' ? 'Auto' : 'Copilot'
}

const currentLabel = computed(() => (props.mode ? word(props.mode) : 'Mode unknown'))
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
}
</script>

<template>
  <div
    role="radiogroup"
    aria-label="Agent execution mode"
    aria-live="polite"
    data-testid="agent-mode-selector"
    class="flex flex-wrap items-center gap-3 rounded-md bg-muted/30 px-3 py-2 text-sm"
  >
    <strong data-testid="agent-mode-current" class="font-medium">
      {{ currentLabel }}<template v-if="props.revision"> · revision {{ props.revision }}</template>
    </strong>

    <span
      v-if="pendingLabel"
      data-testid="agent-mode-pending"
      class="text-muted-foreground"
    >
      {{ pendingLabel }}
    </span>

    <p
      v-if="props.message"
      data-testid="agent-mode-message"
      aria-live="polite"
      class="text-xs text-muted-foreground"
    >
      {{ props.message }}
    </p>

    <p data-testid="agent-mode-scope" class="text-xs text-muted-foreground">
      Applies to new tasks
    </p>

    <button
      type="button"
      data-testid="agent-mode-copilot-choice"
      aria-label="Use Copilot for new tasks"
      :aria-current="props.mode === 'copilot' ? 'true' : undefined"
      :aria-disabled="props.blocked ? 'true' : undefined"
      :disabled="props.blocked"
      @click="choose('copilot')"
      class="rounded-md px-2 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
    >
      Copilot
    </button>

    <button
      type="button"
      data-testid="agent-mode-auto-choice"
      aria-label="Use Auto for new tasks"
      :aria-current="props.mode === 'auto' ? 'true' : undefined"
      :aria-disabled="props.blocked ? 'true' : undefined"
      :disabled="props.blocked"
      @click="choose('auto')"
      class="rounded-md px-2 py-1 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
    >
      Auto
    </button>
  </div>
</template>
