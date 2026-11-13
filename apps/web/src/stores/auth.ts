import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useAgentModeStore } from '@/stores/agentMode'
import { useTaskProgressStore } from '@/stores/taskProgress'
import { authApi } from '@/services/api'
import type { User, AuthResponse, LoginRequest, RegisterRequest } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref<string | null>(localStorage.getItem('access_token'))
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  function setUserFromStorage() {
    const stored = localStorage.getItem('access_token')
    if (stored) {
      token.value = stored
    }
  }

  async function login(loginData: LoginRequest) {
    isLoading.value = true
    error.value = null
    try {
      const res = await authApi.login(loginData.username, loginData.password)
      const data = res.data as unknown as AuthResponse
      token.value = data.access_token
      user.value = data.user
      localStorage.setItem('access_token', data.access_token)
      // A different account means a different execution mode: nothing the
      // previous account's preference may still return is kept.
      useAgentModeStore().reset()
      // A logout or an account switch also drops the task fold: a card read
      // for the previous account is never this account's story.
      useTaskProgressStore().reset()
      return { success: true }
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      const message =
        axiosErr.response?.data?.detail ?? 'Failed to login'
      error.value = message
      return { success: false, error: message }
    } finally {
      isLoading.value = false
    }
  }

  async function register(registerData: RegisterRequest) {
    isLoading.value = true
    error.value = null
    try {
      const res = await authApi.register(
        registerData.username,
        registerData.email,
        registerData.password,
      )
      const data = res.data as unknown as AuthResponse
      token.value = data.access_token
      user.value = data.user
      localStorage.setItem('access_token', data.access_token)
      // A fresh account reads its own execution mode rather than inheriting
      // whatever this browser already saw.
      useAgentModeStore().reset()
      // A fresh account also reads its own tasks: nothing another account's
      // conversation showed may still be on screen.
      useTaskProgressStore().reset()
      return { success: true }
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      const message =
        axiosErr.response?.data?.detail ?? 'Failed to register'
      error.value = message
      return { success: false, error: message }
    } finally {
      isLoading.value = false
    }
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem('access_token')
    // Signing out leaves no account whose mode this browser could claim, and
    // any read that was still in flight belongs to the account just left.
    useAgentModeStore().reset()
  }

  return {
    user,
    token,
    isLoading,
    error,
    login,
    register,
    logout,
    setUserFromStorage,
  }
})
