<script setup lang="ts">
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'
import { computed, type HTMLAttributes } from 'vue'

const buttonVariants = cva(
  'inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground shadow hover:bg-primary/90',
        destructive: 'bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90',
        outline: 'border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground',
        secondary: 'bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-9 px-4 py-2 gap-2',
        sm: 'h-8 rounded-md px-3 text-xs gap-1.5',
        lg: 'h-10 rounded-md px-8 gap-2.5',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
)

type ButtonVariants = VariantProps<typeof buttonVariants>

interface Props extends ButtonVariants {
  class?: HTMLAttributes['class']
  disabled?: boolean
  type?: 'button' | 'submit' | 'reset'
  'aria-label'?: string
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'default' as const,
  size: 'default' as const,
  disabled: false,
  type: 'button',
})
</script>

<template>
  <button
    :class="cn(buttonVariants({ variant, size }), props.class)"
    :disabled="disabled"
    :type="type"
  >
    <slot />
  </button>
</template>
