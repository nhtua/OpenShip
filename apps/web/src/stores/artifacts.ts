import { defineStore } from 'pinia'
import { ref } from 'vue'
import { artifactsApi } from '@/services/api'
import type { ArtifactSummary } from '@/types'

export const useArtifactsStore = defineStore('artifacts', () => {
  const byConversation = ref<Map<string, ArtifactSummary[]>>(new Map())
  const nextCursor = ref<Map<string, string | null>>(new Map())
  const loadingConversation = ref<string | null>(null)
  const error = ref<string | null>(null)
  let requestGeneration = 0

  async function loadConversation(conversationId: string, cursor?: string): Promise<void> {
    const generation = ++requestGeneration
    loadingConversation.value = conversationId
    error.value = null
    try {
      const response = await artifactsApi.list(conversationId, { limit: 50, cursor })
      if (generation !== requestGeneration) return
      const previous = cursor ? (byConversation.value.get(conversationId) ?? []) : []
      byConversation.value.set(conversationId, [...previous, ...response.data.items])
      nextCursor.value.set(conversationId, response.data.next_cursor)
    } catch (err) {
      if (generation !== requestGeneration) return
      const detail = err as { response?: { data?: { error?: { message?: string }; detail?: string } } }
      error.value =
        detail.response?.data?.error?.message ??
        detail.response?.data?.detail ??
        'Failed to load conversation artifacts'
    } finally {
      if (generation === requestGeneration) loadingConversation.value = null
    }
  }

  async function loadMore(conversationId: string): Promise<void> {
    const cursor = nextCursor.value.get(conversationId)
    if (cursor) await loadConversation(conversationId, cursor)
  }

  async function download(artifactId: string, name: string): Promise<void> {
    error.value = null
    try {
      const response = await artifactsApi.download(artifactId)
      const objectUrl = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = name
      link.rel = 'noopener'
      link.click()
      setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
    } catch {
      error.value = 'The retained artifact could not be downloaded.'
    }
  }

  function clear(): void {
    requestGeneration += 1
    byConversation.value.clear()
    nextCursor.value.clear()
    loadingConversation.value = null
    error.value = null
  }

  return {
    byConversation,
    nextCursor,
    loadingConversation,
    error,
    loadConversation,
    loadMore,
    download,
    clear,
  }
})
