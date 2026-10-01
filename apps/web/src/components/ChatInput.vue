<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  loading?: boolean
  disabled?: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  send: [content: string]
}>()

const input = ref('')

function handleSend() {
  const content = input.value.trim()
  if (!content || props.loading || props.disabled) return
  
  emit('send', content)
  input.value = ''
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="chat-input">
    <textarea
      v-model="input"
      :placeholder="placeholder || 'Type a message...'"
      :disabled="disabled"
      rows="1"
      @keydown="handleKeydown"
    ></textarea>
    <button
      class="send-btn"
      :disabled="!input.trim() || loading || disabled"
      @click="handleSend"
    >
      <svg v-if="!loading" width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <path d="M0 8L14 2v12L0 8zm1.5 0L12 4.5v7L1.5 8z"/>
      </svg>
      <svg v-else width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <circle cx="8" cy="8" r="3">
          <animateTransform attributeName="transform" type="rotate"
            values="0 8 8;360 8 8" dur="1s" repeatCount="indefinite"/>
        </circle>
      </svg>
    </button>
  </div>
</template>

<style>
.chat-input {
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
}

.chat-input textarea {
  flex: 1;
  padding: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--surface);
  font-family: inherit;
  font-size: 0.95rem;
  line-height: 1.5;
  resize: none;
  transition: border-color 0.2s;
}

.chat-input textarea:focus {
  outline: none;
  border-color: var(--primary);
}

.chat-input textarea:disabled {
  background: var(--bg);
  color: var(--text-muted);
}

.send-btn {
  width: 2.5rem;
  height: 2.5rem;
  border: none;
  border-radius: 50%;
  background: var(--primary);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.2s, transform 0.1s;
  flex-shrink: 0;
}

.send-btn:hover:not(:disabled) {
  background: var(--primary-hover);
}

.send-btn:active:not(:disabled) {
  transform: scale(0.95);
}

.send-btn:disabled {
  background: var(--text-muted);
  cursor: not-allowed;
  opacity: 0.5;
}
</style>