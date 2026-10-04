<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <div :class="cn('flex flex-1 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring', props.class)">
    <slot />
  </div>
</template>
