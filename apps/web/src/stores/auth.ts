import { defineStore } from 'pinia'
import { ref } from 'vue'
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
