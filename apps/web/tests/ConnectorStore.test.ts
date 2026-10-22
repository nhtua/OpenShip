import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useConnectorsStore } from '@/stores/connectors'
import { connectorsApi } from '@/services/api'

vi.mock('@/services/api', () => ({
  connectorsApi: {
    listProjects: vi.fn(),
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
  { id: 'project-tavily', kind: 'tavily', visibility: 'project', name: 'Project Tavily', status: 'configured', revision: 2, is_default: false },
] as const

describe('connector store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    api.listProjects.mockResolvedValue({ data: [{ id: 'project-1', name: 'Personal project' }] } as never)
    api.listProjectConnectors.mockResolvedValue({ data: [...projectConnectors] } as never)
    api.getProjectDefault.mockImplementation(async (_projectId, kind) => ({
      data: { service: kind, connector_id: kind === 'brave' ? 'global-brave' : null, revision: kind === 'brave' ? 3 : 0 },
    } as never))
    api.getProjectCapabilities.mockResolvedValue({ data: [] } as never)
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: false, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockRejectedValue({ response: { status: 403 } })
  })

  it('loads owned project context and treats global visibility separately from project readiness', async () => {
    const store = useConnectorsStore()
    await store.initialize(true)

    expect(store.projects).toEqual([{ id: 'project-1', name: 'Personal project' }])
    expect(store.currentProjectId).toBe('project-1')
    expect(store.connectors).toEqual([...projectConnectors])
    expect(store.defaults.brave?.connector_id).toBe('global-brave')
    expect(store.canManageGlobal).toBe(false)
    expect(store.globalConnectors).toEqual([])
    expect(store.credentialStoreReady).toBe(true)
    expect(api.getSetupContext).toHaveBeenCalledTimes(1)
    expect(api.listGlobalConnectors).not.toHaveBeenCalled()
  })

  it('only enables global controls when the protected operator endpoint succeeds', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockResolvedValue({ data: [{ ...projectConnectors[0], is_default: false }] } as never)
    const store = useConnectorsStore()
    await store.initialize(true)

    expect(store.canManageGlobal).toBe(true)
    expect(store.globalConnectors).toHaveLength(1)
    expect(store.globalConnectors[0].visibility).toBe('global')
    expect(api.listGlobalConnectors).toHaveBeenCalledTimes(1)
  })

  it('surfaces fixed readiness state while withholding operator-only catalog data', async () => {
    api.getSetupContext.mockResolvedValue({
      data: {
        can_manage_global: false,
        credential_store_ready: false,
        unavailable_reason: 'idempotency_key_unavailable',
      },
    } as never)
    const store = useConnectorsStore()
    await store.initialize(true)

    expect(store.credentialStoreReady).toBe(false)
    expect(store.setupUnavailableReason).toBe('idempotency_key_unavailable')
    expect(store.canManageGlobal).toBe(false)
    expect(api.listGlobalConnectors).not.toHaveBeenCalled()
  })

  it('keeps server management authorization authoritative after setup context', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockRejectedValue({ response: { status: 403 } })
    const store = useConnectorsStore()
    await store.initialize(true)

    expect(api.listGlobalConnectors).toHaveBeenCalledTimes(1)
    expect(store.canManageGlobal).toBe(false)
    expect(store.globalConnectors).toEqual([])
    expect(store.credentialStoreReady).toBe(true)
  })

  it('updates a project default with the revision it loaded', async () => {
    api.getProjectCapabilities.mockResolvedValue({
      data: [{
        id: 'brave_web_search', version: '2.0.0', manifest_digest: 'a'.repeat(64),
        input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
        required_connector_kinds: ['brave'], connector_readiness: { brave: 'ambiguous' },
      }],
    } as never)
    const store = useConnectorsStore()
    await store.initialize()
    api.setProjectDefault.mockResolvedValue({
      data: { service: 'brave', connector_id: 'project-brave', revision: 4 },
    } as never)
    api.getProjectCapabilities.mockResolvedValue({
      data: [{
        id: 'brave_web_search', version: '2.0.0', manifest_digest: 'a'.repeat(64),
        input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
        required_connector_kinds: ['brave'], connector_readiness: { brave: 'ready' },
      }],
    } as never)

    await store.setDefault('brave', 'project-brave')

    expect(api.setProjectDefault).toHaveBeenCalledWith('project-1', 'brave', {
      expected_revision: 3,
      connector_id: 'project-brave',
    })
    expect(store.defaults.brave?.connector_id).toBe('project-brave')
    expect(store.capabilities[0].connector_readiness.brave).toBe('ready')
  })
})
