import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatInput from '@/components/chat/ChatInput.vue'
import { modelProfileCatalog } from './helpers/chatFixtures'
import type { CapabilitySummary, ModelProfileItem } from '@/types'

const capabilities: CapabilitySummary[] = [
  {
    id: 'brave_web_search', version: '1.0.0', manifest_digest: 'a'.repeat(64),
    input_schema: {}, output_schema: {}, effect: 'read_only_external', enabled: true,
    available: true, unavailable_reason: null,
  },
]

function mountInput() {
  return mount(ChatInput, {
    props: {
      profiles: modelProfileCatalog.profiles,
      defaultProfileId: 'primary-fixture',
      selectedProfileId: null,
      fallbackIds: [],
      catalogError: null,
      selectionDisabled: false,
      capabilities,
      submissionError: null,
    },
  })
}

const toolCapableProfiles: ModelProfileItem[] = modelProfileCatalog.profiles.map((profile) => ({
  ...profile,
  capabilities: { ...profile.capabilities, tool_calls: profile.profile_id === 'primary-fixture' },
}))

describe('ChatInput', () => {
  it('shows keyboard instructions in the placeholder only at desktop widths', async () => {
    const matchMedia = vi.spyOn(window, 'matchMedia')
    const listeners = new Set<(event: MediaQueryListEvent) => void>()
    let matches = false
    const query = {
      get matches() {
        return matches
      },
      addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        listeners.add(listener)
      },
      removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        listeners.delete(listener)
      },
    } as unknown as MediaQueryList
    matchMedia.mockReturnValue(query)

    const wrapper = mountInput()
    try {
      const textarea = wrapper.get('textarea')
      expect(textarea.attributes('placeholder')).toBe('Message the agent...')

      matches = true
      for (const listener of listeners) listener({ matches } as MediaQueryListEvent)
      await nextTick()
      expect(textarea.attributes('placeholder')).toBe(
        'Message the agent...   Enter to send · Shift+Enter for a new line',
      )
    } finally {
      wrapper.unmount()
      matchMedia.mockRestore()
    }
  })

  it('sends ordinary chat when no tool is attached', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('hello agent')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('send')?.[0]).toEqual(['hello agent'])
    expect(wrapper.emitted('search')).toBeFalsy()
  })

  it('lets a supported model use available tools automatically without an attached tool', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities: [
          capabilities[0],
          { ...capabilities[0], id: 'tavily_web_search', available: false, unavailable_reason: 'missing_credentials' },
        ],
        submissionError: null,
      },
    })
    expect(wrapper.get('[data-testid="automatic-tool-chip"]').text()).toContain('Automatic tools')
    await wrapper.get('textarea').setValue('nearest star system')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.find('[data-testid="attached-tool"]').exists()).toBe(false)
    expect(wrapper.emitted('send')?.[0]).toEqual(['nearest star system', { enableTools: true }])
    expect(wrapper.emitted('search')).toBeFalsy()
  })

  it('hides the active-run warning but still blocks tool submission', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities,
        submissionError: null,
        toolSubmissionDisabled: false,
      },
    })

    expect(wrapper.get('[data-testid="automatic-tool-chip"]').exists()).toBe(true)
    await wrapper.setProps({ toolSubmissionDisabled: true })
    await wrapper.get('textarea').setValue('search while another run is active')

    expect(wrapper.find('span.text-destructive').exists()).toBe(false)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeFalsy()
    expect(wrapper.emitted('search')).toBeFalsy()
  })

  it('explains when automatic tools need a supported model while keeping manual search model-independent', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: 'secondary-fixture',
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities,
        submissionError: null,
      },
    })
    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    const automaticMode = wrapper.get('[data-testid="automatic-tool-mode"]')
    expect(automaticMode.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Choose a model with sequential tool-call support')

    await wrapper.get('button[data-testid="tool-choice"]').trigger('click')
    await wrapper.get('textarea').setValue('manual query')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('search')?.[0]?.[0]).toMatchObject({
      capability: { id: 'brave_web_search', version: '1.0.0' },
      arguments: { query: 'manual query' },
    })
  })

  it('keeps each unavailable search service independent from the available one', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities: [
          { ...capabilities[0], available: false, unavailable_reason: 'missing_credentials' },
          { ...capabilities[0], id: 'tavily_web_search', available: true, unavailable_reason: null },
        ],
        submissionError: null,
      },
    })
    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    const choices = wrapper.findAll('[data-testid="tool-choice"]')
    expect((choices[0].element as HTMLButtonElement).disabled).toBe(true)
    expect((choices[1].element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.get('[data-testid="automatic-tool-mode"]').attributes('disabled')).toBeUndefined()
    await choices[1].trigger('click')
    await wrapper.get('textarea').setValue('manual Tavily query')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('search')?.[0]?.[0]).toMatchObject({
      capability: { id: 'tavily_web_search', version: '1.0.0' },
    })
  })

  it('allows automatic mode when one enabled search service is available', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        capabilityError: null,
        selectionDisabled: false,
        capabilities: [
          { ...capabilities[0], available: false, unavailable_reason: 'missing_credentials' },
          { ...capabilities[0], id: 'tavily_web_search', available: true, unavailable_reason: null },
        ],
        submissionError: null,
      },
    })
    expect(wrapper.get('[data-testid="automatic-tool-chip"]').text()).toContain('Automatic tools')
    await wrapper.get('textarea').setValue('search without attaching either service')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.find('[data-testid="attached-tool"]').exists()).toBe(false)
    expect(wrapper.emitted('send')?.[0]).toEqual([
      'search without attaching either service', { enableTools: true },
    ])
  })

  it('allows automatic mode for a sole ready Connector without requiring a project default', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities: [{
          id: 'tavily_web_search', version: '2.0.0', manifest_digest: 'b'.repeat(64),
          input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
          required_connector_kinds: ['tavily'], connector_readiness: { tavily: 'ready' },
        }],
        connectors: [{
          id: 'tavily-global', kind: 'tavily', visibility: 'global', name: 'Tavily',
          status: 'configured', revision: 1, is_default: false,
        }],
        defaultConnectorIds: {},
        submissionError: null,
      },
    })

    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    expect(wrapper.get('[data-testid="automatic-tool-mode"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('textarea').setValue('search with the configured Tavily connector')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('send')?.[0]).toEqual([
      'search with the configured Tavily connector',
      { enableTools: true, connectorSelections: {} },
    ])
  })

  it('keeps automatic mode disabled when multiple Connectors need a project default', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities: [{
          id: 'tavily_web_search', version: '2.0.0', manifest_digest: 'b'.repeat(64),
          input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
          required_connector_kinds: ['tavily'], connector_readiness: { tavily: 'ambiguous' },
        }],
        connectors: [
          {
            id: 'tavily-global-one', kind: 'tavily', visibility: 'global', name: 'Tavily One',
            status: 'configured', revision: 1, is_default: false,
          },
          {
            id: 'tavily-global-two', kind: 'tavily', visibility: 'global', name: 'Tavily Two',
            status: 'configured', revision: 1, is_default: false,
          },
        ],
        defaultConnectorIds: {},
        submissionError: null,
      },
    })

    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    expect(wrapper.get('[data-testid="automatic-tool-mode"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('choose a project default when a provider has multiple eligible Connectors')
  })

  it('keeps automatic mode available when one permitted provider is ready and another is not', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities: [
          {
            id: 'brave_web_search', version: '2.0.0', manifest_digest: 'a'.repeat(64),
            input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
            required_connector_kinds: ['brave'], connector_readiness: { brave: 'required' },
          },
          {
            id: 'tavily_web_search', version: '2.0.0', manifest_digest: 'b'.repeat(64),
            input_schema: {}, output_schema: {}, effect: 'read_only_external', permitted: true,
            required_connector_kinds: ['tavily'], connector_readiness: { tavily: 'ready' },
          },
        ],
        connectors: [{
          id: 'tavily-global', kind: 'tavily', visibility: 'global', name: 'Tavily',
          status: 'configured', revision: 1, is_default: false,
        }],
        defaultConnectorIds: {},
        submissionError: null,
      },
    })

    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    expect(wrapper.get('[data-testid="automatic-tool-mode"]').attributes('disabled')).toBeUndefined()
  })

  it('lets the user turn off default automatic tool use for subsequent messages', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities: [{ ...capabilities[0], id: 'brave_web_search' }],
        submissionError: null,
      },
    })
    await wrapper.get('[data-testid="automatic-tool-chip"]').trigger('click')
    await wrapper.get('textarea').setValue('hello without search')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('send')?.[0]).toEqual(['hello without search'])
    expect(wrapper.find('[data-testid="automatic-tool-chip"]').exists()).toBe(false)
  })

  it('keeps automatic mode with the unsent draft and blocks it if the selected model changes', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities,
        submissionError: null,
      },
    })
    await wrapper.get('textarea').setValue('draft for a capable model')
    await wrapper.setProps({ selectedProfileId: 'secondary-fixture' })

    expect(wrapper.text()).toContain('Choose a model with sequential tool-call support')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeFalsy()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('draft for a capable model')
  })

  it('restores automatic tool mode together with a draft when submission fails', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: toolCapableProfiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: null,
        selectionDisabled: false,
        capabilities,
        submissionError: null,
      },
    })
    await wrapper.get('textarea').setValue('retry this with tools enabled')
    await wrapper.get('form').trigger('submit')
    await wrapper.setProps({ submissionError: 'Failed to submit message. Please try again.' })

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('retry this with tools enabled')
    if (wrapper.get('button[aria-label="Add tool"]').attributes('aria-expanded') === 'false') {
      await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    }
    expect(wrapper.get('[data-testid="automatic-tool-mode"]').attributes('aria-checked')).toBe('true')
  })

  it('attaches one tool, preserves the draft when removed, and submits a direct search', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('terraform modules')
    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    await wrapper.findAll('[data-testid="tool-choice"]')[0].trigger('click')

    expect(wrapper.get('[data-testid="attached-tool"]').text()).toContain('Brave Search')
    const count = wrapper.get('select[aria-label="Number of results"]')
    expect((count.element as HTMLSelectElement).value).toBe('5')
    expect(count.findAll('option')).toHaveLength(10)
    await count.setValue('8')
    await wrapper.get('button[aria-label="Remove Brave Search"]').trigger('click')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('terraform modules')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')?.[0]).toEqual(['terraform modules'])

    await wrapper.get('textarea').setValue('terraform modules')
    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    await wrapper.findAll('[data-testid="tool-choice"]')[0].trigger('click')
    const reattachedCount = wrapper.get('select[aria-label="Number of results"]')
    expect((reattachedCount.element as HTMLSelectElement).value).toBe('5')
    await reattachedCount.setValue('8')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('search')?.[0]).toEqual([{
      capability: { id: capabilities[0].id, version: capabilities[0].version, manifest_digest: capabilities[0].manifest_digest },
      arguments: { query: 'terraform modules', max_results: 8 },
    }])
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('restores a draft when submission fails and never submits an IME confirmation', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('keep this draft')
    await wrapper.get('textarea').trigger('keydown', { key: 'Enter', isComposing: true, keyCode: 229 })
    expect(wrapper.emitted('send')).toBeFalsy()

    await wrapper.get('form').trigger('submit')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    await wrapper.setProps({ submissionError: 'Failed to submit message. Please try again.' })
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('keep this draft')
  })
})
