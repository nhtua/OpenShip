import { defineStore } from 'pinia'
import { ref } from 'vue'
import { lifecycleConflictCode } from '@/lib/lifecycleErrors'
import { artifactsApi, workspaceApi } from '@/services/api'
import { useChatStore } from '@/stores/chat'
import type {
  ArtifactSummary,
  ConversationWorkspaceSummary,
  WorkspaceFileRead,
} from '@/types'

export const useArtifactsStore = defineStore('artifacts', () => {
  const byConversation = ref<Map<string, ArtifactSummary[]>>(new Map())
  const workspaceByConversation = ref<Map<string, ConversationWorkspaceSummary>>(new Map())
  const nextCursor = ref<Map<string, string | null>>(new Map())
  const selectedByConversation = ref<Map<string, string | null>>(new Map())
  const contentByArtifact = ref<Map<string, WorkspaceFileRead>>(new Map())
  const loadingConversation = ref<string | null>(null)
  const loadingMoreConversation = ref<string | null>(null)
  const readingArtifactId = ref<string | null>(null)
  const deletingArtifactId = ref<string | null>(null)
  const error = ref<string | null>(null)
  let requestGeneration = 0
  let readGeneration = 0

  function requestMessage(err: unknown, fallback: string): string {
    const detail = err as {
      response?: {
        data?: {
          detail?: string | { error?: { code?: string; message?: string } }
          error?: { code?: string; message?: string }
        }
      }
    }
    const body = detail.response?.data
    const nested = typeof body?.detail === 'object' ? body.detail.error : undefined
    const code = nested?.code ?? body?.error?.code
    if (code === 'artifact_is_pinned') {
      return 'This file is in use by an active operation and cannot be deleted.'
    }
    return (
      nested?.message ??
      body?.error?.message ??
      (typeof body?.detail === 'string' ? body.detail : null) ??
      fallback
    )
  }

  async function loadConversation(conversationId: string, cursor?: string): Promise<void> {
    const generation = ++requestGeneration
    readGeneration += 1
    readingArtifactId.value = null
    if (cursor) loadingMoreConversation.value = conversationId
    else loadingConversation.value = conversationId
    error.value = null
    try {
      const response = await workspaceApi.get(conversationId, { limit: 50, cursor })
      if (generation !== requestGeneration) return
      const { workspace, files } = response.data
      if (!workspace || !files) throw new Error('workspace response was incomplete')
      workspaceByConversation.value.set(conversationId, workspace)
      const previous = cursor ? (byConversation.value.get(conversationId) ?? []) : []
      const seen = new Set(previous.map((item) => item.id))
      byConversation.value.set(
        conversationId,
        [...previous, ...files.items.filter((item) => !seen.has(item.id))],
      )
      nextCursor.value.set(conversationId, files.next_cursor)
    } catch (err) {
      if (generation !== requestGeneration) return
      error.value = requestMessage(err, 'Failed to load conversation files')
    } finally {
      if (generation === requestGeneration) {
        if (cursor) loadingMoreConversation.value = null
        else loadingConversation.value = null
      }
    }
  }

  async function loadMore(conversationId: string): Promise<void> {
    const cursor = nextCursor.value.get(conversationId)
    if (cursor) await loadConversation(conversationId, cursor)
  }

  async function selectFile(conversationId: string, artifactId: string): Promise<void> {
    const artifact = byConversation.value.get(conversationId)?.find((item) => item.id === artifactId)
    if (!artifact || artifact.conversation_id !== conversationId || artifact.status !== 'ready') return
    if (selectedByConversation.value.get(conversationId) === artifactId) {
      selectedByConversation.value.set(conversationId, null)
      readGeneration += 1
      readingArtifactId.value = null
      return
    }
    selectedByConversation.value.set(conversationId, artifactId)
    if (contentByArtifact.value.has(artifactId)) return

    const generation = ++readGeneration
    const listGeneration = requestGeneration
    readingArtifactId.value = artifactId
    error.value = null
    try {
      const response = await workspaceApi.readFile(conversationId, artifactId, { max_bytes: 16 * 1024 })
      if (
        generation !== readGeneration ||
        listGeneration !== requestGeneration ||
        selectedByConversation.value.get(conversationId) !== artifactId
      ) return
      if (response.data.artifact_id !== artifactId || response.data.sha256 !== artifact.sha256) {
        throw new Error('workspace file identity changed')
      }
      contentByArtifact.value.set(artifactId, response.data)
    } catch (err) {
      if (generation === readGeneration) {
        error.value = requestMessage(err, 'The selected file could not be read.')
      }
    } finally {
      if (generation === readGeneration) readingArtifactId.value = null
    }
  }

  async function readMore(conversationId: string, artifactId: string): Promise<void> {
    const current = contentByArtifact.value.get(artifactId)
    if (!current || current.next_offset === null) return
    const artifact = byConversation.value.get(conversationId)?.find((item) => item.id === artifactId)
    if (!artifact || artifact.conversation_id !== conversationId || artifact.sha256 !== current.sha256) return
    const generation = ++readGeneration
    const listGeneration = requestGeneration
    readingArtifactId.value = artifactId
    error.value = null
    try {
      const response = await workspaceApi.readFile(conversationId, artifactId, {
        offset: current.next_offset,
        max_bytes: 16 * 1024,
      })
      if (
        generation !== readGeneration ||
        listGeneration !== requestGeneration ||
        selectedByConversation.value.get(conversationId) !== artifactId
      ) return
      if (response.data.artifact_id !== artifactId || response.data.sha256 !== current.sha256) {
        throw new Error('workspace file identity changed')
      }
      contentByArtifact.value.set(artifactId, {
        ...response.data,
        content: current.content + response.data.content,
      })
    } catch (err) {
      if (generation === readGeneration) error.value = requestMessage(err, 'The selected file could not be read.')
    } finally {
      if (generation === readGeneration) readingArtifactId.value = null
    }
  }

  async function download(artifactId: string, name: string): Promise<void> {
    const generation = requestGeneration
    error.value = null
    try {
      const response = await artifactsApi.download(artifactId)
      if (generation !== requestGeneration) return
      const objectUrl = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = name
      link.rel = 'noopener'
      link.click()
      setTimeout(() => URL.revokeObjectURL(objectUrl), 0)
    } catch (err) {
      if (generation === requestGeneration) {
        error.value = requestMessage(err, 'The retained artifact could not be downloaded.')
      }
    }
  }

  async function deleteArtifact(conversationId: string, artifactId: string): Promise<void> {
    const artifact = byConversation.value.get(conversationId)?.find((item) => item.id === artifactId)
    if (!artifact || artifact.conversation_id !== conversationId || !['ready', 'unavailable'].includes(artifact.status)) return
    const generation = requestGeneration
    deletingArtifactId.value = artifactId
    error.value = null
    try {
      await artifactsApi.delete(artifactId)
      if (generation !== requestGeneration) return
      selectedByConversation.value.set(
        conversationId,
        selectedByConversation.value.get(conversationId) === artifactId
          ? null
          : selectedByConversation.value.get(conversationId) ?? null,
      )
      contentByArtifact.value.delete(artifactId)
      deletingArtifactId.value = null
      await loadConversation(conversationId)
    } catch (err) {
      if (generation === requestGeneration) {
        error.value = requestMessage(err, 'The retained file could not be deleted.')
        if (lifecycleConflictCode(err)) {
          await useChatStore().refreshLifecycleAfterConflict(conversationId)
        }
      }
    } finally {
      if (deletingArtifactId.value === artifactId) deletingArtifactId.value = null
    }
  }

  function clear(): void {
    requestGeneration += 1
    readGeneration += 1
    byConversation.value.clear()
    workspaceByConversation.value.clear()
    nextCursor.value.clear()
    selectedByConversation.value.clear()
    contentByArtifact.value.clear()
    loadingConversation.value = null
    loadingMoreConversation.value = null
    readingArtifactId.value = null
    deletingArtifactId.value = null
    error.value = null
  }

  return {
    byConversation,
    workspaceByConversation,
    nextCursor,
    selectedByConversation,
    contentByArtifact,
    loadingConversation,
    loadingMoreConversation,
    readingArtifactId,
    deletingArtifactId,
    error,
    loadConversation,
    loadMore,
    selectFile,
    readMore,
    download,
    deleteArtifact,
    clear,
  }
})
