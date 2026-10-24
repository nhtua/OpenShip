<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Button, Icon, Label, Textarea } from '@/components/ui'
import ProviderSelector from '@/components/ProviderSelector.vue'
import ToolAttachmentMenu from './ToolAttachmentMenu.vue'
import type {
  CapabilityRef,
  CapabilitySummary,
  ConnectorSelections,
  ConnectorUsage,
  ManagedConnectorKind,
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
  defaultConnectorIds?: Partial<Record<ManagedConnectorKind, string>>
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
  draftContent: null,
})

const emit = defineEmits<{
  send: [content: string, options?: { enableTools: boolean; connectorSelections?: ConnectorSelections }]
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
  awsConnectorId: string | null
  bucketName: string
  bucketRegion: string
} | null = null
const selectedConnectorId = ref<string | null>(null)
const selectedAwsConnectorId = ref<string | null>(null)
const bucketName = ref('')
const bucketRegion = ref('')
const isS3ReadTool = computed(() => ['aws_s3_bucket_storage', 'aws_s3_bucket_metadata'].includes(attachedCapability.value?.id ?? ''))
const awsConnectorChoices = computed(() => (props.connectors ?? []).filter(
  (connector) => connector.kind === 'aws' && connector.status === 'configured',
))
const awsSelectionRequired = computed(() => props.capabilities.some((capability) =>
  ['aws_read_catalog', 'aws_read'].includes(capability.id) &&
  capability.permitted !== false && capability.enabled !== false &&
  capability.connector_readiness?.aws === 'ambiguous',
))
const awsSelectionValid = computed(() => awsConnectorChoices.value.some(
  (connector) => connector.id === selectedAwsConnectorId.value,
))

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
      ? 'Configure an eligible Connector for this project to enable tools.'
      : 'No tools are configured.'
  }
  const hasReadyCapability = props.capabilities.some((capability) =>
    capability.permitted !== false && capability.enabled !== false && capability.available !== false &&
    (capability.required_connector_kinds ?? []).every((kind) =>
      capability.connector_readiness?.[kind] === 'ready' ||
      (kind === 'aws' && awsSelectionRequired.value),
    ),
  )
  if (!hasReadyCapability) {
    if (props.capabilities.some((capability) =>
      (capability.required_connector_kinds ?? []).some((kind) =>
        (kind === 'brave' || kind === 'tavily') && capability.connector_readiness?.[kind] === 'ambiguous',
      ),
    )) {
      return 'Select a project Connector when more than one is eligible.'
    }
    return 'Choose a ready Connector for this project.'
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
// model and an available capability. Keep the choice stable after user changes.
watch(automaticToolDisabled, (disabled) => {
  if (!disabled && !automaticToolModeTouched.value) automaticToolMode.value = true
}, { immediate: true })

watch(awsConnectorChoices, (choices) => {
  if (!choices.some((connector) => connector.id === selectedAwsConnectorId.value)) {
    const defaultId = props.defaultConnectorIds.aws
    selectedAwsConnectorId.value = choices.some((connector) => connector.id === defaultId)
      ? defaultId ?? null
      : choices.length === 1 ? choices[0].id : null
  }
}, { immediate: true })

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
const attachedConnectorKind = computed<ManagedConnectorKind | null>(() => {
  if (attachedCapability.value?.id === 'brave_web_search') return 'brave'
  if (attachedCapability.value?.id === 'tavily_web_search') return 'tavily'
  if (isS3ReadTool.value) return 'aws'
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
  attachedConnectorKind.value && (attachedConnectorKind.value === 'aws' || props.connectors !== undefined) &&
  !attachedConnectorChoices.value.some((connector) =>
    connector.id === (attachedConnectorKind.value === 'aws' ? selectedAwsConnectorId.value : selectedConnectorId.value),
  ),
))
const attachedConnectorId = computed(() =>
  attachedConnectorKind.value === 'aws' ? selectedAwsConnectorId.value : selectedConnectorId.value,
)
const submitDisabled = computed(() =>
  props.disabled || (!isS3ReadTool.value && !content.value.trim()) ||
  (Boolean(attachedCapability.value) && (props.toolSubmissionDisabled || toolUnavailable.value || connectorSelectionMissing.value)) ||
  (isS3ReadTool.value && !bucketName.value.trim()) ||
  (automaticToolMode.value && awsSelectionRequired.value && !awsSelectionValid.value) ||
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
      : ['aws_s3_bucket_storage', 'aws_s3_bucket_metadata'].includes(capability.id)
        ? 'aws'
        : null
  const choices = kind
    ? (props.connectors ?? []).filter((connector) => connector.kind === kind && connector.status === 'configured')
    : []
  const defaultId = kind ? props.defaultConnectorIds[kind] : undefined
  const selected = choices.some((connector) => connector.id === defaultId)
    ? defaultId ?? null
    : choices.length === 1 ? choices[0].id : null
  if (kind === 'aws') {
    selectedAwsConnectorId.value = selected
    bucketName.value = ''
    bucketRegion.value = ''
  } else {
    selectedConnectorId.value = selected
  }
}

function removeTool() {
  attachedCapability.value = null
  maxResults.value = 5
  selectedConnectorId.value = null
  bucketName.value = ''
  bucketRegion.value = ''
}

function updateAttachedConnector(event: Event) {
  const value = (event.target as HTMLSelectElement).value || null
  if (attachedConnectorKind.value === 'aws') selectedAwsConnectorId.value = value
  else selectedConnectorId.value = value
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
    awsConnectorId: selectedAwsConnectorId.value,
    bucketName: bucketName.value,
    bucketRegion: bucketRegion.value,
  }
  if (attachedCapability.value) {
    const connectorSelections: ConnectorSelections = {}
    if (attachedConnectorKind.value && selectedConnectorId.value) {
      connectorSelections[attachedConnectorKind.value] = selectedConnectorId.value
    }
    const command: Omit<ToolCommandRequest, 'client_request_id'> = isS3ReadTool.value
      ? {
          capability: { ...attachedCapability.value },
          arguments: {
            connector_id: selectedAwsConnectorId.value,
            bucket_name: bucketName.value.trim(),
            ...(bucketRegion.value.trim() ? { region: bucketRegion.value.trim() } : {}),
          },
          connector_selections: selectedAwsConnectorId.value ? { aws: selectedAwsConnectorId.value } : {},
        }
      : {
          capability: { ...attachedCapability.value },
          arguments: { query: text, max_results: maxResults.value },
        }
    if (!isS3ReadTool.value && props.connectors !== undefined) command.connector_selections = connectorSelections
    emit('search', command)
  } else {
    if (automaticToolMode.value) {
      const connectorSelections: ConnectorSelections = {}
      if (awsSelectionRequired.value && selectedAwsConnectorId.value) {
        connectorSelections.aws = selectedAwsConnectorId.value
      }
      emit('send', text, {
        enableTools: true,
        ...(Object.keys(connectorSelections).length ? { connectorSelections } : {}),
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
    selectedAwsConnectorId.value = pendingDraft.awsConnectorId
    bucketName.value = pendingDraft.bucketName
    bucketRegion.value = pendingDraft.bucketRegion
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

    <div v-if="isS3ReadTool" class="mt-2 grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(10rem,1fr)]" data-testid="aws-direct-target">
      <label class="grid gap-1 text-xs text-muted-foreground">
        S3 bucket name
        <input
          v-model="bucketName"
          data-testid="aws-bucket-name"
          aria-label="S3 bucket name"
          autocomplete="off"
          class="h-8 min-w-0 rounded-md border border-input bg-background px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.disabled"
        >
      </label>
      <label class="grid gap-1 text-xs text-muted-foreground">
        Region (optional)
        <input
          v-model="bucketRegion"
          data-testid="aws-bucket-region"
          aria-label="AWS region (optional)"
          autocomplete="off"
          placeholder="Use connector default"
          class="h-8 min-w-0 rounded-md border border-input bg-background px-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.disabled"
        >
      </label>
    </div>

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
        :title="automaticToolDisabledReason || 'The model may use available tools for this message.'"
        class="inline-flex h-8 items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-3 text-xs text-foreground hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="disableAutomaticToolMode"
      >
        <span>Automatic tools</span>
        <span aria-hidden="true">×</span>
      </button>

      <label
        v-if="automaticToolMode && awsSelectionRequired"
        class="inline-flex h-8 items-center gap-1 rounded-full border border-border px-2 text-xs text-muted-foreground"
      >
        <span>AWS</span>
        <select
          v-model="selectedAwsConnectorId"
          data-testid="automatic-aws-connector-selection"
          aria-label="AWS connector for automatic tools"
          class="h-6 max-w-40 rounded-full border-0 bg-transparent px-1 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.disabled"
        >
          <option :value="null">Choose a role</option>
          <option v-for="connector in awsConnectorChoices" :key="connector.id" :value="connector.id">
            {{ connector.name }}
          </option>
        </select>
      </label>

      <div
        v-if="attachedCapability"
        data-testid="attached-tool"
        class="inline-flex h-8 items-center gap-1 rounded-full border border-primary/30 bg-primary/10 pl-3 pr-1 text-xs text-foreground"
      >
        <span>{{ attachedLabel }}</span>
        <label class="sr-only" for="search-result-count">Number of results</label>
        <select
          v-if="attachedConnectorKind && props.connectors !== undefined"
          :value="attachedConnectorId"
          data-testid="connector-selection"
          :aria-label="attachedConnectorKind === 'aws' ? 'AWS connector' : 'Search connector'"
          class="h-6 max-w-40 rounded-full border-0 bg-transparent px-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :disabled="props.disabled"
          @change="updateAttachedConnector"
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
          v-if="!isS3ReadTool"
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
        This tool is unavailable. Remove it or configure its connection.
      </span>
      <span v-else-if="attachedCapability && props.toolSubmissionDisabled" class="text-xs text-muted-foreground">
        Wait for the current run to finish before searching.
      </span>
      <span v-else-if="automaticToolMode && automaticToolWarning" class="text-xs text-destructive">
        {{ automaticToolWarning }}
      </span>
      <span
        v-else-if="automaticToolMode && awsSelectionRequired && !awsSelectionValid"
        class="text-xs text-destructive"
      >
        Choose an AWS Connector for this conversation.
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
