import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationFiles from '@/components/chat/ConversationFiles.vue'
import type { ArtifactSummary, ConversationWorkspaceSummary } from '@/types'

const workspace: ConversationWorkspaceSummary = {
  id: 'workspace-1',
  conversation_id: 'conversation-1',
  project_id: 'project-1',
  schema_version: 1,
  relative_root: 'projects/p/conversations/c',
  storage_id: 'storage-1',
  status: 'available',
  created_at: '2026-10-20T12:00:00Z',
  quota: {
    committed_bytes: 512,
    committed_count: 2,
    reserved_bytes: 0,
    reserved_count: 0,
    max_bytes: 1024,
    max_count: 20,
  },
}

function artifact(id: string, name: string, status: ArtifactSummary['status'] = 'ready'): ArtifactSummary {
  return {
    id,
    sha256: id.padEnd(64, 'a').slice(0, 64),
    size_bytes: 256,
    media_type: 'text/plain',
    conversation_id: 'conversation-1',
    project_id: 'project-1',
    name,
    source: 'brave_web_search',
    status,
    created_at: '2026-10-20T12:00:00Z',
    mutability: 'immutable',
  }
}

function props(overrides: Record<string, unknown> = {}) {
  return {
    conversationId: 'conversation-1',
    workspace,
    files: [artifact('artifact-1', 'same.txt'), artifact('artifact-2', 'same.txt')],
    loading: false,
    loadingMore: false,
    error: null,
    nextCursor: null,
    selectedArtifactId: null,
    selectedContent: null,
    selectedNextOffset: null,
    readingArtifactId: null,
    deletingArtifactId: null,
    ...overrides,
  }
}

describe('ConversationFiles', () => {
  it('wraps full filenames and keeps internal artifact IDs out of the list', async () => {
    const longName = 'search-results-with-a-long-filename.json'
    const wrapper = mount(ConversationFiles, {
      props: props({ files: [artifact('artifact-id-one', longName), artifact('artifact-id-two', longName)] }),
    })
    expect(wrapper.get('[data-testid="conversation-file-list"]').exists()).toBe(true)
    const rows = wrapper.findAll('[data-testid="conversation-file"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain(longName)
    expect(rows[1].text()).toContain(longName)
    expect(rows[0].text()).not.toContain('artifact-id-one')
    expect(rows[1].text()).not.toContain('artifact-id-two')
    expect(rows[0].find('span').classes()).toContain('break-words')
    expect(wrapper.text()).toContain('512 B of 1 KB used')
  })

  it('selects, downloads, and requests deletion by exact artifact ID', async () => {
    const wrapper = mount(ConversationFiles, { props: props() })
    const preview = wrapper.get('[aria-label="Preview same.txt"]')
    const download = wrapper.get('[aria-label="Download same.txt"]')
    const remove = wrapper.get('[aria-label="Delete same.txt"]')
    expect(preview.text()).toBe('')
    expect(preview.get('i').classes()).toContain('pi-eye')
    expect(download.text()).toBe('')
    expect(download.get('i').classes()).toContain('pi-download')
    expect(remove.text()).toBe('')
    expect(remove.get('i').classes()).toContain('pi-trash')

    await preview.trigger('click')
    await download.trigger('click')
    await remove.trigger('click')

    expect(wrapper.emitted('select-file')?.[0]).toEqual(['conversation-1', 'artifact-1'])
    expect(wrapper.emitted('download-file')?.[0]).toEqual(['artifact-1', 'same.txt'])
    expect(wrapper.emitted('delete-file')?.[0]).toEqual(['conversation-1', 'artifact-1'])
  })

  it('keeps the close-preview button enabled while the selected file is loading', () => {
    const wrapper = mount(ConversationFiles, {
      props: props({ selectedArtifactId: 'artifact-1', readingArtifactId: 'artifact-1' }),
    })
    const closePreview = wrapper.get('[aria-label="Close preview same.txt"]')
    expect(closePreview.attributes('disabled')).toBeUndefined()
  })

  it('renders selected content as text, and shows pagination and pinned-delete failures', async () => {
    const hostile = '<img src=x onerror=alert(1)>'
    const wrapper = mount(ConversationFiles, {
      props: props({
        selectedArtifactId: 'artifact-1',
        selectedContent: hostile,
        nextCursor: 'next-page',
        error: 'This file is in use by an active operation and cannot be deleted.',
      }),
    })
    expect(wrapper.find('[data-testid="selected-file-content"] img').exists()).toBe(false)
    expect(wrapper.get('[data-testid="selected-file-content"]').text()).toContain(hostile)
    expect(wrapper.get('[role="alert"]').text()).toContain('active operation')
    await wrapper.get('[data-testid="load-more-files"]').trigger('click')
    expect(wrapper.emitted('load-more')?.[0]).toEqual(['conversation-1'])
  })

  it('pretty-prints valid JSON and leaves incomplete JSON readable as text', async () => {
    const jsonArtifact = { ...artifact('json-artifact', 'search-results.json'), media_type: 'application/json' }
    const rawJson = '{"query":"nearest star","items":[{"rank":1,"title":"Alpha Centauri"}]}'
    const valid = mount(ConversationFiles, {
      props: props({
        files: [jsonArtifact],
        selectedArtifactId: jsonArtifact.id,
        selectedContent: rawJson,
      }),
    })
    expect(valid.get('[data-testid="selected-file-content"] pre').text()).toBe(
      JSON.stringify(JSON.parse(rawJson), null, 2),
    )
    expect(valid.get('[data-testid="selected-file-content"]').text()).not.toContain(`Version ${jsonArtifact.id}`)

    const partialJson = '{"query":"nearest star","items":['
    const partial = mount(ConversationFiles, {
      props: props({
        files: [jsonArtifact],
        selectedArtifactId: jsonArtifact.id,
        selectedContent: partialJson,
        selectedNextOffset: 1024,
      }),
    })
    expect(partial.get('[data-testid="selected-file-content"] pre').text()).toBe(partialJson)
  })

  it('distinguishes empty history from unavailable storage', async () => {
    const empty = mount(ConversationFiles, {
      props: props({ files: [], workspace: { ...workspace, quota: { ...workspace.quota, committed_count: 0 } } }),
    })
    expect(empty.text()).toContain('No saved files in this conversation.')
    expect(empty.text()).not.toContain('storage is unavailable')

    const unavailable = mount(ConversationFiles, {
      props: props({ files: [], workspace: { ...workspace, status: 'unavailable' } }),
    })
    expect(unavailable.text()).toContain('Workspace storage is unavailable.')
    expect(unavailable.text()).not.toContain('No saved files')
  })
})
