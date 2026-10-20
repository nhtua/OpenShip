<script setup lang="ts">
import { nextTick, ref } from 'vue'
import type { CapabilityRef, CapabilitySummary } from '@/types'

interface Props {
  capabilities: CapabilitySummary[]
  selectedCapabilityId: string | null
  disabled: boolean
  catalogError?: string | null
  automaticToolMode?: boolean
  automaticToolDisabled?: boolean
  automaticToolDisabledReason?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  catalogError: null,
  automaticToolMode: false,
  automaticToolDisabled: false,
  automaticToolDisabledReason: null,
})
const emit = defineEmits<{
  attach: [capability: CapabilityRef]
  'toggle-automatic': []
}>()

const open = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)

function label(id: string): string {
  if (id === 'brave_web_search') return 'Brave Search'
  if (id === 'tavily_web_search') return 'Tavily Search'
  return id
}

function unavailableReason(capability: CapabilitySummary): string {
  if (!capability.enabled || capability.unavailable_reason === 'disabled') return 'Disabled in tool settings'
  if (capability.unavailable_reason === 'missing_credentials') return 'Add a search provider connection'
  if (capability.unavailable_reason === 'runtime_unavailable') return 'Search service is not configured'
  return 'Unavailable in this installation'
}

function close(returnFocus = false) {
  open.value = false
  if (returnFocus) void nextTick(() => trigger.value?.focus())
}

async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    await nextTick()
    menu.value?.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus()
  }
}

function attach(capability: CapabilitySummary) {
  if (props.disabled || !capability.enabled || !capability.available) return
  emit('attach', {
    id: capability.id,
    version: capability.version,
    manifest_digest: capability.manifest_digest,
  })
  close(true)
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
  const next = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? choices.length - 1
      : event.key === 'ArrowDown'
        ? (current + 1 + choices.length) % choices.length
        : (current <= 0 ? choices.length - 1 : current - 1)
  choices[next].focus()
}
</script>

<template>
  <div class="relative" @keydown="onKeydown">
    <button
      ref="trigger"
      type="button"
      class="inline-flex size-8 items-center justify-center rounded-full border border-input bg-background text-lg leading-none hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
      aria-label="Add tool"
      aria-haspopup="menu"
      :aria-expanded="open"
      :disabled="props.disabled"
      data-testid="tool-attachment-trigger"
      @click="toggle"
    >
      <span aria-hidden="true">+</span>
    </button>

    <div
      v-if="open"
      ref="menu"
      role="menu"
      aria-label="Available tools"
      data-testid="tool-menu"
      class="absolute bottom-11 left-0 z-30 w-72 max-w-[calc(100vw-2rem)] rounded-lg border border-border bg-popover p-2 text-popover-foreground shadow-lg"
    >
      <p class="px-2 pb-2 pt-1 text-xs font-medium text-muted-foreground">
        Add a tool to this message
      </p>
      <p v-if="props.catalogError" class="px-2 pb-2 text-xs text-destructive" role="alert">
        {{ props.catalogError }}
      </p>
      <button
        type="button"
        role="menuitemcheckbox"
        aria-label="Let the model use available tools automatically"
        :aria-checked="props.automaticToolMode"
        :disabled="props.disabled || props.automaticToolDisabled"
        data-testid="automatic-tool-mode"
        class="flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
        @click="emit('toggle-automatic')"
      >
        <span aria-hidden="true" class="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded border border-input text-xs">
          {{ props.automaticToolMode ? '✓' : '' }}
        </span>
        <span class="min-w-0">
          <span class="block font-medium">Let the model use tools automatically</span>
          <span class="mt-0.5 block text-xs text-muted-foreground">
            {{ props.automaticToolDisabledReason || 'The model can choose among enabled search tools.' }}
          </span>
        </span>
      </button>
      <div class="my-1 border-t border-border" />
      <p class="px-2 pb-2 pt-1 text-xs font-medium text-muted-foreground">
        Attach one tool to run it directly
      </p>
      <p v-if="!props.capabilities.length" class="px-2 pb-2 text-xs text-muted-foreground">
        No search tools are configured.
      </p>
      <button
        v-for="capability in props.capabilities"
        :key="capability.id"
        type="button"
        role="menuitem"
        data-testid="tool-choice"
        class="flex w-full items-start justify-between gap-3 rounded-md px-2 py-2 text-left text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
        :disabled="props.disabled || !capability.available || !capability.enabled"
        @click="attach(capability)"
      >
        <span class="min-w-0">
          <span class="block font-medium">{{ label(capability.id) }}</span>
          <span v-if="!capability.available || !capability.enabled" class="mt-0.5 block text-xs text-muted-foreground">
            {{ unavailableReason(capability) }}
          </span>
        </span>
        <span
          v-if="props.selectedCapabilityId === capability.id"
          class="shrink-0 text-xs text-muted-foreground"
        >
          Attached
        </span>
      </button>
    </div>
  </div>
</template>
