import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'openship-theme'

/**
 * Theme store (plan Task 4.2): dark is the default; the user preference is
 * persisted under `openship-theme`. Storage failures (private mode, quotas)
 * must never break the app — the theme still toggles for the session.
 */
export const useThemeStore = defineStore('theme', () => {
  const mode = ref<'light' | 'dark'>('dark')

  function readStored(): 'light' | 'dark' | null {
    try {
      const value = localStorage.getItem(STORAGE_KEY)
      return value === 'light' || value === 'dark' ? value : null
    } catch {
      return null
    }
  }

  function persist(value: 'light' | 'dark') {
    try {
      localStorage.setItem(STORAGE_KEY, value)
    } catch {
      // Storage unavailable: the theme still works for this session.
    }
  }

  function applyClass() {
    document.documentElement.classList.toggle('dark', mode.value === 'dark')
  }

  function initializeTheme() {
    mode.value = readStored() ?? 'dark'
    applyClass()
  }

  function toggleTheme() {
    mode.value = mode.value === 'dark' ? 'light' : 'dark'
    applyClass()
    persist(mode.value)
  }

  return { mode, initializeTheme, toggleTheme }
})
