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
    checkGlobalConnector: vi.fn(),
    globalRequestStatus: vi.fn(),
    createProjectConnector: vi.fn(),
    replaceProjectCredential: vi.fn(),
    updateProjectConnector: vi.fn(),
    checkProjectConnector: vi.fn(),
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
    api.updateGlobalConnector.mockResolvedValue({ data: { id: 'global-brave-1', deleted: true } } as never)
    api.checkGlobalConnector.mockResolvedValue({ data: { status: 'valid', message: 'Brave Search accepted this API key.', revision: 3 } } as never)
    api.listGlobalConnectors.mockResolvedValue({ data: [] } as never)
    api.createProjectConnector.mockResolvedValue({ data: { id: 'legacy-created' } } as never)
    api.replaceProjectCredential.mockResolvedValue({ data: { id: 'legacy-brave-1' } } as never)
    api.updateProjectConnector.mockResolvedValue({ data: { id: 'legacy-brave-1', deleted: true } } as never)
    api.checkProjectConnector.mockResolvedValue({ data: { status: 'valid', message: 'Brave Search accepted this API key.', revision: 7 } } as never)
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
    expect(document.body.querySelector('[data-testid="connector-status"]')).toBeNull()
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
    const statusChip = document.body.querySelector('[data-testid="connector-status"]')
    expect(statusChip?.textContent).toContain('Configured')
    expect(statusChip?.querySelector('[aria-hidden="true"]')?.className).toContain('bg-emerald-500')
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
    expect(document.body.innerText).not.toContain('Revoke')
    wrapper.unmount()
  })

  it('uses the shared grey and red indicator colors for disabled and attention statuses', async () => {
    const disabled = mountDialog({ connector: { ...globalConnector, status: 'disabled' } })
    await flushPromises()
    expect(document.body.querySelector('[data-testid="connector-status"]')?.textContent).toContain('Disabled')
    expect(document.body.querySelector('[data-testid="connector-status"] [aria-hidden="true"]')?.className).toContain('bg-muted-foreground')
    disabled.unmount()

    const expired = mountDialog({ connector: { ...globalConnector, status: 'expired' } })
    await flushPromises()
    expect(document.body.querySelector('[data-testid="connector-status"]')?.textContent).toContain('Needs attention')
    expect(document.body.querySelector('[data-testid="connector-status"] [aria-hidden="true"]')?.className).toContain('bg-destructive')
    expired.unmount()
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

  it('puts Save, Check status, Revoke, and Add another in the credential action row', async () => {
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()
    const buttons = [...document.body.querySelectorAll('button')]
    const save = buttons.find((button) => button.textContent?.trim() === 'Save')!
    const check = buttons.find((button) => button.textContent?.trim() === 'Check status')!
    const revoke = buttons.find((button) => button.textContent?.trim() === 'Revoke')!
    const addAnother = buttons.find((button) => button.textContent?.trim() === 'Add another Brave Search')!

    expect(buttons.some((button) => button.textContent?.trim() === 'Rotate API key')).toBe(false)
    expect(save.parentElement).toBe(check.parentElement)
    expect(save.parentElement).toBe(revoke.parentElement)
    expect(save.parentElement).toBe(addAnother.parentElement)
    expect(revoke.className).toContain('border-destructive')
    expect(revoke.className).toContain('text-destructive')
    expect(revoke.className).toContain('bg-background')
    const chip = document.body.querySelector('[data-testid="connector-status"]')!
    expect(chip.className).toContain('border-emerald-500')
    expect(chip.querySelector('[aria-hidden="true"]')?.className).toContain('bg-emerald-500')
    wrapper.unmount()
  })

  it('keeps reconciliation available after an uncertain Save without resending the key', async () => {
    api.replaceGlobalCredential.mockRejectedValueOnce(Object.assign(new Error('write response lost'), {
      response: { status: 503 },
    }))
    api.globalRequestStatus
      .mockRejectedValueOnce(Object.assign(new Error('status unavailable'), { response: { status: 503 } }))
      .mockResolvedValueOnce({ data: { status: 'complete', result: { id: 'global-brave-1', status: 'configured' } } } as never)
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()
    const key = document.body.querySelector('input[type="password"]') as HTMLInputElement
    key.value = 'sample'
    key.dispatchEvent(new Event('input', { bubbles: true }))
    document.body.querySelector('form[data-testid="connector-credential-form"]')?.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    )
    await flushPromises()

    const button = (name: string) => [...document.body.querySelectorAll('button')]
      .find((item) => item.textContent?.trim() === name)!
    expect(document.body.textContent).toContain('Check request status')
    expect(button('Check request status').disabled).toBe(false)
    expect(button('Check status').disabled).toBe(true)
    expect(button('Revoke').disabled).toBe(true)
    expect(button('Add another Brave Search').disabled).toBe(true)
    expect(document.body.innerHTML).not.toContain('sample')

    button('Check request status').click()
    await flushPromises()

    expect(api.replaceGlobalCredential).toHaveBeenCalledTimes(1)
    expect(api.globalRequestStatus).toHaveBeenCalledTimes(2)
    expect(document.body.querySelector('[data-testid="connector-status"]')?.textContent).toContain('Configured')
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
    wrapper.unmount()
  })

  it('saves a Global replacement through its existing credential endpoint', async () => {
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()
    const key = document.body.querySelector('input[type="password"]') as HTMLInputElement
    key.value = 'sample'
    key.dispatchEvent(new Event('input', { bubbles: true }))
    document.body.querySelector('form[data-testid="connector-credential-form"]')?.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    )
    await flushPromises()

    expect(api.replaceGlobalCredential).toHaveBeenCalledWith(
      'global-brave-1', expect.any(String), { expected_revision: 3, api_key: 'sample' },
    )
    expect(document.body.querySelector('[role="dialog"]')).not.toBeNull()
    expect(document.body.querySelector('[data-testid="connector-status"]')?.textContent).toContain('Configured')
    wrapper.unmount()
  })

  it('uses the new connector revision for a second Save in the same dialog', async () => {
    api.replaceGlobalCredential
      .mockResolvedValueOnce({ data: { ...globalConnector, revision: 4 } } as never)
      .mockResolvedValueOnce({ data: { ...globalConnector, revision: 5 } } as never)
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()

    const saveCredential = async (keyValue: string) => {
      const key = document.body.querySelector('input[type="password"]') as HTMLInputElement
      key.value = keyValue
      key.dispatchEvent(new Event('input', { bubbles: true }))
      document.body.querySelector('form[data-testid="connector-credential-form"]')?.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true }),
      )
      await flushPromises()
    }

    await saveCredential('first-secret')
    await saveCredential('second-secret')

    expect(api.replaceGlobalCredential).toHaveBeenNthCalledWith(
      1, 'global-brave-1', expect.any(String), { expected_revision: 3, api_key: 'first-secret' },
    )
    expect(api.replaceGlobalCredential).toHaveBeenNthCalledWith(
      2, 'global-brave-1', expect.any(String), { expected_revision: 4, api_key: 'second-secret' },
    )
    wrapper.unmount()
  })

  it('checks status against the latest revision after saving in the same dialog', async () => {
    api.replaceGlobalCredential.mockResolvedValueOnce({
      data: { ...globalConnector, revision: 4 },
    } as never)
    api.checkGlobalConnector.mockResolvedValueOnce({
      data: { status: 'valid', message: 'Current key accepted.', revision: 4 },
    } as never)
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()
    const key = document.body.querySelector('input[type="password"]') as HTMLInputElement
    key.value = 'replacement-secret'
    key.dispatchEvent(new Event('input', { bubbles: true }))
    document.body.querySelector('form[data-testid="connector-credential-form"]')?.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }),
    )
    await flushPromises()

    const check = [...document.body.querySelectorAll('button')]
      .find((button) => button.textContent?.trim() === 'Check status')!
    check.click()
    await flushPromises()

    expect(document.body.querySelector('[data-testid="connector-check-result"]')?.textContent)
      .toContain('Current key accepted.')
    wrapper.unmount()
  })

  it('checks a global connector key and shows the provider result', async () => {
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()

    const check = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Check status',
    )
    check?.click()
    await flushPromises()
    expect(api.checkGlobalConnector).toHaveBeenCalledWith('global-brave-1')
    expect(document.body.querySelector('[data-testid="connector-check-result"]')?.textContent).toContain('Brave Search accepted this API key.')
    expect(document.body.querySelector('[data-testid="connector-check-result"] [aria-hidden="true"]')?.className).toContain('bg-emerald-500')
    wrapper.unmount()
  })

  it('confirms cross-project impact before deleting a revoked Global connector setup', async () => {
    const confirm = vi.fn(() => true)
    vi.stubGlobal('confirm', confirm)
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()
    const revoke = Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Revoke',
    )
    revoke?.click()
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith(
      'Revoke Shared Brave? This removes the shared setup for every project and clears project defaults that use it. This cannot be undone.',
    )
    expect(api.updateGlobalConnector).toHaveBeenCalledWith(
      'global-brave-1',
      expect.any(String),
      { expected_revision: 3, revoke: true },
    )
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    wrapper.unmount()
  })

  it('opens the additional credential form inside the selected connector dialog', async () => {
    const wrapper = mountDialog({ connector: globalConnector })
    await flushPromises()
    await Array.from(document.body.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Add another Brave Search',
    )?.click()
    await flushPromises()

    expect(document.body.querySelector('[role="dialog"]')?.textContent).toContain('Add another Brave Search')
    expect((document.body.querySelector('input[aria-label="Connector name"]') as HTMLInputElement).value).toBe('')
    expect(document.body.querySelector('input[type="password"]')).not.toBeNull()
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
