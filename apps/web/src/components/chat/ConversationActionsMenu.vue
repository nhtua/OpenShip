<script setup lang="ts">
import { ref } from 'vue'
import {
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRoot,
  DropdownMenuTrigger,
} from 'reka-ui'
import Icon from '@/components/common/Icon.vue'

const props = defineProps<{
  conversationId: string
  title: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  archive: [id: string]
}>()

const open = ref(false)
const trigger = ref<HTMLButtonElement | null>(null)

function restoreTriggerFocus(event: Event): void {
  event.preventDefault()
  trigger.value?.focus()
}

function archive(): void {
  open.value = false
  emit('archive', props.conversationId)
}
</script>

<template>
  <span class="contents">
    <DropdownMenuRoot v-model:open="open">
      <DropdownMenuTrigger as-child>
        <button
          ref="trigger"
          type="button"
          :disabled="props.disabled"
          :aria-label="`Conversation actions for ${props.title}`"
          :title="`Conversation actions for ${props.title}`"
          class="conversation-actions-trigger inline-flex size-8 shrink-0 items-center justify-center rounded-md text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring disabled:pointer-events-none disabled:opacity-50 md:opacity-0 md:group-hover/conv:opacity-100 md:group-focus-within/conv:opacity-100"
        >
          <Icon name="ellipsis-v" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        side="bottom"
        :side-offset="4"
        class="z-50 min-w-40 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md outline-none"
        @close-auto-focus="restoreTriggerFocus"
      >
        <DropdownMenuItem
          class="flex cursor-default select-none items-center rounded-sm px-2 py-1.5 text-sm text-destructive outline-none focus:bg-accent focus:text-destructive data-[disabled]:pointer-events-none data-[disabled]:opacity-50"
          @select="archive"
        >
          Archive
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenuRoot>
  </span>
</template>

<style scoped>
@media (pointer: coarse) {
  .conversation-actions-trigger {
    opacity: 1;
  }
}
</style>
