import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ConnectorConfigurationDialog from '@/components/connectors/ConnectorConfigurationDialog.vue'
import { pendingConnectorRequests } from '@/lib/pendingConnectorRequests'
import { connectorCatalog } from '@/lib/connectorCatalog'
import { connectorsApi } from '@/services/api'
import { useConnectorsStore } from '@/stores/connectors'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { logout: vi.fn() },
  artifactsApi: { list: vi.fn(), download: vi.fn(), delete: vi.fn() },
  connectorsApi: {
    createGlobalConnector: vi.fn(),
    replaceGlobalCredential: vi.fn(),
    updateGlobalConnector: vi.fn(),
    globalRequestStatus: vi.fn(),
    createProjectConnector: vi.fn(),
    replaceProjectCredential: vi.fn(),
    updateProjectConnector: vi.fn(),
    projectRequestStatus: vi.fn(),
    listGlobalConnectors: vi.fn(),
  },
}))

const api = vi.mocked(connectorsApi)
const brave = connectorCatalog.find((item) => item.kind === 'brave')!
const globalConnector = {
  id: 'global-brave-1', kind: 'brave', visibility: 'global', name: 'Shared Brave',
  status: 'configured', revision: 3, is_default: false,
} as const
const legacyConnector = {
  id: 'legacy-brave-1', kind: 'brave', visibility: 'project', name: 'Legacy Brave',
  status: 'configured', revision: 7, is_default: true,
} as const

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(ConnectorConfigurationDialog, {
    attachTo: document.body,
    props: {
      open: true,
      entry: brave,
      scope: 'global',
      connector: null,
      projectId: null,
      selectedProjectId: 'project-a',
      canManageGlobal: true,
      credentialStoreReady: true,
      ...props,
    },
  })
}

