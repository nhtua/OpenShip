<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import type { RegisterRequest } from '@/types'

const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const localError = ref<string | null>(null)

function validate(): RegisterRequest | null {
  localError.value = null

  if (!username.value.trim()) {
    localError.value = 'Username is required'
    return null
  }

  if (!email.value.trim() || !/\S+@\S+\.\S+/.test(email.value)) {
    localError.value = 'A valid email is required'
    return null
  }

  if (password.value.length < 8) {
    localError.value = 'Password must be at least 8 characters'
    return null
  }

  if (password.value !== confirmPassword.value) {
    localError.value = 'Passwords do not match'
    return null
  }

  return {
    username: username.value.trim(),
    email: email.value.trim(),
    password: password.value,
  }
}

async function handleRegister() {
  const data = validate()
  if (!data) return

  const result = await auth.register(data)
  if (result.success) {
    router.push('/')
  } else {
    localError.value = result.error ?? auth.error
  }
}
</script>

<template>
  <div class="flex min-h-[60vh] items-center justify-center">
    <div class="w-full max-w-md space-y-6 rounded-lg border bg-card p-8 shadow-sm">
      <div class="space-y-2 text-center">
        <h2 class="text-2xl font-bold">Create Account</h2>
        <p class="text-sm text-muted-foreground">
          Sign up to get started with OpenShip
        </p>
      </div>

      <div
        v-if="localError || auth.error"
        class="rounded-md bg-destructive/15 p-3 text-sm text-destructive"
      >
        {{ localError ?? auth.error }}
      </div>

      <form @submit.prevent="handleRegister" class="space-y-4">
        <div class="space-y-2">
          <label class="text-sm font-medium" for="username">Username</label>
          <input
            id="username"
            name="username"
            v-model="username"
            type="text"
            placeholder="choose a username"
            class="flex w-full rounded-md border bg-background px-3 py-2 text-sm"
            required
          />
        </div>
        <div class="space-y-2">
          <label class="text-sm font-medium" for="email">Email</label>
          <input
            id="email"
            name="email"
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
            name="password"
            v-model="password"
            type="password"
            placeholder="••••••••"
            class="flex w-full rounded-md border bg-background px-3 py-2 text-sm"
            required
          />
        </div>
        <div class="space-y-2">
          <label class="text-sm font-medium" for="confirmPassword">
            Confirm Password
          </label>
          <input
            id="confirmPassword"
            name="confirmPassword"
            v-model="confirmPassword"
            type="password"
            placeholder="••••••••"
            class="flex w-full rounded-md border bg-background px-3 py-2 text-sm"
            required
          />
        </div>
        <button
          type="submit"
          :disabled="auth.isLoading"
          class="inline-flex w-full items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
        >
          {{ auth.isLoading ? 'Creating account...' : 'Create Account' }}
        </button>
      </form>
      <p class="text-center text-sm text-muted-foreground">
        Already have an account?
        <RouterLink to="/login" class="text-primary underline">
          Sign In
        </RouterLink>
      </p>
    </div>
  </div>
</template>
