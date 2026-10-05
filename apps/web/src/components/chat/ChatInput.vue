<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { Button, Icon, Label, Textarea } from '@/components/ui'

const props = withDefaults(defineProps<{ disabled?: boolean }>(), {
  disabled: false,
})

const emit = defineEmits<{
  send: [content: string]
}>()

const content = ref('')
const textareaRef = ref<InstanceType<typeof Textarea> | null>(null)

// Focus input on mount so user can start typing immediately
onMounted(() => {
  focusInput()
})

// Re-focus input when streaming completes (disabled → enabled)
watch(
  () => props.disabled,
  (wasDisabled, isDisabled) => {
    if (wasDisabled && !isDisabled) {
      void focusInput()
    }
  },
)

// Grow to content, capped at 192px (max-h-48), then scroll internally.
function grow() {
  const el = textareaRef.value?.$el as HTMLElement | undefined
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 192)}px`
}

async function focusInput() {
  await nextTick()
  const el = textareaRef.value?.$el as HTMLElement | undefined
  if (el) {
    el.focus()
  }
}

async function handleSend() {
  if (props.disabled) return
  const text = content.value.trim()
  if (!text) return
  emit('send', text)
  content.value = ''
  await nextTick()
  // Re-focus input after sending so user can continue typing
  focusInput()
  grow()
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey) return
  // IME composition (event.isComposing, keyCode 229 compatibility) never
  // submits; the composing Enter confirms the composition instead.
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void handleSend()
}
</script>

<template>
  <form
    class="shrink-0 border-t border-border bg-card p-4"
    data-testid="composer"
    @submit.prevent="handleSend"
  >
    <div class="flex items-end gap-3">
      <div class="min-w-0 flex-1">
        <Label for="message" class="sr-only">Message</Label>
        <Textarea
          ref="textareaRef"
          v-model="content"
          id="message"
          name="message"
          rows="1"
          :disabled="props.disabled"
          placeholder="Message the agent..."
          class="min-h-[38px] max-h-48 resize-none overflow-y-auto py-2"
          @keydown="handleKeydown"
          @input="grow"
        />
      </div>
      <Button
        type="submit"
        :disabled="props.disabled || !content.trim()"
        class="h-[38px] shrink-0"
      >
        <Icon name="send" />
        {{ props.disabled ? 'Streaming...' : 'Send' }}
      </Button>
    </div>
    <p class="mt-1 text-xs text-muted-foreground">
      Enter to send · Shift+Enter for a new line
    </p>
  </form>
</template>
