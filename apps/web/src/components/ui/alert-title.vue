<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <h5 :class="cn('mb-1 font-medium leading-none tracking-tight', props.class)">
    <slot />
  </h5>
</template>
