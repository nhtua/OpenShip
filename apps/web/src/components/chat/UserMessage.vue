<script setup lang="ts">
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
}>()
</script>

<template>
  <!-- Right-aligned user bubble, semantic primary tint (plan Task 6.6). -->
  <div class="message flex min-w-0 justify-end" data-testid="bubble-user">
    <div
      class="min-w-0 max-w-full rounded-lg border border-primary/30 bg-primary/15 px-4 py-3 md:max-w-2xl"
    >
      <p
        class="min-w-0 max-w-full whitespace-pre-wrap text-sm [overflow-wrap:anywhere]"
      >
        {{ message.content }}
      </p>
    </div>
  </div>
</template>
