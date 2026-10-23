import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useConnectorsStore } from '@/stores/connectors'
import { connectorsApi } from '@/services/api'

vi.mock('@/services/api', () => ({
  connectorsApi: {
    listProjectConnectors: vi.fn(),
    getProjectDefault: vi.fn(),
    setProjectDefault: vi.fn(),
    getProjectCapabilities: vi.fn(),
    getSetupContext: vi.fn(),
    listGlobalConnectors: vi.fn(),
  },
}))

const api = vi.mocked(connectorsApi)

const projectConnectors = [
  { id: 'global-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave', status: 'configured', revision: 4, is_default: true },
  { id: 'global-tavily', kind: 'tavily', visibility: 'global', name: 'Shared Tavily', status: 'configured', revision: 1, is_default: false },
  { id: 'project-tavily', kind: 'tavily', visibility: 'project', name: 'Project Tavily', status: 'configured', revision: 2, is_default: false },
] as const

describe('connector store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    api.listProjectConnectors.mockResolvedValue({ data: [...projectConnectors] } as never)
    api.getProjectDefault.mockImplementation(async (projectId, kind) => ({
      data: {
        service: kind,
        connector_id: projectId === 'project-a' && kind === 'brave' ? 'global-brave' : null,
        revision: projectId === 'project-a' && kind === 'brave' ? 3 : 0,
      },
    } as never))
    api.getProjectCapabilities.mockResolvedValue({ data: [] } as never)
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: false, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockRejectedValue({ response: { status: 403 } })
  })

  it('loads explicit project context and keeps global usage separate from management authority', async () => {
    const store = useConnectorsStore()
    await store.initialize('project-a', true)

    expect(store.connectors).toEqual([...projectConnectors])
    expect(store.defaults.brave?.connector_id).toBe('global-brave')
    expect(store.canManageGlobal).toBe(false)
    expect(store.connectors.filter((connector) => connector.visibility === 'global')).toEqual(projectConnectors.slice(0, 2))
    expect(store.globalConnectors).toEqual([])
    expect(store.credentialStoreReady).toBe(true)
    expect(api.getSetupContext).toHaveBeenCalledTimes(1)
    expect(api.listGlobalConnectors).not.toHaveBeenCalled()
    expect(api.listProjectConnectors).toHaveBeenCalledWith('project-a')
  })

  it('only enables global controls when the protected operator endpoint succeeds', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockResolvedValue({
      data: [{ ...projectConnectors[0], is_default: false }],
    } as never)
    const store = useConnectorsStore()
    await store.initialize('project-a', true)

    expect(store.canManageGlobal).toBe(true)
    expect(store.globalConnectors).toHaveLength(1)
    expect(store.globalConnectors[0].visibility).toBe('global')
    expect(api.listGlobalConnectors).toHaveBeenCalledTimes(1)
  })

  it('surfaces fixed readiness while keeping safe global usage available', async () => {
    api.getSetupContext.mockResolvedValue({
      data: {
        can_manage_global: false,
        credential_store_ready: false,
        unavailable_reason: 'idempotency_key_unavailable',
      },
    } as never)
    const store = useConnectorsStore()
    await store.initialize('project-a', true)

    expect(store.credentialStoreReady).toBe(false)
    expect(store.setupUnavailableReason).toBe('idempotency_key_unavailable')
    expect(store.canManageGlobal).toBe(false)
    expect(store.connectors.filter((connector) => connector.visibility === 'global')).toEqual(projectConnectors.slice(0, 2))
    expect(store.globalConnectors).toEqual([])
    expect(api.listGlobalConnectors).not.toHaveBeenCalled()
  })

  it('keeps server management authorization authoritative after setup context', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockRejectedValue({ response: { status: 403 } })
    const store = useConnectorsStore()
    await store.initialize('project-a', true)

    expect(api.listGlobalConnectors).toHaveBeenCalledTimes(1)
    expect(store.canManageGlobal).toBe(false)
    expect(store.connectors.filter((connector) => connector.visibility === 'global')).toEqual(projectConnectors.slice(0, 2))
    expect(store.globalConnectors).toEqual([])
    expect(store.credentialStoreReady).toBe(true)
  })

  it('switches one shared project default across services using the shared revision', async () => {
    api.getProjectCapabilities.mockResolvedValue({
      data: [{
        id: 'brave_web_search', version: '2.0.0', manifest_digest: 'a'.repeat(64),
        input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
        required_connector_kinds: ['brave'], connector_readiness: { brave: 'ambiguous' },
      }],
    } as never)
    let activeKind = 'brave'
    let activeId: string | null = 'global-brave'
    let revision = 3
    api.getProjectDefault.mockImplementation(async (_projectId, kind) => ({
      data: {
        service: kind,
        connector_id: kind === activeKind ? activeId : null,
        revision,
      },
    } as never))
    api.setProjectDefault.mockImplementation(async (_projectId, kind, payload) => {
      activeKind = kind
      activeId = payload.connector_id
      revision = payload.expected_revision + 1
      return { data: { service: kind, connector_id: activeId, revision } } as never
    })
    const store = useConnectorsStore()
    await store.initialize('project-a')
    api.getProjectCapabilities.mockResolvedValue({
      data: [{
        id: 'brave_web_search', version: '2.0.0', manifest_digest: 'a'.repeat(64),
        input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
        required_connector_kinds: ['brave'], connector_readiness: { brave: 'ready' },
      }],
    } as never)

    await store.setDefault('project-a', 'tavily', 'global-tavily')

    expect(api.setProjectDefault).toHaveBeenCalledWith('project-a', 'tavily', {
      expected_revision: 3,
      connector_id: 'global-tavily',
    })
    expect(store.defaults.brave?.connector_id).toBeNull()
    expect(store.defaults.brave?.revision).toBe(4)
    expect(store.defaults.tavily?.connector_id).toBe('global-tavily')
    expect(store.defaults.tavily?.revision).toBe(4)
    expect(store.capabilities[0].connector_readiness.brave).toBe('ready')
  })

  it('does not let a late project load overwrite the newly selected project', async () => {
    let resolveOldProject!: (value: { data: typeof projectConnectors[number][] }) => void
    api.listProjectConnectors.mockImplementation((projectId) => {
      if (projectId === 'project-a') {
        return new Promise((resolve) => { resolveOldProject = resolve }) as never
      }
      return Promise.resolve({ data: [{ ...projectConnectors[0], is_default: false }] }) as never
    })
    const store = useConnectorsStore()

    const oldLoad = store.loadProjectContext('project-a')
    await store.loadProjectContext('project-b')
    resolveOldProject({ data: [{ ...projectConnectors[1] }] })
    await oldLoad

    expect(api.listProjectConnectors).toHaveBeenNthCalledWith(1, 'project-a')
    expect(api.listProjectConnectors).toHaveBeenNthCalledWith(2, 'project-b')
    expect(store.connectors).toEqual([{ ...projectConnectors[0], is_default: false }])
    expect(store.defaults.brave?.connector_id).toBeNull()
  })

  it('ignores a completed default update after the selected project changes', async () => {
    let resolveUpdate!: (value: {
      data: { service: 'brave'; connector_id: string; revision: number }
    }) => void
    const store = useConnectorsStore()
    await store.loadProjectContext('project-a')
    api.setProjectDefault.mockImplementation(() => new Promise((resolve) => {
      resolveUpdate = resolve
    }) as never)

    const update = store.setDefault('project-a', 'brave', 'global-brave')
    await store.loadProjectContext('project-b')
    resolveUpdate({ data: { service: 'brave', connector_id: 'global-brave', revision: 4 } })
    await update

    expect(store.defaults.brave?.connector_id).toBeNull()
    expect(api.setProjectDefault).toHaveBeenCalledWith('project-a', 'brave', {
      expected_revision: 3,
      connector_id: 'global-brave',
    })
  })
})
