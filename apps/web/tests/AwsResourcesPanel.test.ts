import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import AwsResourcesPanel from '@/components/connectors/AwsResourcesPanel.vue'
import { resourcesApi } from '@/services/api'
import type { ConnectorUsage, EnvironmentSummary, ResourceBindingSummary } from '@/types'

vi.mock('@/services/api', () => ({
  resourcesApi: {
    listEnvironments: vi.fn(),
    listBindings: vi.fn(),
    createEnvironment: vi.fn(),
    createBinding: vi.fn(),
    listObservations: vi.fn(),
  },
  artifactsApi: { download: vi.fn() },
}))

const api = vi.mocked(resourcesApi)
const connector = {
  id: 'aws-connector-1', kind: 'aws', visibility: 'project', name: 'Production AWS',
  status: 'configured', revision: 3, is_default: false,
} as ConnectorUsage
const environment: EnvironmentSummary = {
  id: 'environment-1', project_id: 'project-a', name: 'production', display_name: 'Production', created_at: '',
}
const binding: ResourceBindingSummary = {
  resource_id: 'resource-1', uri: 'aws:s3://production-data', display_name: 'Production data',
  environment_id: environment.id, environment_name: environment.name, binding_id: 'binding-1',
  binding_revision: 2, connector_id: connector.id, connector_revision: connector.revision,
  account_id: '123456789012', region: 'us-west-2', bucket_name: 'production-data',
  bucket_arn: 'arn:aws:s3:::production-data', expected_owner: '123456789012',
  management_method: 'unknown', state: 'active', created_at: '',
}

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(AwsResourcesPanel, {
    props: {
      projectId: 'project-a',
      connectors: [connector],
      environments: [],
      bindings: [],
      loading: false,
      ...props,
    },
  })
}

function setInput(wrapper: ReturnType<typeof mount>, id: string, value: string): void {
  const element = wrapper.get<HTMLInputElement>(`#${id}`).element
  element.value = value
  element.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('AwsResourcesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    api.listEnvironments.mockResolvedValue({ data: [environment] } as never)
    api.listBindings.mockResolvedValue({ data: { items: [binding], next_cursor: null } } as never)
    api.createEnvironment.mockResolvedValue({ data: environment } as never)
    api.createBinding.mockResolvedValue({ data: binding } as never)
    api.listObservations.mockResolvedValue({ data: { items: [], next_cursor: null } } as never)
  })

  it('creates project environments and binds a selected bucket to the current AWS connector revision', async () => {
    const wrapper = mountPanel()
    setInput(wrapper, 'resource-environment-name', 'production')
    setInput(wrapper, 'resource-environment-label', 'Production')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(api.createEnvironment).toHaveBeenCalledWith('project-a', {
      name: 'production', display_name: 'Production',
    })
    expect(api.createBinding).not.toHaveBeenCalled()
    await wrapper.setProps({ environments: [environment] })
    setInput(wrapper, 'binding-bucket-name', 'production-data')
    setInput(wrapper, 'binding-display-name', 'Production data')
    await wrapper.get('form:nth-of-type(2)').trigger('submit')
    await flushPromises()

    expect(api.createBinding).toHaveBeenCalledWith('project-a', {
      environment_id: environment.id,
      connector_id: connector.id,
      connector_revision: connector.revision,
      bucket_name: 'production-data',
      display_name: 'Production data',
    })
    wrapper.unmount()
  })

  it('queues bucket checks and loads retained observations for the selected binding', async () => {
    const wrapper = mountPanel({ environments: [environment], bindings: [binding] })
    const checkButton = wrapper.findAll('button').find((button) => button.text().includes('Check bucket'))
    await checkButton?.trigger('click')
    expect(wrapper.emitted('check')).toEqual([[connector.id, binding.binding_id]])

    await wrapper.get('button[aria-expanded="false"]').trigger('click')
    await flushPromises()
    expect(api.listObservations).toHaveBeenCalledWith('project-a', binding.resource_id)
    expect(wrapper.find('[data-testid="resource-observations"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
