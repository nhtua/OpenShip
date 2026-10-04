<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <div
    :class="
      cn(
        'flex h-full w-full flex-col group/sidebar\\:data-[collapsible=icon]\\:overflow-hidden',
        props.class,
      )
    "
  >
    <slot />
  </div>
</template>
