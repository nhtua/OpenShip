<script setup lang="ts">
import { cn } from '@/lib/utils'
import { Tooltip as RekaTooltip, TooltipContent, TooltipProvider, TooltipTrigger } from 'reka-ui'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
  content: string
  side?: 'top' | 'right' | 'bottom' | 'left'
  delay?: number
}

const props = withDefaults(defineProps<Props>(), {
  side: 'top',
  delay: 0,
})
</script>

<template>
  <TooltipProvider :delayDuration="delay">
    <Tooltip>
      <TooltipTrigger as-child>
        <slot />
      </TooltipTrigger>
      <TooltipContent
        :side="side"
        :class="cn('z-50 overflow-hidden rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground animate-in fade-in-0 zoom-in-95', props.class)"
      >
        {{ content }}
      </TooltipContent>
    </Tooltip>
  </TooltipProvider>
</template>
