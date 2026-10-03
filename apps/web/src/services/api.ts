import axios from 'axios'
import type { AuthResponse } from '@/types'

const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
    }
    return Promise.reject(error)
  },
)

export const authApi = {
  login: (username: string, password: string): Promise<{ data: AuthResponse }> =>
    api.post('/auth/login', { username, password }),

  register: (
    username: string,
    email: string,
    password: string,
  ): Promise<{ data: AuthResponse }> =>
    api.post('/auth/register', { username, email, password }),

  logout: (): Promise<void> => api.post('/auth/logout'),
}

export default api
