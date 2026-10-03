<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  disabled: boolean
}>()

const emit = defineEmits<{
  send: [content: string]
}>()

const content = ref('')

function handleSend() {
  const text = content.value.trim()
  if (text && !props.disabled) {
    emit('send', text)
    content.value = ''
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="border-t border-[#30363d] p-4 bg-[#161b22]">
    <div class="flex gap-3">
      <textarea
        v-model="content"
        :disabled="disabled"
        placeholder="Adjust parameters, ask questions, or approve..."
        class="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:ring-2 focus:ring-[#58a6ff] resize-none"
        rows="1"
        @keydown="handleKeydown"
      ></textarea>
      <button
        @click="handleSend"
        :disabled="disabled || !content.trim()"
        class="flex items-center gap-2 bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <i class="pi pi-send"></i>
        Send
      </button>
    </div>
  </div>
</template>