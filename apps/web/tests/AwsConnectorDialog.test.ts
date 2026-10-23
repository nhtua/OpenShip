import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AwsConnectorDialog from '@/components/connectors/AwsConnectorDialog.vue'
import { pendingConnectorRequests } from '@/lib/pendingConnectorRequests'
import { connectorsApi } from '@/services/api'

vi.mock('@/services/api', () => ({
  connectorsApi: {
    getProjectConnector: vi.fn(),
    createProjectAwsConnector: vi.fn(),
    replaceProjectAwsCredential: vi.fn(),
    projectRequestStatus: vi.fn(),
    updateProjectConnector: vi.fn(),
  },
}))

const api = vi.mocked(connectorsApi)
const awsConnector = {
  id: 'aws-connector-1', kind: 'aws', visibility: 'project', name: 'Production AWS',
  status: 'configured', revision: 4, is_default: false,
} as const
const awsConfiguration = {
  schema_version: 1, kind: 'aws', region: 'us-west-2',
  expected_account_id: '123456789012', basic_role_arn: 'arn:aws:iam::123456789012:role/OpenShipRead',
}

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(AwsConnectorDialog, {
    attachTo: document.body,
    props: {
      open: true,
      projectId: 'project-a',
      connector: null,
      bindings: [],
      credentialStoreReady: true,
      ...props,
    },
  })
}

function input(id: string, value: string): void {
  const element = document.body.querySelector(`#${id}`) as HTMLInputElement
  element.value = value
  element.dispatchEvent(new Event('input', { bubbles: true }))
}

function submit(): void {
  document.body.querySelector('form[data-testid="aws-connector-form"]')?.dispatchEvent(
    new Event('submit', { bubbles: true, cancelable: true }),
  )
}

describe('AWS connector dialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pendingConnectorRequests.clear()
    api.getProjectConnector.mockResolvedValue({
      data: { name: 'Production AWS', configuration: awsConfiguration },
    } as never)
    api.createProjectAwsConnector.mockResolvedValue({ data: { id: 'new-aws-connector' } } as never)
    api.replaceProjectAwsCredential.mockResolvedValue({ data: { id: awsConnector.id } } as never)
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('sets up a project connector with a scoped role and sends write-only secrets without retaining them', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    input('aws-connector-name', 'Production AWS')
    input('aws-region', 'us-west-2')
    input('aws-account', '123456789012')
    input('aws-role-arn', 'arn:aws:iam::123456789012:role/OpenShipRead')
    input('aws-access-key', 'AKIAEXAMPLE')
    input('aws-secret-key', 'secret-example')
    submit()
    await flushPromises()

    expect(api.createProjectAwsConnector).toHaveBeenCalledWith('project-a', expect.any(String), {
      name: 'Production AWS',
      configuration: awsConfiguration,
      auth: { access_key_id: 'AKIAEXAMPLE', secret_access_key: 'secret-example' },
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(document.body.innerHTML).not.toContain('AKIAEXAMPLE')
    expect(document.body.innerHTML).not.toContain('secret-example')
    wrapper.unmount()
  })

  it('leaves replacement credential inputs enabled for an existing connector and uses its pinned revision', async () => {
    const wrapper = mountDialog({ connector: awsConnector })
    await flushPromises()
    input('aws-access-key', 'AKIAREPLACEMENT')
    input('aws-secret-key', 'replacement-secret')
    submit()
    await flushPromises()

    expect(api.replaceProjectAwsCredential).toHaveBeenCalledWith(
      'project-a',
      awsConnector.id,
      expect.any(String),
      {
        expected_revision: 4,
        auth: { access_key_id: 'AKIAREPLACEMENT', secret_access_key: 'replacement-secret' },
      },
    )
    expect(wrapper.emitted('saved')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps request reconciliation available after an uncertain credential replacement without resending the key', async () => {
    api.replaceProjectAwsCredential.mockRejectedValueOnce(Object.assign(new Error('write response lost'), {
      response: { status: 503 },
    }))
    api.projectRequestStatus
      .mockRejectedValueOnce(Object.assign(new Error('status unavailable'), { response: { status: 503 } }))
      .mockResolvedValueOnce({ data: { status: 'complete', result: { id: awsConnector.id } } } as never)
    const wrapper = mountDialog({ connector: awsConnector })
    await flushPromises()
    input('aws-access-key', 'AKIAUNCERTAIN')
    input('aws-secret-key', 'uncertain-secret')
    submit()
    await flushPromises()

    expect(document.body.textContent).toContain('Check request status before entering another key.')
    expect((document.body.querySelector('#aws-access-key') as HTMLInputElement).value).toBe('')
    expect(document.body.innerHTML).not.toContain('uncertain-secret')
    expect(api.projectRequestStatus).toHaveBeenCalledWith('project-a', expect.any(String))
    const statusButton = document.body.querySelector('[data-testid="check-aws-request-status"]') as HTMLButtonElement
    statusButton.click()
    await flushPromises()

    expect(api.replaceProjectAwsCredential).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('saved')).toHaveLength(1)
    wrapper.unmount()
  })
})
