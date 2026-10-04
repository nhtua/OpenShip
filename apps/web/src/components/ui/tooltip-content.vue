<script setup lang="ts">
import type { TooltipContentProps } from 'reka-ui'
import type { HTMLAttributes } from 'vue'
import { reactiveOmit } from '@vueuse/core'
import { TooltipContent, TooltipProvider, TooltipArrow } from 'reka-ui'
import { cn } from '@/lib/utils'

interface Props extends TooltipContentProps {
  class?: HTMLAttributes['class']
  sideOffset?: number
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
}

const props = withDefaults(defineProps<Props>(), {
  sideOffset: 4,
})

const delegatedProps = reactiveOmit(props, 'class')
</script>

<template>
  <TooltipProvider>
    <TooltipContent
      data-slot="tooltip-content"
      :class="
        cn(
          'bg-primary text-primary-foreground animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 z-50 w-fit rounded-md px-3 py-1.5 text-xs text-balance',
          props.class,
        )
      "
      v-bind="delegatedProps"
    >
      <slot />
      <TooltipArrow class="fill-primary" />
    </TooltipContent>
  </TooltipProvider>
</template>
