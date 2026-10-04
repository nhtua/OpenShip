<script setup lang="ts">
import type { PrimitiveProps } from 'reka-ui'
import type { HTMLAttributes } from 'vue'
import { Primitive } from 'reka-ui'
import { cn } from '@/lib/utils'

export interface SidebarMenuButtonProps extends PrimitiveProps {
  variant?: 'default' | 'outline'
  size?: 'default' | 'sm' | 'lg'
  isActive?: boolean
  class?: HTMLAttributes['class']
}

const props = withDefaults(defineProps<SidebarMenuButtonProps>(), {
  as: 'button',
  variant: 'default',
  size: 'default',
  isActive: false,
})

/**
 * Row geometry per the plan visual contract: 12px horizontal / 8px vertical
 * padding, 12px icon gap (upstream's p-2/gap-2 is intentionally overridden).
 */
const baseClasses = cn(
  'peer/menu-button flex w-full items-center gap-3 overflow-hidden rounded-md px-3 py-2 text-left text-sm outline-hidden ring-sidebar-ring transition-[width,height,padding]',
  'border-1 border-transparent focus-visible:ring-2 active:bg-sidebar-accent active:text-sidebar-accent-foreground',
  'disabled:pointer-events-none disabled:opacity-50 aria-disabled:pointer-events-none aria-disabled:opacity-50',
  'data-[active=true]:border-border data-[active=true]:bg-sidebar-accent data-[active=true]:font-medium data-[active=true]:text-sidebar-accent-foreground',
  'group-data-[collapsible=icon]:size-8! group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:p-2!',
  '[&>span:last-child]:truncate [&>i]:shrink-0',
)
</script>

<template>
  <Primitive
    data-slot="sidebar-menu-button"
    data-sidebar="menu-button"
    :data-size="props.size"
    :data-active="String(props.isActive)"
    :class="cn(baseClasses, props.class)"
    :as="props.as"
    :as-child="props.asChild"
    v-bind="$attrs"
  >
    <slot />
  </Primitive>
</template>
