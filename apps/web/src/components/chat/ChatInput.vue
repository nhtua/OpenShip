<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  send: [content: string]
}>()

defineProps<{
  disabled?: boolean
}>()

const input = ref('')

function handleSend() {
  const content = input.value.trim()
  if (!content) return
  emit('send', content)
  input.value = ''
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="border-t bg-card p-4">
    <form @submit.prevent="handleSend" class="mx-auto flex gap-2">
      <input
        v-model="input"
        type="text"
        placeholder="Type a message..."
        :disabled="disabled"
        @keydown="handleKeydown"
        class="flex-1 rounded-lg border border-input bg-background px-4 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
      />
      <button
        type="submit"
        :disabled="disabled || !input.trim()"
        class="inline-flex items-center justify-center rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span v-if="!disabled">Send</span>
        <span v-else class="inline-flex items-center gap-2">
          <svg
            class="h-4 w-4 animate-spin"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              class="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              stroke-width="4"
            ></circle>
            <path
              class="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            ></path>
          </svg>
          Streaming...
        </span>
      </button>
    </form>
  </div>
</template>
