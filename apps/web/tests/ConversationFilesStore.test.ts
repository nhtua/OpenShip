import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { artifactsApi, workspaceApi } from '@/services/api'
import { useArtifactsStore } from '@/stores/artifacts'
import type { ConversationWorkspacePage, WorkspaceFileRead } from '@/types'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  artifactsApi: { list: vi.fn(), download: vi.fn(), delete: vi.fn() },
  workspaceApi: { get: vi.fn(), readFile: vi.fn() },
}))

const mockWorkspace = vi.mocked(workspaceApi)
const mockArtifacts = vi.mocked(artifactsApi)

function page(conversationId: string, ids: string[], nextCursor: string | null = null): ConversationWorkspacePage {
  return {
    workspace: {
      id: `workspace-${conversationId}`,
      conversation_id: conversationId,
      project_id: 'project-1',
      schema_version: 1,
      relative_root: 'relative-only',
      storage_id: 'storage-1',
      status: 'available',
      created_at: '2026-10-20T12:00:00Z',
      quota: { committed_bytes: 10, committed_count: ids.length, reserved_bytes: 0, reserved_count: 0, max_bytes: 100, max_count: 10 },
    },
    files: {
      items: ids.map((id) => ({
        id,
        sha256: id.padEnd(64, 'a').slice(0, 64),
        size_bytes: 10,
        media_type: 'text/plain',
        conversation_id: conversationId,
        project_id: 'project-1',
        name: 'same-name.txt',
        source: 'upload',
        status: 'ready' as const,
        created_at: '2026-10-20T12:00:00Z',
        mutability: 'immutable' as const,
      })),
      next_cursor: nextCursor,
    },
  }
}

function read(artifactId: string, content: string, nextOffset: number | null = null): WorkspaceFileRead {
  return {
    workspace_id: 'workspace-c1',
    artifact_id: artifactId,
    version_id: artifactId,
    sha256: artifactId.padEnd(64, 'a').slice(0, 64),
    media_type: 'text/plain',
    size_bytes: 10,
    offset: 0,
    content,
    next_offset: nextOffset,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('conversation file store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
  })

  it('hydrates the conversation association and paginates distinct same-name snapshots', async () => {
    mockWorkspace.get
      .mockResolvedValueOnce({ data: page('c1', ['file-1'], 'cursor-2') } as never)
      .mockResolvedValueOnce({ data: page('c1', ['file-2']) } as never)
    const store = useArtifactsStore()

    await store.loadConversation('c1')
    await store.loadMore('c1')

    expect(mockWorkspace.get).toHaveBeenNthCalledWith(1, 'c1', { limit: 50, cursor: undefined })
    expect(mockWorkspace.get).toHaveBeenNthCalledWith(2, 'c1', { limit: 50, cursor: 'cursor-2' })
    expect(store.workspaceByConversation.get('c1')?.id).toBe('workspace-c1')
    expect(store.byConversation.get('c1')?.map((item) => item.id)).toEqual(['file-1', 'file-2'])
  })

  it('keeps reads tied to an exact artifact and ignores a delayed response after switching conversations', async () => {
    const delayedRead = deferred<{ data: WorkspaceFileRead }>()
    mockWorkspace.get
      .mockResolvedValueOnce({ data: page('c1', ['file-c1']) } as never)
      .mockResolvedValueOnce({ data: page('c2', ['file-c2']) } as never)
    mockWorkspace.readFile.mockReturnValue(delayedRead.promise as never)
    const store = useArtifactsStore()
    await store.loadConversation('c1')
    const pendingRead = store.selectFile('c1', 'file-c1')
    await Promise.resolve()
    await store.loadConversation('c2')
    delayedRead.resolve({ data: read('file-c1', 'private old conversation') })
    await pendingRead

    expect(store.selectedByConversation.get('c1')).toBe('file-c1')
    expect(store.contentByArtifact.has('file-c1')).toBe(false)
    expect(store.byConversation.get('c2')?.map((item) => item.id)).toEqual(['file-c2'])
  })

  it('appends bounded read chunks and refuses to select an ID outside the active conversation list', async () => {
    mockWorkspace.get.mockResolvedValueOnce({ data: page('c1', ['file-1']) } as never)
    mockWorkspace.readFile
      .mockResolvedValueOnce({ data: read('file-1', 'first ', 6) } as never)
      .mockResolvedValueOnce({ data: { ...read('file-1', 'second'), offset: 6 } } as never)
    const store = useArtifactsStore()
    await store.loadConversation('c1')
    await store.selectFile('c1', 'not-in-this-conversation')
    expect(mockWorkspace.readFile).not.toHaveBeenCalled()

    await store.selectFile('c1', 'file-1')
    await store.readMore('c1', 'file-1')
    expect(store.contentByArtifact.get('file-1')?.content).toBe('first second')
    expect(mockWorkspace.readFile).toHaveBeenNthCalledWith(2, 'c1', 'file-1', { offset: 6, max_bytes: 16 * 1024 })
  })

  it('keeps a pinned artifact listed and reports a safe delete refusal', async () => {
    mockWorkspace.get.mockResolvedValueOnce({ data: page('c1', ['file-1']) } as never)
    mockArtifacts.delete.mockRejectedValueOnce({
      response: { data: { detail: { error: { code: 'artifact_is_pinned' } } } },
    })
    const store = useArtifactsStore()
    await store.loadConversation('c1')
    await store.deleteArtifact('c1', 'file-1')

    expect(store.byConversation.get('c1')?.[0].id).toBe('file-1')
    expect(store.error).toBe('This file is in use by an active operation and cannot be deleted.')
  })
})
