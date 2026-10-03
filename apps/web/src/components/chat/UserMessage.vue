<script setup lang="ts">
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
}>()
</script>

<template>
  <div class="flex justify-end">
    <div class="max-w-2xl bg-[#238636]/20 border border-[#238636]/30 rounded-lg px-4 py-3">
      <p class="text-sm text-[#e6edf3]">{{ message.content }}</p>
    </div>
  </div>
</template>