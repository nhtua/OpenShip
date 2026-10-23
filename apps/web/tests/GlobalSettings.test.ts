import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import GlobalSettings from '@/components/settings/GlobalSettings.vue'
import { connectorsApi, toolPolicyApi } from '@/services/api'
import { useProjectsStore } from '@/stores/projects'

vi.mock('@/services/api', () => ({
  connectorsApi: {
    load: vi.fn(),
    listProjectConnectors: vi.fn(),
    getProjectDefault: vi.fn(),
    getProjectCapabilities: vi.fn(),
    getSetupContext: vi.fn(),
    listGlobalConnectors: vi.fn(),
  },
  toolPolicyApi: {
    get: vi.fn(),
    set: vi.fn(),
    cutover: vi.fn(),
  },
}))

const connectorRequests = vi.mocked(connectorsApi)
const policyRequests = vi.mocked(toolPolicyApi)
const unmounts: Array<() => void> = []

function response(revision = 4, overrides: Record<string, unknown> = {}) {
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

async function mountSettings() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = useProjectsStore(pinia)
  projects.projects = [{ id: 'project-a', name: 'Production' }]
  projects.currentProjectId = 'project-a'
  const wrapper = mount(GlobalSettings, {
    global: {
      plugins: [pinia],
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  })
  unmounts.push(() => wrapper.unmount())
  await flushPromises()
  return { wrapper, projects }
}

describe('GlobalSettings tool availability', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    policyRequests.get.mockResolvedValue({ data: response() } as never)
    policyRequests.set.mockResolvedValue({ data: response(5, { effective_ids: [] }) } as never)
    policyRequests.cutover.mockResolvedValue({
      data: response(5, {
        deployment_ceiling_active: true,
        cutover_available: false,
        legacy_cutover_at: '2026-10-23T12:00:00Z',
      }),
    } as never)
    connectorRequests.listProjectConnectors.mockResolvedValue({ data: [] } as never)
    connectorRequests.getProjectDefault.mockImplementation(async (_projectId, service) => ({
      data: { service, connector_id: null, revision: 0 },
    } as never))
    connectorRequests.getProjectCapabilities.mockResolvedValue({ data: [] } as never)
  })

  afterEach(() => {
    for (const unmount of unmounts.splice(0)) unmount()
    vi.clearAllMocks()
  })

  it('shows revisioned tool controls and refreshes project eligibility after a change', async () => {
    const { wrapper } = await mountSettings()

    const toggle = wrapper.get('[data-testid="tool-policy-toggle-brave_web_search"]')
    expect((toggle.element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.text()).toContain('Brave Search')

    await toggle.setValue(false)
    await flushPromises()

    expect(policyRequests.set).toHaveBeenCalledWith('brave_web_search', {
      enabled: false,
      expected_revision: 4,
    })
    expect(connectorRequests.getProjectCapabilities).toHaveBeenCalledWith('project-a')
  })

  it('hides installation controls from a non-operator', async () => {
    policyRequests.get.mockRejectedValue({ response: { status: 403 } })
    const { wrapper } = await mountSettings()

    expect(wrapper.text()).toContain('Only the installation operator can manage built-in tool availability.')
    expect(wrapper.find('[data-testid="tool-policy-toggle-brave_web_search"]').exists()).toBe(false)
  })

  it('explains deployment restrictions and previews the legacy allowlist before cutover', async () => {
    policyRequests.get.mockResolvedValue({
      data: response(12, {
        deployment_ceiling_active: true,
        deployment_ceiling_ids: ['brave_web_search'],
        unknown_deployment_ids: ['unknown_tool'],
        cutover_available: true,
        tools: [
          response().tools[0],
          {
            id: 'tavily_web_search',
            description: 'Search the web with Tavily.',
            enabled: true,
            permitted: false,
            deployment_restricted: true,
            unavailable_reason: 'deployment_restricted',
          },
        ],
      }),
    } as never)
    const { wrapper } = await mountSettings()

    expect(wrapper.text()).toContain('This tool is blocked by TOOL_ENABLED_IDS.')
    expect(wrapper.text()).toContain('Brave Search')
    expect(wrapper.text()).toContain('unknown_tool')
    await wrapper.get('[data-testid="tool-policy-cutover"]').trigger('click')
    await flushPromises()
    expect(policyRequests.cutover).toHaveBeenCalledWith({ expected_revision: 12 })
    expect(wrapper.text()).toContain('Remove TOOL_ENABLED_IDS from the API and worker environments')
  })

  it('refreshes the shared policy when the browser window regains focus', async () => {
    const { wrapper } = await mountSettings()
    policyRequests.get.mockResolvedValue({ data: response(5) } as never)

    window.dispatchEvent(new Event('focus'))
    await flushPromises()

    expect(policyRequests.get).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
