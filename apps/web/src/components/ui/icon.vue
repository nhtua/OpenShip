<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

export const PRIME_ICON_NAMES = [
  'comments', 'plus', 'user', 'lock', 'envelope', 'sign-out',
  'bars', 'chevron-left', 'chevron-right', 'times',
  'sun', 'moon', 'spinner', 'info-circle',
  'arrow-down', 'send',
] as const

export type PrimeIconName = (typeof PRIME_ICON_NAMES)[number]

interface Props {
  name: PrimeIconName
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <i
    :class="cn('pi pi-' + name, 'text-base leading-none shrink-0', props.class)"
    aria-hidden="true"
  />
</template>
