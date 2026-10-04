<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <div :class="cn('mt-2 flex flex-col gap-2 px-3', props.class)">
    <slot />
  </div>
</template>
