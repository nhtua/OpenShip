import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ConnectorSettingsView from '@/views/ConnectorSettingsView.vue'
import { connectorsApi, projectsApi } from '@/services/api'
import { useChatStore } from '@/stores/chat'
import { useProjectsStore } from '@/stores/projects'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { logout: vi.fn() },
  artifactsApi: { list: vi.fn(), download: vi.fn(), delete: vi.fn() },
  projectsApi: { list: vi.fn(), create: vi.fn(), rename: vi.fn(), delete: vi.fn() },
  connectorsApi: {
    listProjectConnectors: vi.fn(),
    getProjectDefault: vi.fn(),
    setProjectDefault: vi.fn(),
    getProjectCapabilities: vi.fn(),
    getSetupContext: vi.fn(),
    listGlobalConnectors: vi.fn(),
    createGlobalConnector: vi.fn(),
    replaceGlobalCredential: vi.fn(),
    updateGlobalConnector: vi.fn(),
    globalRequestStatus: vi.fn(),
    createProjectConnector: vi.fn(),
    replaceProjectCredential: vi.fn(),
    updateProjectConnector: vi.fn(),
    projectRequestStatus: vi.fn(),
  },
}))

const api = vi.mocked(connectorsApi)
const projectApi = vi.mocked(projectsApi)

const globalBrave = {
  id: 'global-brave-1', kind: 'brave', visibility: 'global', name: 'Shared Brave',
  status: 'configured', revision: 3, is_default: false,
} as const
const legacyBrave = {
  id: 'legacy-brave-1', kind: 'brave', visibility: 'project', name: 'Legacy private Brave',
  status: 'configured', revision: 6, is_default: true,
} as const

async function mountCatalog() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = useProjectsStore(pinia)
  projects.projects = [{ id: 'project-a', name: 'Production' }, { id: 'project-b', name: 'Staging' }]
  projects.currentProjectId = 'project-a'
  const chat = useChatStore(pinia)
  chat.conversations = [{ id: 'conversation-a', title: 'Existing', project_id: 'project-a' } as never]
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/connectors', name: 'connectors', component: { template: '<div />' } },
      { path: '/projects/:projectId/settings', name: 'project-settings', component: { template: '<div />' } },
      { path: '/conversations/:conversationId', name: 'conversation', component: { template: '<div />' } },
    ],
  })
  await router.push('/connectors')
  await router.isReady()
  const wrapper = mount(ConnectorSettingsView, {
    global: {
      plugins: [pinia, router],
      stubs: { AppLayout: { template: '<div class="app-layout"><slot /></div>' } },
    },
  })
  await flushPromises()
  return { wrapper, projects, router, pinia }
}

