import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { toolPolicyApi } from '@/services/api'
import { useToolPolicyStore } from '@/stores/toolPolicy'

vi.mock('@/services/api', () => ({
  toolPolicyApi: {
    get: vi.fn(),
    set: vi.fn(),
    cutover: vi.fn(),
  },
}))

const api = vi.mocked(toolPolicyApi)

function policy(revision: number, overrides: Record<string, unknown> = {}) {
  return {
    revision,
    tools: [{
      id: 'brave_web_search',
      description: 'Search the web with Brave.',
      enabled: true,
      permitted: true,
      deployment_restricted: false,
      unavailable_reason: null,
    }],
    effective_ids: ['brave_web_search'],
    deployment_ceiling_active: false,
    deployment_ceiling_ids: null,
    unknown_deployment_ids: [],
    cutover_available: false,
    legacy_cutover_at: null,
    ...overrides,
  }
}

describe('tool policy store', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    api.get.mockResolvedValue({ data: policy(7) } as never)
    api.set.mockResolvedValue({ data: policy(8, { effective_ids: [] }) } as never)
    api.cutover.mockResolvedValue({ data: policy(8, { cutover_available: false }) } as never)
  })

  it('loads one revisioned installation policy with deployment restrictions', async () => {
    api.get.mockResolvedValue({
      data: policy(11, {
        deployment_ceiling_active: true,
        deployment_ceiling_ids: ['brave_web_search'],
        tools: [{
          id: 'tavily_web_search',
          description: 'Search the web with Tavily.',
          enabled: true,
          permitted: false,
          deployment_restricted: true,
          unavailable_reason: 'deployment_restricted',
        }],
      }),
    } as never)
    const store = useToolPolicyStore()

    await store.load()

    expect(store.revision).toBe(11)
    expect(store.tools[0].deployment_restricted).toBe(true)
    expect(store.deploymentCeilingIds).toEqual(['brave_web_search'])
    expect(store.forbidden).toBe(false)
  })

  it('submits an operator change against the displayed revision and adopts the returned snapshot', async () => {
    const store = useToolPolicyStore()
    await store.load()

    await store.setEnabled('brave_web_search', false)

    expect(api.set).toHaveBeenCalledWith('brave_web_search', {
      enabled: false,
      expected_revision: 7,
    })
    expect(store.revision).toBe(8)
    expect(store.effectiveIds).toEqual([])
  })

  it('ignores an older policy response after a newer refresh completes', async () => {
    let resolveOld!: (value: { data: ReturnType<typeof policy> }) => void
    api.get
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }) as never)
      .mockResolvedValueOnce({ data: policy(12) } as never)
    const store = useToolPolicyStore()

    const oldLoad = store.load()
    await store.load()
    resolveOld({ data: policy(10) })
    await oldLoad

    expect(store.revision).toBe(12)
  })

  it('keeps management controls unavailable when the server rejects a non-operator', async () => {
    api.get.mockRejectedValue({ response: { status: 403 } })
    const store = useToolPolicyStore()

    await store.load()

    expect(store.forbidden).toBe(true)
    expect(store.tools).toEqual([])
  })

  it('adopts the current deployment ceiling once with its expected revision', async () => {
    api.get.mockResolvedValue({
      data: policy(20, {
        deployment_ceiling_active: true,
        deployment_ceiling_ids: [],
        cutover_available: true,
      }),
    } as never)
    const store = useToolPolicyStore()
    await store.load()

    await store.cutoverLegacyAllowlist()

    expect(api.cutover).toHaveBeenCalledWith({ expected_revision: 20 })
    expect(store.revision).toBe(8)
    expect(store.cutoverAvailable).toBe(false)
  })
})
