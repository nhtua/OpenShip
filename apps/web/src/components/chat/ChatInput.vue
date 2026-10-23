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
  ResourceBindingSummary,
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
  resourceBindings?: ResourceBindingSummary[]
  draftContent?: string | null
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
  resourceBindings: () => [],
  draftContent: null,
})

const emit = defineEmits<{
  send: [content: string, options?: { enableTools: boolean; connectorSelections?: ConnectorSelections; resourceBindingSelections?: string[] }]
  search: [command: Omit<ToolCommandRequest, 'client_request_id'>]
  select: [profileId: string | null, fallbackIds: string[]]
  'draft-consumed': []
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
  resourceBindingIds: string[]
} | null = null
const selectedConnectorId = ref<string | null>(null)
const selectedResourceBindingIds = ref<string[]>([])
const selectedStorageBindingId = ref<string | null>(null)
const isS3ReadTool = computed(() => ['aws_s3_bucket_storage', 'aws_s3_bucket_metadata'].includes(attachedCapability.value?.id ?? ''))
const activeResourceBindings = computed(() => props.resourceBindings.filter((binding) => binding.state === 'active'))

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
    const deploymentRestricted = props.capabilities.some((capability) =>
      capability.unavailable_reason === 'deployment_restricted' || capability.deployment_restricted === true,
    )
    const disabledByOperator = props.capabilities.some((capability) =>
      capability.unavailable_reason === 'disabled_by_operator',
    )
    if (props.capabilities.some((capability) => capability.unavailable_reason === 'policy_unavailable')) {
      return 'Tool availability could not be confirmed. Ask the installation operator to check Global Settings.'
    }
    if (deploymentRestricted && disabledByOperator) {
      return 'Enable at least one tool in Global Settings and include it in TOOL_ENABLED_IDS.'
    }
    if (deploymentRestricted) {
      return 'Tools are restricted by TOOL_ENABLED_IDS. Ask the installation operator to update the deployment ceiling.'
    }
    if (disabledByOperator) return 'Enable at least one tool in Global Settings.'
    return props.capabilities.length
      ? 'Configure a Connector or bind an S3 bucket to enable tools.'
      : 'No tools are configured.'
  }
  const hasReadyCapability = props.capabilities.some((capability) =>
    capability.permitted !== false && capability.enabled !== false && capability.available !== false &&
    (capability.required_connector_kinds ?? []).every((kind) =>
      capability.connector_readiness?.[kind] === 'ready',
    ) && (
      !['aws_s3_bucket_storage', 'aws_s3_bucket_metadata'].includes(capability.id) ||
      selectedResourceBindingIds.value.some((id) => props.resourceBindings.some(
        (binding) => binding.binding_id === id && binding.state === 'active',
      ))
    ),
  )
  if (!hasReadyCapability) {
    if (props.capabilities.some((capability) =>
      (capability.required_connector_kinds ?? []).some((kind) =>
        (kind === 'brave' || kind === 'tavily') && capability.connector_readiness?.[kind] === 'ambiguous',
      ),
    )) {
      return 'Configure a Connector, or choose a project default when a provider has multiple eligible Connectors.'
    }
    return 'Choose a ready search Connector or select an active S3 bucket.'
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

watch(activeResourceBindings, (bindings) => {
  const availableIds = new Set(bindings.map((binding) => binding.binding_id))
  selectedResourceBindingIds.value = selectedResourceBindingIds.value.filter((id) => availableIds.has(id))
  if (!selectedStorageBindingId.value || !availableIds.has(selectedStorageBindingId.value)) {
    selectedStorageBindingId.value = bindings.length === 1 ? bindings[0].binding_id : null
  }
})

const attachedSummary = computed(() => props.capabilities.find(
  (capability) => capability.id === attachedCapability.value?.id,
) ?? null)
const attachedLabel = computed(() => {
  if (attachedCapability.value?.id === 'brave_web_search') return 'Brave Search'
  if (attachedCapability.value?.id === 'tavily_web_search') return 'Tavily Search'
  if (attachedCapability.value?.id === 'aws_s3_bucket_storage') return 'S3 storage'
  if (attachedCapability.value?.id === 'aws_s3_bucket_metadata') return 'S3 metadata'
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
  props.disabled || (!isS3ReadTool.value && !content.value.trim()) ||
  (Boolean(attachedCapability.value) && (props.toolSubmissionDisabled || toolUnavailable.value || connectorSelectionMissing.value)) ||
  (isS3ReadTool.value && !activeResourceBindings.value.some((binding) => binding.binding_id === selectedStorageBindingId.value)) ||
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
  selectedStorageBindingId.value = activeResourceBindings.value.length === 1
    ? activeResourceBindings.value[0].binding_id
    : null
}

function removeTool() {
  attachedCapability.value = null
  maxResults.value = 5
  selectedConnectorId.value = null
  selectedStorageBindingId.value = null
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
  if (!text && !isS3ReadTool.value) return

  pendingDraft = {
    content: content.value,
    capability: attachedCapability.value ? { ...attachedCapability.value } : null,
    maxResults: maxResults.value,
    automaticToolMode: automaticToolMode.value,
    connectorId: selectedConnectorId.value,
    resourceBindingIds: [...selectedResourceBindingIds.value],
  }
  if (attachedCapability.value) {
    const connectorSelections: ConnectorSelections = {}
    if (attachedConnectorKind.value && selectedConnectorId.value) {
      connectorSelections[attachedConnectorKind.value] = selectedConnectorId.value
    }
    const command: Omit<ToolCommandRequest, 'client_request_id'> = isS3ReadTool.value
      ? {
          capability: { ...attachedCapability.value },
          arguments: { resource_binding_id: selectedStorageBindingId.value },
        }
      : {
          capability: { ...attachedCapability.value },
          arguments: { query: text, max_results: maxResults.value },
        }
    if (!isS3ReadTool.value && props.connectors !== undefined) command.connector_selections = connectorSelections
    emit('search', command)
  } else {
    if (automaticToolMode.value) {
      const resourceBindingSelections = selectedResourceBindingIds.value.filter((id) =>
        activeResourceBindings.value.some((binding) => binding.binding_id === id),
      )
      emit('send', text, {
        enableTools: true,
        ...(resourceBindingSelections.length ? { resourceBindingSelections } : {}),
      })
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
    selectedResourceBindingIds.value = pendingDraft.resourceBindingIds
    pendingDraft = null
    void nextTick(grow)
  },
)

watch(() => props.draftContent, (draft) => {
  if (!draft) return
  if (!content.value.trim()) {
    content.value = draft
    void nextTick(() => {
      grow()
      void focusInput()
    })
  }
  emit('draft-consumed')
})

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
      v-if="!isS3ReadTool"
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
        :resource-bindings="activeResourceBindings"
        :disabled="props.disabled"
        @attach="attachTool"
        @toggle-automatic="toggleAutomaticToolMode"
      />

      <label
        v-if="activeResourceBindings.length && !attachedCapability"
        class="inline-flex h-8 min-w-0 items-center gap-2 text-xs text-muted-foreground"
      >
        <span>S3 buckets</span>
        <select
          v-model="selectedResourceBindingIds"
          multiple
          aria-label="S3 buckets available to tools"
          data-testid="resource-binding-selection"
          class="h-8 max-w-64 rounded-md border border-input bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <option v-for="binding in activeResourceBindings" :key="binding.binding_id" :value="binding.binding_id">
            {{ binding.display_name }} · {{ binding.environment_name }}
          </option>
        </select>
      </label>

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
        <select
          v-if="isS3ReadTool"
          v-model="selectedStorageBindingId"
          data-testid="storage-binding-selection"
          aria-label="AWS resource binding"
          class="h-6 max-w-52 rounded-full border-0 bg-transparent px-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.disabled"
        >
          <option :value="null">Choose an S3 bucket</option>
          <option v-for="binding in activeResourceBindings" :key="binding.binding_id" :value="binding.binding_id">
            {{ binding.display_name }} · {{ binding.environment_name }}
          </option>
        </select>
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
          <span v-else>{{ attachedCapability?.id === 'aws_s3_bucket_storage' ? 'Measure' : attachedCapability?.id === 'aws_s3_bucket_metadata' ? 'Inspect' : attachedCapability ? 'Search' : 'Send' }}</span>
        </Button>
      </div>
    </div>
  </form>
</template>
