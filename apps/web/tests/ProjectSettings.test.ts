import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import ProjectSettings from '@/components/settings/ProjectSettings.vue'
import SettingsView from '@/views/SettingsView.vue'
import { useConnectorsStore } from '@/stores/connectors'
import { useProjectsStore } from '@/stores/projects'
import { connectorsApi, projectsApi } from '@/services/api'
import { mountWithRouter } from './helpers/mountWithRouter'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: [] }), post: vi.fn() },
  authApi: { logout: vi.fn() },
  artifactsApi: { list: vi.fn(), download: vi.fn(), delete: vi.fn() },
  projectsApi: {
    list: vi.fn(),
    create: vi.fn(),
    rename: vi.fn(),
    delete: vi.fn(),
  },
  connectorsApi: {
    listProjectConnectors: vi.fn(),
    getProjectDefault: vi.fn(),
    setProjectDefault: vi.fn(),
    getProjectCapabilities: vi.fn(),
  },
}))

const projectApi = vi.mocked(projectsApi)
const connectorApi = vi.mocked(connectorsApi)

async function mountProjectSettings(pinia: ReturnType<typeof createPinia>, projectId: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/projects/:projectId/settings', name: 'project-settings', component: { template: '<div />' } },
      { path: '/settings/global', name: 'global-settings', component: { template: '<div />' } },
    ],
  })
  await router.push(`/projects/${projectId}/settings`)
  await router.isReady()
  return mount(ProjectSettings, {
    props: { projectId },
    global: { plugins: [pinia, router] },
  })
}

const globalBrave = {
  id: 'global-brave',
  kind: 'brave',
  visibility: 'global',
  name: 'Shared Brave Search',
  status: 'configured',
  revision: 4,
  is_default: false,
} as const

const legacyBrave = {
  id: 'legacy-project-brave',
  kind: 'brave',
  visibility: 'project',
  name: 'Legacy private Brave',
  status: 'configured',
  revision: 2,
  is_default: true,
} as const

