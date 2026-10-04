<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
  active?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  active: false,
})
</script>

<template>
  <button
    :class="
      cn(
        'flex w-full items-center gap-3 overflow-hidden rounded-md px-3 py-2 text-sm transition-colors hover:bg-accent hover:text-accent-foreground',
        { 'bg-accent text-accent-foreground': active },
        props.class,
      )
    "
  >
    <slot />
  </button>
</template>
