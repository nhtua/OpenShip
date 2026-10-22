<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Button, Icon, Label, Textarea } from '@/components/ui'
import ProviderSelector from '@/components/ProviderSelector.vue'
import ToolAttachmentMenu from './ToolAttachmentMenu.vue'
import type {
  CapabilityRef,
  CapabilitySummary,
  ConnectorKind,
  ConnectorSelections,
  ConnectorUsage,
  ModelProfileItem,
  ProjectCapabilitySummary,
  ToolCommandRequest,
} from '@/types'

interface Props {
  disabled?: boolean
  profiles: ModelProfileItem[]
  defaultProfileId: string | null
  selectedProfileId: string | null
  fallbackIds: string[]
  catalogError: string | null
  selectionDisabled: boolean
  capabilities: Array<CapabilitySummary | ProjectCapabilitySummary>
  capabilityError?: string | null
  toolSubmissionDisabled?: boolean
  submissionError?: string | null
  connectors?: ConnectorUsage[]
  defaultConnectorIds?: ConnectorSelections
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
  defaultConnectorIds: () => ({}),
})

const emit = defineEmits<{
  send: [content: string, options?: { enableTools: boolean; connectorSelections?: ConnectorSelections }]
  search: [command: Omit<ToolCommandRequest, 'client_request_id'>]
  select: [profileId: string | null, fallbackIds: string[]]
}>()

const content = ref('')
const desktopViewportQuery =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 40rem)')
    : null
const showKeyboardHint = ref(desktopViewportQuery?.matches ?? false)
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
  connectorId: string | null
} | null = null
const selectedConnectorId = ref<string | null>(null)

const inputPlaceholder = computed(() =>
  showKeyboardHint.value
    ? 'Message the agent...   Enter to send · Shift+Enter for a new line'
    : 'Message the agent...',
)

