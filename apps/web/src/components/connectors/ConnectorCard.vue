<script setup lang="ts">
import { computed } from 'vue'
import type { ConnectorUsage } from '@/types'
import {
  connectorStatusChipClass,
  connectorStatusDotClass,
  connectorStatusPresentation,
  type ConnectorCatalogEntry,
} from '@/lib/connectorCatalog'

const props = defineProps<{
  entry: ConnectorCatalogEntry
  connector?: ConnectorUsage
}>()

const emit = defineEmits<{
  select: [event: MouseEvent]
}>()

const presentation = computed(() => connectorStatusPresentation(props.connector))
const title = computed(() => props.connector?.name ?? props.entry.name)
const dotClass = computed(() => connectorStatusDotClass(presentation.value.status))
</script>

<template>
  <article
    :data-kind="entry.kind"
    :data-visibility="entry.visibility"
    :data-status="presentation.status"
    class="rounded-xl border border-border bg-card transition-colors hover:border-primary/50"
  >
    <button
      type="button"
      class="grid w-full gap-4 p-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[auto_1fr_auto] md:items-center"
      :aria-label="`Configure ${title}`"
      @click="emit('select', $event)"
    >
      <span
        aria-hidden="true"
        class="grid size-12 place-items-center rounded-xl bg-primary/10 text-lg font-semibold text-primary"
      >
        {{ entry.initials }}
      </span>
      <span class="grid gap-1">
        <span class="font-semibold">{{ title }}</span>
        <span class="text-xs text-muted-foreground">{{ entry.name }} · Published by {{ entry.publisher }}</span>
        <span class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span class="rounded-full border border-border px-2 py-0.5">Global</span>
          <span
            :class="['inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs', connectorStatusChipClass(presentation.status)]"
            :aria-label="`Status: ${presentation.label}`"
          >
            <span aria-hidden="true" :class="['size-1.5 rounded-full', dotClass]" />
            <span>{{ presentation.label }}</span>
            <span v-if="presentation.detail" class="capitalize">· {{ presentation.detail }}</span>
          </span>
        </span>
      </span>
      <span class="text-sm font-medium text-primary">View details</span>
    </button>
  </article>
</template>
