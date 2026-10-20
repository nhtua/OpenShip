<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Button, Icon, Label, Textarea } from '@/components/ui'
import ProviderSelector from '@/components/ProviderSelector.vue'
import ToolAttachmentMenu from './ToolAttachmentMenu.vue'
import type { CapabilityRef, CapabilitySummary, ModelProfileItem, ToolCommandRequest } from '@/types'

interface Props {
  disabled?: boolean
  profiles: ModelProfileItem[]
  defaultProfileId: string | null
  selectedProfileId: string | null
  fallbackIds: string[]
  catalogError: string | null
  selectionDisabled: boolean
  capabilities: CapabilitySummary[]
  capabilityError?: string | null
  toolSubmissionDisabled?: boolean
  submissionError?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
  profiles: () => [],
  defaultProfileId: null,
  selectedProfileId: null,
  fallbackIds: () => [],
  catalogError: null,
  selectionDisabled: false,
  capabilities: () => [],
  capabilityError: null,
  toolSubmissionDisabled: false,
  submissionError: null,
})

const emit = defineEmits<{
  send: [content: string, options?: { enableTools: boolean }]
  search: [command: Omit<ToolCommandRequest, 'client_request_id'>]
  select: [profileId: string | null, fallbackIds: string[]]
}>()

const content = ref('')
const maxResults = ref(5)
const attachedCapability = ref<CapabilityRef | null>(null)
const automaticToolMode = ref(false)
const automaticToolModeTouched = ref(false)
const textareaRef = ref<InstanceType<typeof Textarea> | null>(null)
const activeRunToolDisabledReason = 'Wait for the current run to finish before using tools.'
let pendingDraft: {
  content: string
  capability: CapabilityRef | null
  maxResults: number
  automaticToolMode: boolean
} | null = null

const effectiveProfile = computed(() =>
  props.profiles.find((profile) => profile.profile_id === props.selectedProfileId) ??
  props.profiles.find((profile) => profile.profile_id === props.defaultProfileId) ??
  null,
)
const automaticToolDisabledReason = computed(() => {
  if (attachedCapability.value) return 'Remove the attached tool to let the model choose automatically.'
  if (props.capabilityError) return 'The search tool catalog is unavailable.'
  if (props.catalogError) return 'The model profile catalog is unavailable.'
  if (!props.capabilities.some((capability) => capability.enabled && capability.available)) {
    return props.capabilities.length
      ? 'Configure at least one available search tool.'
      : 'No search tools are configured.'
  }
  if (
    !effectiveProfile.value?.available ||
    effectiveProfile.value.adapter !== 'openai' ||
    effectiveProfile.value.capabilities.tool_calls !== true ||
    effectiveProfile.value.capabilities.parallel_tool_calls === true
  ) return 'Choose a model with sequential tool-call support.'
  if (props.fallbackIds.length) return 'Turn off model fallbacks to use tools automatically.'
  if (props.toolSubmissionDisabled) return activeRunToolDisabledReason
  return null
})
const automaticToolWarning = computed(() =>
  automaticToolDisabledReason.value === activeRunToolDisabledReason
    ? null
    : automaticToolDisabledReason.value,
)
const automaticToolDisabled = computed(() => Boolean(automaticToolDisabledReason.value))

// Start with automatic tools enabled when this installation has a compatible
// model and search capability. Keep the choice stable after the user changes it.
watch(automaticToolDisabled, (disabled) => {
  if (!disabled && !automaticToolModeTouched.value) automaticToolMode.value = true
}, { immediate: true })

const attachedSummary = computed(() => props.capabilities.find(
  (capability) => capability.id === attachedCapability.value?.id,
) ?? null)
const attachedLabel = computed(() => {
  if (attachedCapability.value?.id === 'brave_web_search') return 'Brave Search'
  if (attachedCapability.value?.id === 'tavily_web_search') return 'Tavily Search'
  return attachedCapability.value?.id ?? ''
})
const toolUnavailable = computed(() => Boolean(
  attachedCapability.value && (!attachedSummary.value?.available || !attachedSummary.value.enabled),
))
const submitDisabled = computed(() =>
  props.disabled || !content.value.trim() ||
  (Boolean(attachedCapability.value) && (props.toolSubmissionDisabled || toolUnavailable.value)) ||
  (automaticToolMode.value && !attachedCapability.value && automaticToolDisabled.value),
)

function forwardSelect(profileId: string | null, fallbackIds: string[]) {
  emit('select', profileId, fallbackIds)
}

function attachTool(capability: CapabilityRef) {
  attachedCapability.value = capability
  automaticToolMode.value = false
  maxResults.value = 5
}

function removeTool() {
  attachedCapability.value = null
  maxResults.value = 5
}

function toggleAutomaticToolMode() {
  if (automaticToolDisabled.value) return
  automaticToolModeTouched.value = true
  automaticToolMode.value = !automaticToolMode.value
}

function disableAutomaticToolMode() {
  automaticToolModeTouched.value = true
  automaticToolMode.value = false
}

