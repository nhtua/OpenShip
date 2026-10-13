<script setup lang="ts">
import { computed } from 'vue'
import { Badge, Button } from '@/components/ui'
import type { ModelProfileItem } from '@/types'

interface Props {
  profiles: ModelProfileItem[]
  defaultProfileId: string | null
  selectedProfileId: string | null
  fallbackIds: string[]
  // A catalog that could not be read. Stated here rather than as a page-wide
  // failure: the installation still answers with its default.
  catalogError: string | null
  // True only while a submission is in flight: that turn's provider is
  // already fixed, so a change here would describe a different turn.
  changeDisabled: boolean
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

// Which row the person reading this is on. A selection that is no longer
// offered is kept visible rather than dropped quietly: the next turn would
// otherwise be answered by something the reader did not choose.
const selected = computed<ModelProfileItem | null>(() => {
  const id = props.selectedProfileId
  if (!id) return null
  return props.profiles.find((p) => p.profile_id === id) ?? null
})

const staleSelection = computed(
  () => Boolean(props.selectedProfileId) && selected.value === null,
)

// The profile that answers when nothing is chosen. Naming the default is
// what makes "no selection" a claim a reader can check, rather than a
// guess about this installation.
const effective = computed<ModelProfileItem | null>(() => {
  if (props.selectedProfileId) return selected.value
  return props.profiles.find(
    (p) => p.profile_id === props.defaultProfileId,
  ) ?? null
})

// Destinations this selection may fall back to. Only ones the chosen
// profile itself allows are offered, so an opt-in is never a request the
// server would refuse.
const fallbackChoices = computed(() => {
  const allowed = effective.value?.allowed_fallback_profile_ids ?? []
  return props.profiles.filter((p) => allowed.includes(p.profile_id))
})

function chooseProfile(profileId: string) {
  if (props.changeDisabled) return
  // Fallbacks the new profile does not allow are dropped rather than kept
  // as a promise this profile cannot keep.
  const allowed =
    props.profiles.find((p) => p.profile_id === profileId)
      ?.allowed_fallback_profile_ids ?? []
  const kept = props.fallbackIds.filter((id) => allowed.includes(id))
  emit('select', profileId, kept)
}

function toggleFallback(profileId: string) {
  if (props.changeDisabled) return
  const chosen = props.fallbackIds.includes(profileId)
  const next = chosen
    ? props.fallbackIds.filter((id) => id !== profileId)
    : [...props.fallbackIds, profileId]
  emit('select', props.selectedProfileId, next)
}
</script>

<template>
  <div data-testid="provider-selector" class="flex gap-2">
    <!-- What the next turn will use, stated once and plainly. -->
    <div class="flex items-center gap-1">
      <span class="text-xs text-muted-foreground">Answering with</span>
      <Badge v-if="catalogError" variant="destructive" class="h-5 text-[10px]">
        Catalog unavailable
      </Badge>
      <span v-if="catalogError" class="text-xs text-muted-foreground">
        {{ catalogError }}
      </span>
      <Badge v-else-if="effective" variant="default" class="h-5 text-[10px]">
        {{ effective.label }}
      </Badge>
      <Badge v-else-if="staleSelection" variant="destructive" class="h-5 text-[10px]">
        Needs replacement
      </Badge>
      <Badge v-else variant="outline" class="h-5 text-[10px]">
        Not listed
      </Badge>
    </div>

    <!-- Every profile this installation offers, in the order it offered
         them. One this build cannot serve is still shown, with the reason
         it needs — so the browser can act instead of guess. -->
    <div class="flex gap-1">
      <div
        v-for="profile in profiles"
        :key="profile.profile_id"
        data-testid="provider-option"
        class="flex items-center gap-2"
      >
        <Button
          v-if="profile.available"
          size="sm"
          :disabled="props.changeDisabled"
          @click="chooseProfile(profile.profile_id)"
          class="h-5"
        >
          {{ profile.label }}
          <span
            v-if="effective?.profile_id === profile.profile_id"
            class="text-[10px]"
          >(chosen)</span>
        </Button>
        <span v-else class="text-xs text-muted-foreground">
          {{ profile.label }} — {{ profile.explanation }}
        </span>
        <Badge
          v-if="profile.destination_label"
          variant="secondary"
          class="h-4 text-[9px]"
        >
          {{ profile.destination_label }}
        </Badge>
      </div>
    </div>

    <!-- Fallback opt-in: the destinations a next turn may fall back to,
         and nothing else. Each control names what it will do. -->
    <div
      v-if="fallbackChoices.length"
      class="flex gap-1 text-[10px] text-muted-foreground"
    >
      <span>Falls back to</span>
      <Button
        v-for="profile in fallbackChoices"
        :key="profile.profile_id"
        size="sm"
        :disabled="props.changeDisabled"
        @click="toggleFallback(profile.profile_id)"
        class="h-5"
      >
        {{
          fallbackIds.includes(profile.profile_id)
            ? `Stop falling back to ${profile.label}`
            : `Fall back to ${profile.label}`
        }}
      </Button>
    </div>
  </div>
</template>
