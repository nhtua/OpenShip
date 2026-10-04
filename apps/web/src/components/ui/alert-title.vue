<script setup lang="ts">
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

const alertTitleVariants = cva('mb-1 font-medium leading-none tracking-tight', {
  variants: {},
  defaultVariants: {},
})

interface Props {
  class?: HTMLAttributes['class']
}

const props = defineProps<Props>()
</script>

<template>
  <h5 :class="cn(alertTitleVariants(), props.class)">
    <slot />
  </h5>
</template>
