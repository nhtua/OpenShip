<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <div :class="cn('flex flex-1 flex-col gap-2 overflow-auto py-4', props.class)">
    <slot />
  </div>
</template>
