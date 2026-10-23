import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProjectsStore } from '@/stores/projects'
import { projectsApi } from '@/services/api'

vi.mock('@/services/api', () => ({
  projectsApi: {
    list: vi.fn(),
    create: vi.fn(),
    rename: vi.fn(),
    archive: vi.fn(),
    restore: vi.fn(),
  },
}))

const api = vi.mocked(projectsApi)

describe('projects store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    api.list.mockResolvedValue({
      data: [
        { id: 'project-a', name: 'Production' },
        { id: 'project-b', name: 'Staging' },
      ],
    } as never)
  })

  it('loads owned projects and replaces a stale selection with an owned project', async () => {
    const store = useProjectsStore()
    store.currentProjectId = 'foreign-project'

    await store.initialize()

    expect(store.projects.map(({ id }) => id)).toEqual(['project-a', 'project-b'])
    expect(store.currentProjectId).toBe('project-a')
  })

  it('creates and selects a normalized project', async () => {
    api.create.mockResolvedValue({ data: { id: 'project-c', name: 'Sandbox' } } as never)
    const store = useProjectsStore()
    await store.initialize()

    await store.createProject('  Sandbox  ')

    expect(api.create).toHaveBeenCalledWith({ name: 'Sandbox' })
    expect(store.projects.at(-1)).toEqual({ id: 'project-c', name: 'Sandbox' })
    expect(store.currentProjectId).toBe('project-c')
  })

  it('renames the specified owned project without changing the active project', async () => {
    api.rename.mockResolvedValue({ data: { id: 'project-b', name: 'Preview' } } as never)
    const store = useProjectsStore()
    await store.initialize()
    store.selectProject('project-b')

    await store.renameProject('project-b', 'Preview')

    expect(api.rename).toHaveBeenCalledWith('project-b', { name: 'Preview' })
    expect(store.projects.find(({ id }) => id === 'project-b')?.name).toBe('Preview')
    expect(store.currentProjectId).toBe('project-b')
  })

  it('archives the selected project and falls back to another active project', async () => {
    api.archive.mockResolvedValue({ data: { id: 'project-b', name: 'Staging', archived_at: '2026-10-22T00:00:00Z' } } as never)
    const store = useProjectsStore()
    await store.initialize()
    store.selectProject('project-b')

    await store.archiveProject('project-b')

    expect(api.archive).toHaveBeenCalledWith('project-b')
    expect(store.projects).toEqual([{ id: 'project-a', name: 'Production' }])
    expect(store.archivedProjects).toEqual([{ id: 'project-b', name: 'Staging', archived_at: '2026-10-22T00:00:00Z' }])
    expect(store.currentProjectId).toBe('project-a')
  })

  it('keeps selection and lists unchanged when archive fails', async () => {
    api.archive.mockRejectedValue(new Error('Archive failed'))
    const store = useProjectsStore()
    await store.initialize()
    store.selectProject('project-b')

    await expect(store.archiveProject('project-b')).rejects.toThrow('Archive failed')

    expect(store.projects.map(({ id }) => id)).toEqual(['project-a', 'project-b'])
    expect(store.archivedProjects).toEqual([])
    expect(store.currentProjectId).toBe('project-b')
  })

  it('does not let an active list started before archive reinsert the project', async () => {
    let releaseList: ((value: { data: { id: string; name: string }[] }) => void) | undefined
    api.list.mockImplementationOnce((async () => new Promise((resolve) => { releaseList = resolve })) as never)
    const store = useProjectsStore()
    const load = store.initialize()
    await vi.waitFor(() => expect(releaseList).toBeTypeOf('function'))
    store.projects = [{ id: 'project-a', name: 'Production' }, { id: 'project-b', name: 'Staging' }]
    api.archive.mockResolvedValue({ data: { id: 'project-b', name: 'Staging', archived_at: '2026-10-22T00:00:00Z' } } as never)

    await store.archiveProject('project-b')
    releaseList?.({ data: [{ id: 'project-a', name: 'Production' }, { id: 'project-b', name: 'Staging' }] })
    await load

    expect(store.projects.map(({ id }) => id)).toEqual(['project-a'])
    expect(store.archivedProjects.map(({ id }) => id)).toEqual(['project-b'])
  })

  it('restores an archived project without selecting it', async () => {
    api.list.mockImplementation(async (state?: string) => ({
      data: state === 'archived' ? [{ id: 'project-b', name: 'Staging', archived_at: '2026-10-22T00:00:00Z' }] : [{ id: 'project-a', name: 'Production' }],
    } as never))
    api.restore.mockResolvedValue({ data: { id: 'project-b', name: 'Staging', archived_at: null } } as never)
    const store = useProjectsStore()
    await store.initialize()
    await store.loadArchivedProjects()

    await store.restoreProject('project-b')

    expect(api.list).toHaveBeenNthCalledWith(2, 'archived')
    expect(store.projects.map(({ id }) => id)).toEqual(['project-a', 'project-b'])
    expect(store.archivedProjects).toEqual([])
    expect(store.currentProjectId).toBe('project-a')
  })

  it('does not repopulate active or archived projects after logout', async () => {
    let releaseActive: ((value: { data: { id: string; name: string }[] }) => void) | undefined
    let releaseArchived: ((value: { data: { id: string; name: string; archived_at: string }[] }) => void) | undefined
    api.list
      .mockImplementationOnce((async () => new Promise((resolve) => { releaseActive = resolve })) as never)
      .mockImplementationOnce((async () => new Promise((resolve) => { releaseArchived = resolve })) as never)
    const store = useProjectsStore()
    const activeLoad = store.initialize()
    const archivedLoad = store.loadArchivedProjects()
    await vi.waitFor(() => {
      expect(releaseActive).toBeTypeOf('function')
      expect(releaseArchived).toBeTypeOf('function')
    })

    store.clear()
    releaseActive?.({ data: [{ id: 'project-a', name: 'Production' }] })
    releaseArchived?.({ data: [{ id: 'project-b', name: 'Staging', archived_at: '2026-10-22T00:00:00Z' }] })
    await Promise.all([activeLoad, archivedLoad])

    expect(store.projects).toEqual([])
    expect(store.archivedProjects).toEqual([])
    expect(store.currentProjectId).toBeNull()
  })

  it('ignores project selection outside the owned project list', async () => {
    const store = useProjectsStore()
    await store.initialize()

    expect(store.selectProject('foreign-project')).toBe(false)
    expect(store.currentProjectId).toBe('project-a')
  })

  it('clears project state on logout', async () => {
    const store = useProjectsStore()
    await store.initialize()

    store.clear()

    expect(store.projects).toEqual([])
    expect(store.archivedProjects).toEqual([])
    expect(store.currentProjectId).toBeNull()
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })
})
