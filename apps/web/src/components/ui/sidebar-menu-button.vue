<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <div :class="cn('flex items-center gap-2 px-4 py-3 text-sm font-medium', props.class)">
    <slot />
  </div>
</template>
