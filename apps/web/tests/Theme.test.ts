import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useThemeStore } from '@/stores/theme'

describe('theme store', () => {
  beforeEach(() => {
    document.documentElement.className = ''
    localStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    document.documentElement.className = ''
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('initializes to dark when no preference is stored', () => {
    const store = useThemeStore()
    store.initializeTheme()
    expect(store.mode).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('initializes to light from a persisted preference', () => {
    localStorage.setItem('openship-theme', 'light')
    const store = useThemeStore()
    store.initializeTheme()
    expect(store.mode).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('falls back to dark for an invalid stored value', () => {
    localStorage.setItem('openship-theme', 'neon')
    const store = useThemeStore()
    store.initializeTheme()
    expect(store.mode).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('toggleTheme flips the root class and persists the mode', () => {
    const store = useThemeStore()
    store.initializeTheme()
    expect(store.mode).toBe('dark')

    store.toggleTheme()
    expect(store.mode).toBe('light')
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(localStorage.getItem('openship-theme')).toBe('light')

    store.toggleTheme()
    expect(store.mode).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('openship-theme')).toBe('dark')
  })

  it('does not crash when storage throws', () => {
    const boom = vi
      .fn()
      .mockImplementation(() => {
        throw new Error('storage denied')
      })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(boom)

    const store = useThemeStore()
    expect(() => store.initializeTheme()).not.toThrow()
    expect(store.mode).toBe('dark')
    expect(() => store.toggleTheme()).not.toThrow()
  })
})
