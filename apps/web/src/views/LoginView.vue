<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const username = ref('')
const password = ref('')
const localError = ref<string | null>(null)
const isSubmitting = ref(false)

async function handleLogin() {
  localError.value = null
  const trimmedUsername = username.value.trim()
  const trimmedPassword = password.value.trim()

  if (!trimmedUsername || !trimmedPassword) {
    localError.value = 'Username and password are required'
    return
  }

  if (auth.isLoading || isSubmitting.value) return
  isSubmitting.value = true

  const result = await auth.login({ username: trimmedUsername, password: trimmedPassword })
  if (result.success) {
    router.push({ name: 'chat' })
  } else {
    localError.value = result.error ?? auth.error ?? 'Login failed'
  }

  isSubmitting.value = false
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-background">
    <div class="w-full max-w-md bg-card border border-input rounded-lg shadow-lg p-8">
      <div class="text-center mb-8">
        <div class="w-12 h-12 bg-primary rounded-lg flex items-center justify-center mx-auto mb-4">
          <i class="pi pi-comments text-primary-foreground text-xl" />
        </div>
        <h2 class="text-2xl font-bold text-foreground">Sign In</h2>
        <p class="text-muted-foreground text-sm mt-2">Agentic DevOps Co-Pilot</p>
      </div>

      <form @submit.prevent="handleLogin">
        <div class="mb-4">
          <label class="block text-sm font-medium text-foreground mb-2" for="username">Username</label>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-muted-foreground">
              <i class="pi pi-user" />
            </span>
            <input
              id="username"
              v-model="username"
              type="text"
              name="username"
              placeholder="Your username"
              autocomplete="username"
              required
              :aria-invalid="!username.trim()"
              :aria-describedby="!username.trim() ? 'username-error' : undefined"
              class="w-full bg-background border border-input rounded-md pl-10 pr-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </div>

        <div class="mb-6">
          <label class="block text-sm font-medium text-foreground mb-2" for="password">Password</label>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-muted-foreground">
              <i class="pi pi-lock" />
            </span>
            <input
              id="password"
              v-model="password"
              type="password"
              name="password"
              placeholder="Your password"
              autocomplete="current-password"
              required
              :aria-invalid="!password.trim()"
              :aria-describedby="!password.trim() ? 'password-error' : undefined"
              class="w-full bg-background border border-input rounded-md pl-10 pr-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </div>

        <div v-if="localError || auth.error" role="alert" class="text-sm text-destructive mb-4 bg-destructive/10 border border-destructive/30 rounded-md px-3 py-2">
          <i class="pi pi-info-circle mr-2" />{{ localError ?? auth.error }}
        </div>

        <button
          type="submit"
          :disabled="auth.isLoading || isSubmitting"
          class="w-full bg-primary text-primary-foreground rounded-md font-medium py-2 transition-colors hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <span v-if="auth.isLoading || isSubmitting">Signing in...</span>
          <span v-else>Sign In</span>
        </button>
      </form>

      <div class="mt-6 text-center">
        <p class="text-sm text-muted-foreground">
          Don't have an account?
          <router-link to="/register" class="text-primary hover:text-primary/90 underline-offset-4 hover:underline">Sign up</router-link>
        </p>
      </div>
    </div>
  </div>
</template>