describe('Project Settings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    projectApi.list.mockResolvedValue({
      data: [{ id: 'project-a', name: 'Production' }],
    } as never)
    projectApi.rename.mockImplementation(async (id, payload) => ({
      data: { id, name: payload.name },
    } as never))
    projectApi.delete.mockResolvedValue({ data: undefined } as never)
    connectorApi.listProjectConnectors.mockResolvedValue({ data: [] } as never)
    connectorApi.getProjectDefault.mockImplementation(async (_id, service) => ({
      data: { service, connector_id: null, revision: 0 },
    } as never))
    connectorApi.setProjectDefault.mockImplementation(async (id, service, payload) => ({
      data: { service, connector_id: payload.connector_id, revision: payload.expected_revision + 1 },
    } as never))
    connectorApi.getProjectCapabilities.mockResolvedValue({ data: [] } as never)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows project lifecycle controls and explains empty global defaults', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const projects = useProjectsStore(pinia)
    projects.projects = [{ id: 'project-a', name: 'Production' }]
    projects.currentProjectId = 'project-a'

    const wrapper = await mountProjectSettings(pinia, 'project-a')
    await flushPromises()

    expect(wrapper.get('input[aria-label="Project name"]').element).toHaveProperty('value', 'Production')
    expect(wrapper.text()).toContain('Project Settings')
    expect(wrapper.text()).toContain('No global Brave Search connector is available')
    expect(wrapper.text()).toContain('No global Tavily Search connector is available')
    expect((wrapper.get('button[aria-label="Delete project"]').element as HTMLButtonElement).disabled).toBe(true)
  })

  it('offers only global connectors for new defaults and keeps the current private default visible', async () => {
    connectorApi.listProjectConnectors.mockResolvedValue({
      data: [globalBrave, legacyBrave],
    } as never)
    connectorApi.getProjectDefault.mockImplementation(async (_id, service) => ({
      data: {
        service,
        connector_id: service === 'brave' ? 'legacy-project-brave' : null,
        revision: service === 'brave' ? 5 : 0,
      },
    } as never))
    const pinia = createPinia()
    setActivePinia(pinia)
    const projects = useProjectsStore(pinia)
    projects.projects = [
      { id: 'project-a', name: 'Production' },
      { id: 'project-b', name: 'Staging' },
    ]
    projects.currentProjectId = 'project-a'

    const wrapper = await mountProjectSettings(pinia, 'project-a')
    await flushPromises()

    const braveDefault = wrapper.get('select[aria-label="Brave Search default"]')
    expect(braveDefault.text()).toContain('Shared Brave Search')
    expect(braveDefault.text()).toContain('Legacy private Brave')
    expect(wrapper.text()).toContain('Choose a Global connector to replace this legacy Project default')
    expect(braveDefault.findAll('option').filter((option) => option.attributes('value') === 'legacy-project-brave')).toHaveLength(1)

    await braveDefault.setValue('global-brave')
    await flushPromises()

    expect(connectorApi.setProjectDefault).toHaveBeenCalledWith('project-a', 'brave', {
      expected_revision: 5,
      connector_id: 'global-brave',
    })
    expect((braveDefault.element as HTMLSelectElement).value).toBe('global-brave')
  })

  it('explains a disabled current global default while allowing the owner to clear it', async () => {
    connectorApi.listProjectConnectors.mockResolvedValue({
      data: [{ ...globalBrave, status: 'disabled' }],
    } as never)
    connectorApi.getProjectDefault.mockImplementation(async (_id, service) => ({
      data: {
        service,
        connector_id: service === 'brave' ? 'global-brave' : null,
        revision: service === 'brave' ? 2 : 0,
      },
    } as never))
    const pinia = createPinia()
    setActivePinia(pinia)
    const projects = useProjectsStore(pinia)
    projects.projects = [{ id: 'project-a', name: 'Production' }]
    projects.currentProjectId = 'project-a'

    const wrapper = await mountProjectSettings(pinia, 'project-a')
    await flushPromises()

    const braveDefault = wrapper.get('select[aria-label="Brave Search default"]')
    expect(braveDefault.text()).toContain('disabled Global connector')
    expect(wrapper.text()).toContain('The current Global connector is disabled')
    expect((braveDefault.element as HTMLSelectElement).value).toBe('global-brave')
    expect((braveDefault.element as HTMLSelectElement).disabled).toBe(false)
  })

  it('keeps defaults independent when both projects choose the same global connector', async () => {
    connectorApi.listProjectConnectors.mockResolvedValue({ data: [globalBrave] } as never)
    connectorApi.getProjectDefault.mockImplementation(async (projectId, service) => ({
      data: {
        service,
        connector_id: projectId === 'project-a' && service === 'brave' ? 'global-brave' : null,
        revision: projectId === 'project-a' && service === 'brave' ? 1 : 0,
      },
    } as never))
    const pinia = createPinia()
    const projects = useProjectsStore(pinia)
    const connectors = useConnectorsStore(pinia)

    await connectors.loadProjectContext('project-a')
    await connectors.setDefault('project-a', 'brave', 'global-brave')
    projects.selectProject('project-b')
    await connectors.loadProjectContext('project-b')

    expect(connectors.defaults.brave?.connector_id).toBeNull()
    expect(connectorApi.setProjectDefault).toHaveBeenCalledWith('project-a', 'brave', {
      expected_revision: 1,
      connector_id: 'global-brave',
    })
    expect(connectorApi.getProjectDefault).toHaveBeenLastCalledWith('project-b', 'tavily')
  })

  it('shows the Project Settings and Global Settings tabs and routes', async () => {
    const { wrapper, router } = await mountWithRouter(SettingsView, '/projects/project-a/settings')
    await flushPromises()

    expect(wrapper.get('a[aria-label="Project Settings tab"]').attributes('href')).toBe('/projects/project-a/settings')
    expect(wrapper.get('a[aria-label="Global Settings tab"]').attributes('href')).toBe('/settings/global')
    await router.push('/settings/global')
    await flushPromises()
    expect(wrapper.text()).toContain('Global Settings')
    expect(wrapper.text()).toContain('installation operator')
  })

  it('keeps /connectors as the catalog route and redirects the old settings link', async () => {
    const router = (await import('@/router')).router
    const routes = router.getRoutes()
    expect(routes.some((route) => route.path === '/connectors')).toBe(true)
    expect(routes.find((route) => route.path === '/settings/connectors')?.redirect).toBe('/connectors')
  })
})
