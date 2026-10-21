<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ModelProfileItem } from '@/types'

interface Props {
  profiles: ModelProfileItem[]
  defaultProfileId: string | null
  selectedProfileId: string | null
  fallbackIds: string[]
  catalogError?: string | null
  changeDisabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  profiles: () => [],
  defaultProfileId: null,
  selectedProfileId: null,
  fallbackIds: () => [],
  catalogError: null,
  changeDisabled: false,
})

const emit = defineEmits<{
  select: [profileId: string | null, fallbackIds: string[]]
}>()

const open = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)
const menu = ref<HTMLElement | null>(null)

const selected = computed<ModelProfileItem | null>(() => {
  if (!props.selectedProfileId) return null
  return props.profiles.find((profile) => profile.profile_id === props.selectedProfileId) ?? null
})
const staleSelection = computed(() => Boolean(props.selectedProfileId) && !selected.value)
const effective = computed<ModelProfileItem | null>(() =>
  props.selectedProfileId
    ? selected.value
    : props.profiles.find((profile) => profile.profile_id === props.defaultProfileId) ?? null,
)
const triggerText = computed(() => {
  if (props.catalogError) return 'Catalog unavailable'
  if (staleSelection.value) return 'Needs replacement'
  return effective.value?.model ?? 'Model unavailable'
})
const fallbackChoices = computed(() => {
  const allowed = effective.value?.allowed_fallback_profile_ids ?? []
  return props.profiles.filter((profile) => allowed.includes(profile.profile_id))
})

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

function chooseProfile(profileId: string) {
  if (props.changeDisabled) return
  const allowed = props.profiles.find((profile) => profile.profile_id === profileId)
    ?.allowed_fallback_profile_ids ?? []
  emit('select', profileId, props.fallbackIds.filter((id) => allowed.includes(id)))
  close(true)
}

function toggleFallback(profileId: string) {
  if (props.changeDisabled) return
  const selected = props.fallbackIds.includes(profileId)
  const next = selected
    ? props.fallbackIds.filter((id) => id !== profileId)
    : [...props.fallbackIds, profileId]
  emit('select', props.selectedProfileId, next)
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

function onDocumentClick(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  if (menu.value?.contains(target) || trigger.value?.contains(target)) return
  close()
}

onMounted(() => document.addEventListener('click', onDocumentClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))
</script>

<template>
  <div class="relative" data-testid="provider-selector" @keydown="onKeydown">
    <button
      ref="trigger"
      type="button"
      data-testid="model-trigger"
      aria-label="Choose model"
      aria-haspopup="menu"
      :aria-expanded="open"
      class="inline-flex h-8 max-w-48 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      @click="toggle"
    >
      <span class="truncate">{{ triggerText }}</span>
      <span aria-hidden="true" class="text-xs">⌄</span>
    </button>

    <div
      v-if="open"
      ref="menu"
      role="menu"
      aria-label="Model choices"
      class="absolute bottom-10 right-0 z-30 max-h-[min(70vh,32rem)] w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-lg border border-border bg-popover p-2 text-popover-foreground shadow-lg"
    >
      <p class="px-2 pb-2 pt-1 text-xs font-medium text-muted-foreground">
        Default for new conversations: {{ profiles.find((profile) => profile.profile_id === defaultProfileId)?.model ?? 'not configured' }}
      </p>
      <p v-if="catalogError" class="px-2 pb-2 text-xs text-destructive" role="alert">
        {{ catalogError }}
      </p>
      <p v-else-if="staleSelection" class="px-2 pb-2 text-xs text-destructive" role="alert">
        The previously selected model is no longer available. Choose a current profile.
      </p>
      <div
        v-for="profile in profiles"
        :key="profile.profile_id"
        data-testid="provider-option"
        class="rounded-md"
      >
        <button
          v-if="profile.available"
          type="button"
          role="menuitemradio"
          :aria-checked="effective?.profile_id === profile.profile_id"
          :disabled="props.changeDisabled"
          class="flex w-full items-start justify-between gap-3 rounded-md px-2 py-2 text-left hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          @click="chooseProfile(profile.profile_id)"
        >
          <span class="min-w-0">
            <span class="block text-sm font-medium">{{ profile.model }}</span>
            <span class="mt-0.5 block truncate text-xs text-muted-foreground">
              {{ profile.label }} · {{ profile.adapter }}<template v-if="profile.destination_label"> · {{ profile.destination_label }}</template>
            </span>
          </span>
          <span v-if="effective?.profile_id === profile.profile_id" class="shrink-0 text-xs text-primary">
            Selected
          </span>
        </button>
        <p v-else class="px-2 py-2 text-sm text-muted-foreground">
          <span class="block">{{ profile.label }} · {{ profile.model }} · unavailable</span>
          <span class="mt-0.5 block text-xs">{{ profile.explanation }}</span>
        </p>
      </div>
      <div v-if="fallbackChoices.length" class="mt-2 border-t border-border px-2 pt-2">
        <p class="mb-1 text-xs font-medium text-muted-foreground">Fallback models</p>
        <button
          v-for="profile in fallbackChoices"
          :key="profile.profile_id"
          data-testid="fallback-option"
          type="button"
          :disabled="props.changeDisabled"
          class="block w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
          @click="toggleFallback(profile.profile_id)"
        >
          {{ fallbackIds.includes(profile.profile_id) ? '✓ ' : '' }}{{ profile.model }}
          <span class="text-muted-foreground">
            — {{ fallbackIds.includes(profile.profile_id) ? 'enabled' : 'optional' }}
          </span>
        </button>
      </div>
    </div>
  </div>
</template>
