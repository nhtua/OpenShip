import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import LogicalFiles from '@/components/chat/LogicalFiles.vue'
import type { ArtifactSummary, LogicalFileSummary, LogicalFileVersion } from '@/types'

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  createFromArtifact: vi.fn(),
  replaceContent: vi.fn(),
  readFile: vi.fn(),
}))

vi.mock('@/services/api', () => ({
  logicalFilesApi: {
    list: mocks.list,
    createFromArtifact: mocks.createFromArtifact,
    replaceContent: mocks.replaceContent,
  },
  workspaceApi: { readFile: mocks.readFile },
}))

const artifact: ArtifactSummary = {
  id: 'artifact-source',
  sha256: 'a'.repeat(64),
  size_bytes: 15,
  media_type: 'text/plain',
  conversation_id: 'conversation-1',
  project_id: 'project-1',
  name: 'parser.py',
  source: 'upload',
  status: 'ready',
  created_at: '2026-10-28T00:00:00Z',
  mutability: 'immutable',
}

const file: LogicalFileSummary = {
  id: 'file-1',
  name: 'parser.py',
  current_version_id: 'version-1',
  current_artifact: {
    id: artifact.id,
    sha256: artifact.sha256,
    size_bytes: artifact.size_bytes,
    media_type: artifact.media_type,
    status: 'ready',
  },
  created_at: artifact.created_at,
}

const version: LogicalFileVersion = {
  id: file.current_version_id,
  file_id: file.id,
  artifact_id: artifact.id,
  base_version_id: null,
  created_at: artifact.created_at,
}

describe('LogicalFiles', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.list.mockResolvedValue({ data: { items: [file], next_cursor: null } })
    mocks.readFile.mockResolvedValue({
      data: {
        artifact_id: artifact.id,
        sha256: artifact.sha256,
        content: "print('reviewed')\n",
        offset: 0,
        next_offset: null,
      },
    })
  })

  it('keeps Run disabled and renders source as text until sandbox qualification is complete', async () => {
    const wrapper = mount(LogicalFiles, {
      props: {
        conversationId: 'conversation-1',
        artifacts: [artifact],
        readOnly: false,
      },
    })
    await flushPromises()
    await wrapper.get('[aria-label="Saved source files"] button').trigger('click')
    await flushPromises()

    const editor = wrapper.get('textarea')
    expect((editor.element as HTMLTextAreaElement).value).toBe("print('reviewed')\n")
    expect(wrapper.get('button[aria-describedby="sandbox-unavailable"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Execution is unavailable until the OpenSandbox gVisor profile passes host qualification.')

    await editor.setValue('<script>alert(1)</script>')
    expect((editor.element as HTMLTextAreaElement).value).toBe('<script>alert(1)</script>')
    expect(wrapper.find('script').exists()).toBe(false)
  })

  it('captures selected text artifacts as versioned files and saves edits by expected base', async () => {
    mocks.list
      .mockReset()
      .mockResolvedValueOnce({ data: { items: [], next_cursor: null } })
      .mockResolvedValue({ data: { items: [file], next_cursor: null } })
    mocks.createFromArtifact.mockResolvedValue({ data: { file, version } })
    const nextVersion = { ...version, id: 'version-2', base_version_id: version.id }
    mocks.replaceContent.mockResolvedValue({ data: nextVersion })
    const wrapper = mount(LogicalFiles, {
      props: {
        conversationId: 'conversation-1',
        artifacts: [artifact],
        readOnly: false,
      },
    })
    await flushPromises()

    await wrapper.get('#source-artifact').setValue(artifact.id)
    await wrapper.get('[data-testid="capture-source"]').trigger('click')
    await flushPromises()
    expect(mocks.createFromArtifact).toHaveBeenCalledWith('conversation-1', artifact.id, expect.any(String))
    expect(mocks.readFile).toHaveBeenCalledWith('conversation-1', artifact.id, { offset: 0, max_bytes: 16 * 1024 })

    await wrapper.get('textarea').setValue("print('edited')\n")
    await wrapper.get('[data-testid="save-source-version"]').trigger('click')
    await flushPromises()
    expect(mocks.replaceContent).toHaveBeenCalledWith(
      'conversation-1', file.id, version.id, expect.any(String), "print('edited')\n",
    )
  })
})
