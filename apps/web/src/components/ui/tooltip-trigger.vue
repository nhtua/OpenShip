<script setup lang="ts">
import type { TooltipTriggerProps } from 'reka-ui'
import { TooltipTrigger, useForwardProps } from 'reka-ui'

const props = defineProps<TooltipTriggerProps>()

const delegatedProps = useForwardProps(props)
</script>

<template>
  <TooltipTrigger v-bind="delegatedProps">
    <slot />
  </TooltipTrigger>
</template>
