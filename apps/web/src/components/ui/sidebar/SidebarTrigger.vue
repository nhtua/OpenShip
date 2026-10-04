<script setup lang="ts">
import type { HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'
import Button from '@/components/ui/button.vue'
import Icon from '@/components/common/Icon.vue'
import { useSidebar } from './utils'

const props = defineProps<{
  class?: HTMLAttributes['class']
}>()

const { toggleSidebar } = useSidebar()
</script>

<template>
  <Button
    data-sidebar="trigger"
    data-slot="sidebar-trigger"
    variant="ghost"
    size="icon"
    :class="cn('h-7 w-7', props.class)"
    aria-label="Toggle Sidebar"
    @click="toggleSidebar"
  >
    <Icon name="bars" />
    <span class="sr-only">Toggle Sidebar</span>
  </Button>
</template>
