<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <div :class="cn('[&:not(:last-child)]:border-b', props.class)">
    <slot />
  </div>
</template>
