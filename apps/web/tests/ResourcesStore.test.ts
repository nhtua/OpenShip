import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useResourcesStore } from '@/stores/resources'
import { resourcesApi } from '@/services/api'

vi.mock('@/services/api', () => ({
  resourcesApi: {
    listEnvironments: vi.fn(),
    listBindings: vi.fn(),
    createEnvironment: vi.fn(),
    createBinding: vi.fn(),
    listObservations: vi.fn(),
  },
}))

const api = vi.mocked(resourcesApi)

describe('project resources store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    api.listEnvironments.mockImplementation(async (projectId) => ({
      data: [{ id: `environment-${projectId}`, project_id: projectId, name: 'prod', display_name: 'Production', created_at: '' }],
    } as never))
    api.listBindings.mockImplementation(async (projectId) => ({
      data: { items: [{
        resource_id: `resource-${projectId}`, uri: `aws:s3://${projectId}`, display_name: 'Data',
        environment_id: `environment-${projectId}`, environment_name: 'prod', binding_id: `binding-${projectId}`,
        binding_revision: 1, connector_id: 'aws-connector', connector_revision: 1, account_id: '123456789012',
        region: 'us-east-1', bucket_name: `${projectId}-bucket`, bucket_arn: `arn:aws:s3:::${projectId}-bucket`,
        expected_owner: '123456789012', management_method: 'unknown', state: 'active', created_at: '',
      }], next_cursor: null },
    } as never))
  })

  it('keeps the latest project when a prior project request finishes late', async () => {
    let resolveOldEnvironments!: (value: never) => void
    let resolveOldBindings!: (value: never) => void
    api.listEnvironments.mockImplementation((projectId) => projectId === 'old'
      ? new Promise((resolve) => { resolveOldEnvironments = resolve as (value: never) => void })
      : Promise.resolve({ data: [{ id: 'environment-new', project_id: 'new', name: 'prod', display_name: 'Production', created_at: '' }] } as never))
    api.listBindings.mockImplementation((projectId) => projectId === 'old'
      ? new Promise((resolve) => { resolveOldBindings = resolve as (value: never) => void })
      : Promise.resolve({ data: { items: [], next_cursor: null } } as never))

    const store = useResourcesStore()
    const oldLoad = store.loadProject('old')
    await store.loadProject('new')
    resolveOldEnvironments({ data: [{ id: 'environment-old', project_id: 'old', name: 'prod', display_name: 'Old', created_at: '' }] } as never)
    resolveOldBindings({ data: { items: [], next_cursor: null } } as never)
    await oldLoad

    expect(store.environments[0]?.project_id).toBe('new')
    expect(store.bindings).toEqual([])
  })

  it('loads environments and only active owned binding summaries for the selected project', async () => {
    const store = useResourcesStore()
    await store.loadProject('project-a')

    expect(api.listEnvironments).toHaveBeenCalledWith('project-a')
    expect(api.listBindings).toHaveBeenCalledWith('project-a')
    expect(store.environments[0]?.id).toBe('environment-project-a')
    expect(store.bindings[0]?.state).toBe('active')
  })
})
