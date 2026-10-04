<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <div :class="cn('px-4 py-2 text-xs text-muted-foreground', props.class)">
    <slot />
  </div>
</template>
