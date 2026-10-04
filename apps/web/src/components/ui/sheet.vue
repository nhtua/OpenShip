<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'
import { Sheet, SheetContent, SheetOverlay } from 'reka-ui'

interface Props {
  class?: HTMLAttributes['class']
  side?: 'left' | 'right'
}

const props = withDefaults(defineProps<Props>(), {
  side: 'left',
})

const sideMap: Record<string, { class: string }> = {
  left: { class: 'left-0 right-auto' },
  right: { class: 'right-0 left-auto' },
}
</script>

<template>
  <Sheet>
    <SheetOverlay :class="cn('bg-background/80 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0', $attrs.class || '')" />
    <SheetContent
      :side="side"
      :class="cn('w-[280px] bg-background p-4 shadow-lg transition ease-in-out data-[state=closed]:duration-300 data-[state=open]:duration-500', sideMap[side].class, props.class)"
      v-bind="$attrs"
    >
      <slot />
    </SheetContent>
  </Sheet>
</template>
