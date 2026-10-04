<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
  variant?: 'default' | 'destructive'
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'default',
})

const variantClasses: Record<string, string> = {
  default: 'border-background/5 bg-background text-foreground',
  destructive: 'border-destructive/50 text-destructive dark:border-destructive [&>svg]:text-destructive',
}
</script>

<template>
  <div
    :class="
      cn(
        'relative w-full rounded-lg border px-4 py-3 text-sm [&>svg+div]:translate-y-[-3px] [&>svg]:absolute [&>svg]:left-4 [&>svg]:top-4 [&>svg]:text-foreground',
        variantClasses[variant],
        props.class,
      )
    "
    role="alert"
  >
    <slot />
  </div>
</template>
