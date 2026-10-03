<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const username = ref('')
const password = ref('')
const error = ref('')

async function handleLogin() {
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    router.push('/')
  } catch (err: unknown) {
    const axiosErr = err as { response?: { data?: { detail?: string } } }
    error.value = axiosErr.response?.data?.detail ?? 'Login failed'
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-[#0d1117]">
    <div class="w-full max-w-md bg-[#161b22] border border-[#30363d] rounded-lg shadow-xl p-8">
      <div class="text-center mb-8">
        <div class="w-12 h-12 bg-[#58a6ff] rounded-lg flex items-center justify-center mx-auto mb-4">
          <i class="pi pi-anchor text-white text-xl"></i>
        </div>
        <h1 class="text-2xl font-bold text-[#e6edf3]">OpenShip</h1>
        <p class="text-[#8b949e] text-sm mt-2">Agentic DevOps Co-Pilot</p>
      </div>

      <form @submit.prevent="handleLogin">
        <div class="mb-4">
          <label class="block text-sm font-medium text-[#e6edf3] mb-2">Username</label>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-[#8b949e]">
              <i class="pi pi-user"></i>
            </span>
            <input
              v-model="username"
              type="text"
              placeholder="Your username"
              class="w-full bg-[#0d1117] border border-[#30363d] rounded-lg pl-10 pr-3 py-2 text-sm text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
            />
          </div>
        </div>

        <div class="mb-6">
          <label class="block text-sm font-medium text-[#e6edf3] mb-2">Password</label>
          <div class="relative">
            <span class="absolute inset-y-0 left-0 flex items-center pl-3 text-[#8b949e]">
              <i class="pi pi-lock"></i>
            </span>
            <input
              v-model="password"
              type="password"
              placeholder="Your password"
              class="w-full bg-[#0d1117] border border-[#30363d] rounded-lg pl-10 pr-3 py-2 text-sm text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
            />
          </div>
        </div>

        <p v-if="error" class="text-sm text-[#f85149] mb-4 bg-[#da3633]/10 border border-[#da3633]/30 rounded-lg px-3 py-2">
          <i class="pi pi-info-circle mr-2"></i>{{ error }}
        </p>

        <button
          type="submit"
          class="w-full bg-[#238636] hover:bg-[#2ea043] text-white font-medium py-2 rounded-lg transition-colors"
        >
          Sign In
        </button>
      </form>

      <div class="mt-6 text-center">
        <p class="text-sm text-[#8b949e]">
          Don't have an account?
          <router-link to="/register" class="text-[#58a6ff] hover:text-[#79b8ff]">Sign up</router-link>
        </p>
      </div>
    </div>
  </div>
</template>