describe('Connectors catalog page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    projectApi.list.mockResolvedValue({ data: [{ id: 'project-a', name: 'Production' }] } as never)
    api.listProjectConnectors.mockResolvedValue({ data: [] } as never)
    api.getProjectDefault.mockImplementation(async (_projectId, service) => ({
      data: { service, connector_id: null, revision: 0 },
    } as never))
    api.setProjectDefault.mockResolvedValue({ data: undefined } as never)
    api.getProjectCapabilities.mockResolvedValue({ data: [] } as never)
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: false, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockResolvedValue({ data: [] } as never)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows Global cards for both supported services before any configuration form is selected', async () => {
    const { wrapper } = await mountCatalog()

    expect(wrapper.findAll('[data-visibility="global"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('Brave Search')
    expect(wrapper.text()).toContain('Tavily Search')
    expect(wrapper.text()).toContain('Not configured')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Project visibility')
    expect(wrapper.text()).not.toContain('Add a project connector')
    expect(wrapper.findAll('[data-connector-card-grid] button')).toHaveLength(2)
    expect(wrapper.findAll('[data-connector-card-grid] button[aria-label^="Add"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('keeps legacy private connectors and their migration guidance outside the Global card list', async () => {
    api.listProjectConnectors.mockResolvedValue({ data: [
      legacyBrave,
      { id: 'project-aws', kind: 'aws', visibility: 'project', name: 'Project AWS', status: 'configured', revision: 1, is_default: false },
    ] } as never)
    api.getProjectDefault.mockImplementation(async (_projectId, service) => ({
      data: { service, connector_id: service === 'brave' ? legacyBrave.id : null, revision: 4 },
    } as never))
    const { wrapper } = await mountCatalog()

    const cards = wrapper.findAll('[data-visibility="global"]')
    expect(cards.map((card) => card.text().includes('Legacy private Brave'))).toEqual([false, false])
    expect(wrapper.find('[data-legacy-connectors]').text()).toContain('Legacy private Brave')
    expect(wrapper.find('[data-legacy-connectors]').text()).not.toContain('Project AWS')
    expect(wrapper.text()).toContain('Choose a Global connector to replace this legacy Project default')
    wrapper.unmount()
  })

  it('shows a page error when catalog data fails instead of reporting services as outdated', async () => {
    api.listProjectConnectors.mockRejectedValue(new Error('synthetic catalog failure'))
    const { wrapper } = await mountCatalog()

    expect(wrapper.get('[role="alert"]').text()).toContain('Failed to load project connector settings')
    expect(wrapper.findAll('[data-status="needs-attention"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-visibility="global"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('shows a page error when operator catalog loading fails', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockRejectedValue(new Error('synthetic global-list failure'))
    const { wrapper } = await mountCatalog()

    expect(wrapper.get('[role="alert"]').text()).toContain('Failed to load the global connector catalog')
    expect(wrapper.findAll('[data-status="needs-attention"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-visibility="global"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('shows separate Global instance cards and puts add-another inside the selected dialog', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockResolvedValue({ data: [
      globalBrave,
      { ...globalBrave, id: 'global-brave-2', name: 'Backup Brave' },
    ] } as never)
    api.listProjectConnectors.mockResolvedValue({ data: [globalBrave] } as never)
    const { wrapper } = await mountCatalog()

    expect(wrapper.findAll('[data-kind="brave"][data-visibility="global"]')).toHaveLength(2)
    expect(wrapper.findAll('[data-connector-card-grid] button')).toHaveLength(3)
    expect(wrapper.findAll('[data-connector-card-grid] button[aria-label^="Add"]')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Add another Brave Search')
    expect(wrapper.text()).not.toContain('Add another Tavily Search')
    await wrapper.get('button[aria-label="Configure Shared Brave"]').trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
    expect(document.body.textContent).toContain('Add another Brave Search')
    await Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Add another Brave Search',
    )?.click()
    await flushPromises()
    expect(document.body.querySelector('[role="dialog"]')?.textContent).toContain('Add another Brave Search')
    expect(document.body.querySelector('input[type="password"]')).not.toBeNull()
    expect(document.body.textContent).not.toContain('Project visibility')
    wrapper.unmount()
  })

  it('opens the create form by selecting an unconfigured connector card', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    const { wrapper } = await mountCatalog()

    await wrapper.get('button[aria-label="Configure Brave Search"]').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
    expect(document.body.querySelector('input[type="password"]')).not.toBeNull()
    expect(wrapper.findAll('[data-connector-card-grid] button')).toHaveLength(2)
    expect(wrapper.findAll('[data-connector-card-grid] button[aria-label^="Add"]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('opens the selected configured instance details without closing the dialog on its first render', async () => {
    api.getSetupContext.mockResolvedValue({
      data: { can_manage_global: true, credential_store_ready: true, unavailable_reason: null },
    } as never)
    api.listGlobalConnectors.mockResolvedValue({ data: [globalBrave] } as never)
    api.listProjectConnectors.mockResolvedValue({ data: [globalBrave] } as never)
    const { wrapper } = await mountCatalog()

    await wrapper.get('button[aria-label="Configure Shared Brave"]').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
    expect(document.body.textContent).toContain('Revoke')
    expect(document.body.textContent).toContain('Check status')
    expect(document.body.textContent).toContain('Add another Brave Search')
    wrapper.unmount()
  })
})