const effectiveProfile = computed(() =>
  props.profiles.find((profile) => profile.profile_id === props.selectedProfileId) ??
  props.profiles.find((profile) => profile.profile_id === props.defaultProfileId) ??
  null,
)
const automaticToolDisabledReason = computed(() => {
  if (attachedCapability.value) return 'Remove the attached tool to let the model choose automatically.'
  if (props.capabilityError) return 'The search tool catalog is unavailable.'
  if (props.catalogError) return 'The model profile catalog is unavailable.'
  if (!props.capabilities.some((capability) =>
    capability.permitted !== false && capability.enabled !== false && capability.available !== false,
  )) {
    return props.capabilities.length
      ? 'Configure at least one available search tool.'
      : 'No search tools are configured.'
  }
  const connectorNotReady = props.capabilities.some((capability) =>
    capability.permitted !== false &&
    capability.required_connector_kinds?.some((kind) =>
      capability.connector_readiness?.[kind] !== 'ready',
    ),
  )
  if (connectorNotReady) {
    return 'Configure a Connector, or choose a project default when a provider has multiple eligible Connectors.'
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
  attachedCapability.value && (
    attachedSummary.value?.available === false || attachedSummary.value?.enabled === false ||
    attachedSummary.value?.permitted === false
  ),
))
const attachedConnectorKind = computed<ConnectorKind | null>(() => {
  if (attachedCapability.value?.id === 'brave_web_search') return 'brave'
  if (attachedCapability.value?.id === 'tavily_web_search') return 'tavily'
  return null
})
const attachedConnectorChoices = computed(() => {
  const kind = attachedConnectorKind.value
  if (!kind) return []
  return (props.connectors ?? []).filter(
    (connector) => connector.kind === kind && connector.status === 'configured',
  )
})
const connectorSelectionMissing = computed(() => Boolean(
  attachedConnectorKind.value && props.connectors !== undefined &&
  !attachedConnectorChoices.value.some((connector) => connector.id === selectedConnectorId.value),
))
const submitDisabled = computed(() =>
  props.disabled || !content.value.trim() ||
  (Boolean(attachedCapability.value) && (props.toolSubmissionDisabled || toolUnavailable.value || connectorSelectionMissing.value)) ||
  (automaticToolMode.value && !attachedCapability.value && automaticToolDisabled.value),
)

function forwardSelect(profileId: string | null, fallbackIds: string[]) {
  emit('select', profileId, fallbackIds)
}

function attachTool(capability: CapabilityRef) {
  attachedCapability.value = capability
  automaticToolMode.value = false
  maxResults.value = 5
  const kind = capability.id === 'brave_web_search'
    ? 'brave'
    : capability.id === 'tavily_web_search'
      ? 'tavily'
      : null
  const choices = kind
    ? (props.connectors ?? []).filter((connector) => connector.kind === kind && connector.status === 'configured')
    : []
  const defaultId = kind ? props.defaultConnectorIds[kind] : undefined
  selectedConnectorId.value = choices.some((connector) => connector.id === defaultId)
    ? defaultId ?? null
    : choices.length === 1
      ? choices[0].id
      : null
}

function removeTool() {
  attachedCapability.value = null
  maxResults.value = 5
  selectedConnectorId.value = null
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
    connectorId: selectedConnectorId.value,
  }
  if (attachedCapability.value) {
    const connectorSelections: ConnectorSelections = {}
    if (attachedConnectorKind.value && selectedConnectorId.value) {
      connectorSelections[attachedConnectorKind.value] = selectedConnectorId.value
    }
    const command: Omit<ToolCommandRequest, 'client_request_id'> = {
      capability: { ...attachedCapability.value },
      arguments: { query: text, max_results: maxResults.value },
    }
    if (props.connectors !== undefined) command.connector_selections = connectorSelections
    emit('search', command)
  } else {
    if (automaticToolMode.value) {
      const connectorSelections: ConnectorSelections = {}
      for (const kind of ['brave', 'tavily'] as const) {
        const connectorId = props.defaultConnectorIds[kind]
        if (connectorId) connectorSelections[kind] = connectorId
      }
      const options: { enableTools: boolean; connectorSelections?: ConnectorSelections } = {
        enableTools: true,
      }
      if (props.connectors !== undefined || Object.keys(connectorSelections).length) {
        options.connectorSelections = connectorSelections
      }
      emit('send', text, options)
    }
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
    selectedConnectorId.value = pendingDraft.connectorId
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

function updateKeyboardHint(event: MediaQueryListEvent) {
  showKeyboardHint.value = event.matches
}

onMounted(() => {
  void focusInput()
  desktopViewportQuery?.addEventListener('change', updateKeyboardHint)
})

onBeforeUnmount(() => {
  desktopViewportQuery?.removeEventListener('change', updateKeyboardHint)
})
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
      :placeholder="inputPlaceholder"
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
          v-if="attachedConnectorKind && props.connectors !== undefined"
          v-model="selectedConnectorId"
          data-testid="connector-selection"
          aria-label="Search connector"
          class="h-6 max-w-40 rounded-full border-0 bg-transparent px-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.disabled"
        >
          <option :value="null">Choose a connector</option>
          <option
            v-for="connector in attachedConnectorChoices"
            :key="connector.id"
            :value="connector.id"
          >
            {{ connector.name }} · {{ connector.visibility === 'global' ? 'Global' : 'Project' }}{{ connector.is_default ? ' · Default' : '' }}
          </option>
        </select>
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
        <Button
          type="submit"
          size="sm"
          :disabled="submitDisabled"
          :aria-label="props.disabled ? 'Streaming' : undefined"
          class="h-8 shrink-0 px-3"
        >
          <Icon name="send" />
          <span v-if="props.disabled" class="relative inline-flex items-center justify-center">
            <span class="invisible">{{ attachedCapability ? 'Search' : 'Send' }}</span>
            <span class="absolute inset-0 inline-flex items-center justify-center" aria-hidden="true">
              <span class="flex gap-1">
                <span class="size-1.5 animate-bounce rounded-full bg-current"></span>
                <span
                  class="size-1.5 animate-bounce rounded-full bg-current"
                  style="animation-delay: 150ms"
                ></span>
                <span
                  class="size-1.5 animate-bounce rounded-full bg-current"
                  style="animation-delay: 300ms"
                ></span>
              </span>
            </span>
            <span class="sr-only">Streaming</span>
          </span>
          <span v-else>{{ attachedCapability ? 'Search' : 'Send' }}</span>
        </Button>
      </div>
    </div>
  </form>
</template>
