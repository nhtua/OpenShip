<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Button } from '@/components/ui'

const auth = useAuthStore()
const router = useRouter()

const username = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const localError = ref<string | null>(null)
const isSubmitting = ref(false)

function validate(): boolean {
  localError.value = null
  const trimmed = username.value.trim()
  const emailTrimmed = email.value.trim()
  const pass = password.value.trim()
  const confirmPass = confirmPassword.value.trim()

  if (!trimmed) {
    localError.value = 'Username is required'
    return false
  }
  if (!emailTrimmed || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailTrimmed)) {
    localError.value = 'A valid email is required'
    return false
  }
  if (pass.length < 8) {
    localError.value = 'Password must be at least 8 characters'
    return false
  }
  if (pass !== confirmPass) {
    localError.value = 'Passwords do not match'
    return false
  }
  return true
}

async function handleRegister() {
  if (!validate()) return
  if (auth.isLoading || isSubmitting.value) return
  isSubmitting.value = true

  const result = await auth.register({
    username: username.value.trim(),
    email: email.value.trim(),
    password: password.value.trim(),
  })

  if (result.success) {
    router.push({ name: 'chat' })
  } else {
    localError.value = result.error ?? auth.error ?? 'Registration failed'
  }

  isSubmitting.value = false
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-background">
    <div class="w-full max-w-md bg-card border border-input rounded-lg shadow-lg p-6 md:p-8">
      <div class="text-center mb-8">
        <div class="w-12 h-12 bg-primary rounded-lg flex items-center justify-center mx-auto mb-4">
          <i class="pi pi-comments text-primary-foreground text-xl" />
        </div>
        <h2 class="text-2xl font-bold text-foreground">Create Account</h2>
        <p class="text-muted-foreground text-sm mt-2">Join OpenShip</p>
      </div>

      <form @submit.prevent="handleRegister">
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
              placeholder="Choose a username"
              autocomplete="username"
              required
              :aria-invalid="!username.trim()"
              :aria-describedby="!username.trim() ? 'username-error' : undefined"
              class="w-full bg-background border border-input rounded-md pl-10 pr-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </div>

        <div class="mb-4">
          <label class="block text-sm font-medium text-foreground mb-2" for="email">Email</label>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-muted-foreground">
              <i class="pi pi-envelope" />
            </span>
            <input
              id="email"
              v-model="email"
              type="email"
              name="email"
              placeholder="your@email.com"
              autocomplete="email"
              required
              :aria-invalid="!email.trim()"
              :aria-describedby="!email.trim() ? 'email-error' : undefined"
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
              placeholder="At least 8 characters"
              autocomplete="new-password"
              required
              :aria-invalid="password.trim().length > 0 && password.trim().length < 8"
              :aria-describedby="password.trim().length > 0 && password.trim().length < 8 ? 'password-error' : undefined"
              class="w-full bg-background border border-input rounded-md pl-10 pr-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </div>

        <div class="mb-4">
          <label class="block text-sm font-medium text-foreground mb-2" for="confirmPassword">Confirm Password</label>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-muted-foreground">
              <i class="pi pi-lock" />
            </span>
            <input
              id="confirmPassword"
              v-model="confirmPassword"
              type="password"
              name="confirmPassword"
              placeholder="Confirm your password"
              autocomplete="new-password"
              required
              :aria-invalid="confirmPassword.trim().length > 0 && confirmPassword.trim() !== password.trim()"
              :aria-describedby="confirmPassword.trim().length > 0 && confirmPassword.trim() !== password.trim() ? 'confirm-error' : undefined"
              class="w-full bg-background border border-input rounded-md pl-10 pr-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </div>

        <div v-if="localError || auth.error" role="alert" class="text-sm text-destructive mb-4 bg-destructive/10 border border-destructive/30 rounded-md px-3 py-2">
          <i class="pi pi-info-circle mr-2" />{{ localError ?? auth.error }}
        </div>

        <Button
          type="submit"
          class="w-full"
          :disabled="auth.isLoading || isSubmitting"
        >
          <span v-if="auth.isLoading || isSubmitting">Creating account...</span>
          <span v-else>Create Account</span>
        </Button>
      </form>

      <div class="mt-6 text-center">
        <p class="text-sm text-muted-foreground">
          Already have an account?
          <router-link to="/login" class="text-primary hover:text-primary/90 underline-offset-4 hover:underline">Sign in</router-link>
        </p>
      </div>
    </div>
  </div>
</template>