function grow() {
  const el = textareaRef.value?.$el as HTMLElement | undefined
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 192)}px`
}

async function focusInput() {
  await nextTick()
  const el = textareaRef.value?.$el as HTMLElement | undefined
  el?.focus()
}

async function handleSubmit() {
  if (submitDisabled.value) return
  const text = content.value.trim()
  if (!text) return

  pendingDraft = {
    content: content.value,
    capability: attachedCapability.value ? { ...attachedCapability.value } : null,
    maxResults: maxResults.value,
    automaticToolMode: automaticToolMode.value,
  }
  if (attachedCapability.value) {
    emit('search', {
      capability: { ...attachedCapability.value },
      arguments: { query: text, max_results: maxResults.value },
    })
  } else {
    if (automaticToolMode.value) emit('send', text, { enableTools: true })
    else emit('send', text)
  }
  content.value = ''
  await nextTick()
  grow()
}

watch(
  () => props.submissionError,
  (submissionError) => {
    if (!submissionError || !pendingDraft) return
    content.value = pendingDraft.content
    attachedCapability.value = pendingDraft.capability
    maxResults.value = pendingDraft.maxResults
    automaticToolMode.value = pendingDraft.automaticToolMode
    pendingDraft = null
    void nextTick(grow)
  },
)

watch(
  () => props.disabled,
  (disabled, wasDisabled) => {
    if (disabled && !wasDisabled) return
    if (!disabled && wasDisabled && pendingDraft) {
      // The parent only releases the draft after the submission was accepted.
      pendingDraft = null
      removeTool()
      void focusInput()
    }
    if (wasDisabled && !disabled) void focusInput()
  },
)

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey) return
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void handleSubmit()
}

onMounted(() => { void focusInput() })
</script>

<template>
  <form
    class="shrink-0 border-t border-border bg-card p-3 md:p-4"
    data-testid="composer"
    @submit.prevent="handleSubmit"
  >
    <Label for="message" class="sr-only">Message</Label>
    <Textarea
      ref="textareaRef"
      v-model="content"
      id="message"
      name="message"
      rows="1"
      :disabled="props.disabled"
      placeholder="Message the agent..."
      class="min-h-[42px] max-h-48 resize-none overflow-y-auto py-2"
      @keydown="handleKeydown"
      @input="grow"
    />

    <div class="mt-2 flex min-w-0 flex-wrap items-center gap-2">
      <ToolAttachmentMenu
        :capabilities="props.capabilities"
        :catalog-error="props.capabilityError"
        :selected-capability-id="attachedCapability?.id ?? null"
        :automatic-tool-mode="automaticToolMode"
        :automatic-tool-disabled="automaticToolDisabled"
        :automatic-tool-disabled-reason="automaticToolDisabledReason"
        :disabled="props.disabled"
        @attach="attachTool"
        @toggle-automatic="toggleAutomaticToolMode"
      />

      <button
        v-if="automaticToolMode"
        type="button"
        data-testid="automatic-tool-chip"
        aria-label="Disable automatic tool use"
        :title="automaticToolDisabledReason || 'The model may use available search tools for this message.'"
        class="inline-flex h-8 items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-3 text-xs text-foreground hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="disableAutomaticToolMode"
      >
        <span>Automatic tools</span>
        <span aria-hidden="true">×</span>
      </button>

      <div
        v-if="attachedCapability"
        data-testid="attached-tool"
        class="inline-flex h-8 items-center gap-1 rounded-full border border-primary/30 bg-primary/10 pl-3 pr-1 text-xs text-foreground"
      >
        <span>{{ attachedLabel }}</span>
        <label class="sr-only" for="search-result-count">Number of results</label>
        <select
          id="search-result-count"
          v-model.number="maxResults"
          aria-label="Number of results"
          class="h-6 rounded-full border-0 bg-transparent px-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.disabled"
        >
          <option v-for="count in 10" :key="count" :value="count">{{ count }}</option>
        </select>
        <button
          type="button"
          class="ml-1 inline-flex size-6 items-center justify-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :aria-label="`Remove ${attachedLabel}`"
          @click="removeTool"
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>

      <span v-if="toolUnavailable" class="text-xs text-destructive">
        This search tool is unavailable. Remove it or configure its connection.
      </span>
      <span v-else-if="attachedCapability && props.toolSubmissionDisabled" class="text-xs text-muted-foreground">
        Wait for the current run to finish before searching.
      </span>
      <span v-else-if="automaticToolMode && automaticToolWarning" class="text-xs text-destructive">
        {{ automaticToolWarning }}
      </span>

      <div class="ml-auto flex min-w-0 items-center gap-2">
        <ProviderSelector
          :profiles="props.profiles"
          :default-profile-id="props.defaultProfileId"
          :selected-profile-id="props.selectedProfileId"
          :fallback-ids="props.fallbackIds"
          :catalog-error="props.catalogError"
          :change-disabled="props.selectionDisabled"
          @select="forwardSelect"
        />
        <Button type="submit" size="sm" :disabled="submitDisabled" class="h-8 shrink-0 px-3">
          <Icon name="send" />
          {{ props.disabled ? 'Streaming...' : attachedCapability ? 'Search' : 'Send' }}
        </Button>
      </div>
    </div>
    <p class="mt-1.5 text-xs text-muted-foreground">
      Enter to {{ attachedCapability ? 'search' : 'send' }} · Shift+Enter for a new line
    </p>
  </form>
</template>