describe('connector configuration dialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setActivePinia(createPinia())
    pendingConnectorRequests.clear()
    api.createGlobalConnector.mockResolvedValue({ data: { id: 'global-created' } } as never)
    api.replaceGlobalCredential.mockResolvedValue({ data: { id: 'global-brave-1' } } as never)
    api.updateGlobalConnector.mockResolvedValue({ data: { id: 'global-brave-1' } } as never)
    api.listGlobalConnectors.mockResolvedValue({ data: [] } as never)
    api.createProjectConnector.mockResolvedValue({ data: { id: 'legacy-created' } } as never)
    api.replaceProjectCredential.mockResolvedValue({ data: { id: 'legacy-brave-1' } } as never)
    api.updateProjectConnector.mockResolvedValue({ data: { id: 'legacy-brave-1' } } as never)
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.unstubAllGlobals()
  })

  it('does not expose an API-key input while the dialog is closed', () => {
    const wrapper = mountDialog({ open: false })
    expect(document.body.querySelector('input[type="password"]')).toBeNull()
    wrapper.unmount()
  })

  it('clears the write-only input when the dialog closes and opens again', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const keyInput = document.body.querySelector('input[type="password"]') as HTMLInputElement
    keyInput.value = 'sample'
    keyInput.dispatchEvent(new Event('input', { bubbles: true }))
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.setProps({ open: false })
    await flushPromises()

    expect(document.body.querySelector('input[type="password"]')).toBeNull()
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect((document.body.querySelector('input[type="password"]') as HTMLInputElement).value).toBe('')
    wrapper.unmount()
  })

  it('opens an operator create form with fixed Global scope and writes no secret into stores or storage', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mountDialog()
    await flushPromises()

    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
    expect(document.body.querySelector('input[type="password"]')).not.toBeNull()
    expect(document.body.innerText).toContain('Global')
    expect(document.body.innerText).not.toContain('Project visibility')
    const nameInput = document.body.querySelector('input[aria-label="Connector name"]') as HTMLInputElement
    nameInput.value = 'Shared Brave'
    nameInput.dispatchEvent(new Event('input', { bubbles: true }))
    const keyInput = document.body.querySelector('input[type="password"]') as HTMLInputElement
    keyInput.value = 'sample'
    keyInput.dispatchEvent(new Event('input', { bubbles: true }))
    document.body.querySelector('form[data-testid="connector-credential-form"]')?.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    )
    await flushPromises()

    expect(api.createGlobalConnector).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      kind: 'brave', name: 'Shared Brave', configuration: {}, api_key: 'sample',
    }))
    expect(api.createGlobalConnector.mock.calls[0][1]).not.toHaveProperty('visibility')
    expect(useConnectorsStore(pinia)).not.toHaveProperty('apiKey')
    expect(JSON.stringify(useConnectorsStore(pinia).$state)).not.toContain('sample')
    expect(sessionStorage.getItem('connector-api-key')).toBeNull()
    expect(document.body.innerHTML).not.toContain('sample')
    wrapper.unmount()
  })

  it('shows a read-only usage view to consumers and gives operator guidance', async () => {
    const wrapper = mountDialog({ connector: globalConnector, canManageGlobal: false })
    await flushPromises()

    expect(document.body.textContent).toContain('Shared Brave')
    expect(document.body.textContent).toContain('installation operator')
    expect(document.body.querySelector('input[type="password"]')).toBeNull()
    expect(document.body.innerText).not.toContain('Revoke globally')
    wrapper.unmount()
  })

  it('reconciles a pending Global request through the Global endpoint even after project selection changes', async () => {
    const requestScope = 'global:create:brave'
    pendingConnectorRequests.set(requestScope, 'global-request-id')
    api.globalRequestStatus.mockResolvedValue({ data: { status: 'processing' } } as never)
    const wrapper = mountDialog({ selectedProjectId: 'project-a' })
    await wrapper.setProps({ selectedProjectId: 'project-b' })
    await flushPromises()

    expect(api.globalRequestStatus).toHaveBeenCalledWith('global-request-id')
    expect(api.projectRequestStatus).not.toHaveBeenCalled()
    expect(pendingConnectorRequests.get(requestScope)).toBe('global-request-id')
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    await wrapper.setProps({ open: false })
    await flushPromises()
    expect(document.body.querySelector('input[type="password"]')).toBeNull()
    expect(pendingConnectorRequests.get(requestScope)).toBe('global-request-id')
    wrapper.unmount()
  })

  it('retains legacy request reconciliation under its original project scope', async () => {
    const requestScope = 'project:project-a:credential:legacy-brave-1'
    pendingConnectorRequests.set(requestScope, 'legacy-request-id')
    api.projectRequestStatus.mockResolvedValue({ data: { status: 'processing' } } as never)
    const wrapper = mountDialog({
      entry: brave,
      scope: 'project',
      connector: legacyConnector,
      projectId: 'project-a',
      selectedProjectId: 'project-b',
    })
    await flushPromises()

    expect(api.projectRequestStatus).toHaveBeenCalledWith('project-a', 'legacy-request-id')
    expect(api.globalRequestStatus).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('replaces a legacy Project credential only through its original project API', async () => {
    const wrapper = mountDialog({
      scope: 'project',
      connector: legacyConnector,
      projectId: 'project-a',
    })
    await flushPromises()
    const keyInput = document.body.querySelector('input[type="password"]') as HTMLInputElement
    keyInput.value = 'sample'
    keyInput.dispatchEvent(new Event('input', { bubbles: true }))
    document.body.querySelector('form[data-testid="connector-credential-form"]')?.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    )
    await flushPromises()

    expect(api.replaceProjectCredential).toHaveBeenCalledWith(
      'project-a',
      'legacy-brave-1',
      expect.any(String),
      { expected_revision: 7, api_key: 'sample' },
    )
    expect(api.replaceGlobalCredential).not.toHaveBeenCalled()
    expect(api.createGlobalConnector).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('uses the existing authorized Global mutation when disabling the connector', async () => {
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()

    const disable = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Disable',
    )
    disable?.click()
    await flushPromises()
    expect(api.updateGlobalConnector).toHaveBeenCalledWith(
      'global-brave-1',
      expect.any(String),
      { expected_revision: 3, enabled: false },
    )
    wrapper.unmount()
  })

  it('confirms cross-project impact before revoking a Global connector', async () => {
    const confirm = vi.fn(() => true)
    vi.stubGlobal('confirm', confirm)
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()
    const revoke = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Revoke globally',
    )
    revoke?.click()
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith(
      'Revoke Shared Brave? This affects every project using this global connector.',
    )
    expect(api.updateGlobalConnector).toHaveBeenCalledWith(
      'global-brave-1',
      expect.any(String),
      { expected_revision: 3, revoke: true },
    )
    wrapper.unmount()
  })

  it('restores focus when Escape closes the dialog and clears the write-only form', async () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'Open configuration'
    document.body.append(trigger)
    trigger.focus()
    const wrapper = mountDialog({ returnFocusElement: trigger })
    await flushPromises()

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.setProps({ open: false })
    await flushPromises()

    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    expect(document.activeElement).toBe(trigger)
    wrapper.unmount()
  })
})
