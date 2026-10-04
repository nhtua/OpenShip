<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'
import type { PrimeIconName } from './icons'

const props = defineProps<{
  name: PrimeIconName
  class?: HTMLAttributes['class']
}>()
</script>

<template>
  <!-- Decorative glyph: the surrounding control owns the accessible name. -->
  <i
    :class="cn('pi', 'pi-' + props.name, 'text-base leading-none shrink-0', props.class)"
    aria-hidden="true"
  />
</template>
