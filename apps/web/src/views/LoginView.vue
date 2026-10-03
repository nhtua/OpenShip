<script setup lang="ts">
import { ref } from 'vue'

const email = ref('')
const password = ref('')

function handleLogin() {
  console.log('Login attempt:', email.value)
}
</script>

<template>
  <div class="flex min-h-[60vh] items-center justify-center">
    <div class="w-full max-w-md space-y-6 rounded-lg border bg-card p-8 shadow-sm">
      <div class="space-y-2 text-center">
        <h2 class="text-2xl font-bold">Sign In</h2>
        <p class="text-sm text-muted-foreground">
          Enter your credentials to access your account
        </p>
      </div>
      <form @submit.prevent="handleLogin" class="space-y-4">
        <div class="space-y-2">
          <label class="text-sm font-medium" for="email">Email</label>
          <input
            id="email"
            v-model="email"
            type="email"
            placeholder="you@example.com"
            class="flex w-full rounded-md border bg-background px-3 py-2 text-sm"
            required
          />
        </div>
        <div class="space-y-2">
          <label class="text-sm font-medium" for="password">Password</label>
          <input
            id="password"
            v-model="password"
            type="password"
            placeholder="••••••••"
            class="flex w-full rounded-md border bg-background px-3 py-2 text-sm"
            required
          />
        </div>
        <button
          type="submit"
          class="inline-flex w-full items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          Sign In
        </button>
      </form>
      <p class="text-center text-sm text-muted-foreground">
        Don't have an account?
        <RouterLink to="/register" class="text-primary underline">
          Register
        </RouterLink>
      </p>
    </div>
  </div>
</template>
