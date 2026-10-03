<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

function handleLogout() {
  auth.logout()
}
</script>

<template>
  <div class="flex min-h-[60vh] items-center justify-center">
    <div class="space-y-4 text-center">
      <h2 class="text-2xl font-bold">Chat</h2>
      <p class="text-muted-foreground">
        Select or create a conversation to start chatting.
      </p>
      <button
        v-if="auth.user"
        @click="handleLogout"
        class="inline-flex items-center justify-center rounded-md border px-4 py-2 text-sm font-medium transition-colors hover:bg-muted"
      >
        Sign Out ({{ auth.user.username }})
      </button>
    </div>
  </div>
</template>
