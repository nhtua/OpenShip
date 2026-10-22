import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useProjectsStore } from '@/stores/projects'
import { projectsApi } from '@/services/api'

vi.mock('@/services/api', () => ({
  projectsApi: {
    list: vi.fn(),
    create: vi.fn(),
    rename: vi.fn(),
    delete: vi.fn(),
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

  it('deletes the selected project and falls back to another owned project', async () => {
    api.delete.mockResolvedValue({ data: undefined } as never)
    const store = useProjectsStore()
    await store.initialize()
    store.selectProject('project-b')

    await store.deleteProject('project-b')

    expect(api.delete).toHaveBeenCalledWith('project-b')
    expect(store.projects).toEqual([{ id: 'project-a', name: 'Production' }])
    expect(store.currentProjectId).toBe('project-a')
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
    expect(store.currentProjectId).toBeNull()
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })
})
