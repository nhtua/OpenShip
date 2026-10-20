<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Button, Label } from '@/components/ui'
import type { CapabilityRef, CapabilitySummary } from '@/types'

interface SearchCommandDraft {
  capability: CapabilityRef
  arguments: { query: string; max_results: number }
}

const props = withDefaults(
  defineProps<{ capabilities: CapabilitySummary[]; disabled?: boolean; catalogError?: string | null }>(),
  { capabilities: () => [], disabled: false, catalogError: null },
)

const emit = defineEmits<{ submit: [command: SearchCommandDraft] }>()

const query = ref('')
const maxResults = ref(5)
const selectedId = ref('')

watch(
  () => props.capabilities,
  (capabilities) => {
    if (!capabilities.some((item) => item.id === selectedId.value)) {
      selectedId.value = capabilities.find((item) => item.available)?.id ?? capabilities[0]?.id ?? ''
    }
  },
  { immediate: true, deep: true },
)

const selected = computed(() => props.capabilities.find((item) => item.id === selectedId.value) ?? null)
const canSubmit = computed(() => Boolean(selected.value?.available && query.value.trim() && !props.disabled))

function toolLabel(id: string): string {
  if (id === 'brave_web_search') return 'Brave Search'
  if (id === 'tavily_web_search') return 'Tavily Search'
  return id
}

const unavailableMessage = computed(() => {
  if (!selected.value || selected.value.available) return ''
  if (selected.value.unavailable_reason === 'missing_credentials') {
    return `${toolLabel(selected.value.id)} is not configured on this OpenShip installation.`
  }
  if (selected.value.unavailable_reason === 'disabled') {
    return `${toolLabel(selected.value.id)} is disabled on this OpenShip installation.`
  }
  return `${toolLabel(selected.value.id)} is temporarily unavailable.`
})

function submit() {
  const capability = selected.value
  const trimmed = query.value.trim()
  if (!capability?.available || !trimmed || props.disabled) return
  emit('submit', {
    capability: {
      id: capability.id,
      version: capability.version,
      manifest_digest: capability.manifest_digest,
    },
    arguments: { query: trimmed, max_results: maxResults.value },
  })
  query.value = ''
}
</script>

<template>
  <form
    class="shrink-0 border-t border-border bg-card/60 px-4 py-3 md:px-6"
    data-testid="search-tool-form"
    @submit.prevent="submit"
  >
    <div class="grid gap-3 md:grid-cols-[minmax(10rem,0.8fr)_minmax(16rem,2fr)_auto_auto] md:items-end">
      <div class="space-y-1.5">
        <Label for="search-service">Search service</Label>
        <select
          id="search-service"
          v-model="selectedId"
          aria-label="Search service"
          :disabled="props.disabled || props.capabilities.length === 0"
          class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
        >
          <option v-for="capability in props.capabilities" :key="capability.id" :value="capability.id">
            {{ toolLabel(capability.id) }}{{ capability.available ? '' : ' · unavailable' }}
          </option>
        </select>
      </div>
      <div class="space-y-1.5">
        <Label for="search-query">Search query</Label>
        <input
          id="search-query"
          v-model="query"
          aria-label="Search query"
          maxlength="512"
          autocomplete="off"
          :disabled="props.disabled || !selected?.available"
          placeholder="Search the web..."
          class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
        />
      </div>
      <div class="space-y-1.5">
        <Label for="search-count">Results</Label>
        <select
          id="search-count"
          v-model.number="maxResults"
          aria-label="Maximum results"
          :disabled="props.disabled"
          class="h-9 rounded-md border border-input bg-background px-3 text-sm"
        >
          <option v-for="count in [3, 5, 10]" :key="count" :value="count">{{ count }}</option>
        </select>
      </div>
      <Button type="submit" :disabled="!canSubmit" class="h-9">
        Search
      </Button>
    </div>
    <p v-if="unavailableMessage" class="mt-2 text-xs text-muted-foreground" role="status">
      {{ unavailableMessage }}
    </p>
    <p v-else-if="props.catalogError" class="mt-2 text-xs text-muted-foreground" role="status">
      {{ props.catalogError }}
    </p>
    <p v-else-if="props.capabilities.length === 0" class="mt-2 text-xs text-muted-foreground" role="status">
      Search tools are not available on this OpenShip installation.
    </p>
  </form>
</template>
