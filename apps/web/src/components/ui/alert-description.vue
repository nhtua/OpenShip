<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <p :class="cn('text-sm [&_p]:leading-relaxed', props.class)">
    <slot />
  </p>
</template>
