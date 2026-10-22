import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatInput from '@/components/chat/ChatInput.vue'
import { modelProfileCatalog } from './helpers/chatFixtures'
import type { CapabilitySummary, ConnectorUsage } from '@/types'

const capability: CapabilitySummary = {
  id: 'brave_web_search', version: '2.0.0', manifest_digest: 'a'.repeat(64),
  input_schema: {}, output_schema: {}, effect: 'read_only_external', enabled: true,
  available: true, unavailable_reason: null, permitted: true,
  required_connector_kinds: ['brave'], connector_readiness: { brave: 'ready' },
}

const connectors: ConnectorUsage[] = [
  { id: 'global-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave', status: 'configured', revision: 2, is_default: true },
  { id: 'project-brave', kind: 'brave', visibility: 'project', name: 'Project Brave', status: 'configured', revision: 1, is_default: false },
]

describe('connector selection', () => {
  it('shows connector scope and submits the explicitly selected connector identity', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: modelProfileCatalog.profiles,
        defaultProfileId: 'primary-fixture', selectedProfileId: null, fallbackIds: [],
        catalogError: null, selectionDisabled: false, capabilities: [capability],
        connectors, defaultConnectorIds: { brave: 'global-brave' },
      },
    })

    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    await wrapper.get('[data-testid="tool-choice"]').trigger('click')
    const selector = wrapper.get('[data-testid="connector-selection"]')
    expect(selector.text()).toContain('Global')
    expect(selector.text()).toContain('Project')
    await selector.setValue('project-brave')
    await wrapper.get('textarea').setValue('search for managed connector docs')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('search')?.[0]?.[0]).toMatchObject({
      connector_selections: { brave: 'project-brave' },
    })
    wrapper.unmount()
  })

  it('keeps connector selection disabled when choices are ambiguous and no default exists', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        profiles: modelProfileCatalog.profiles,
        defaultProfileId: 'primary-fixture', selectedProfileId: null, fallbackIds: [],
        catalogError: null, selectionDisabled: false, capabilities: [capability], connectors,
        defaultConnectorIds: {},
      },
    })
    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    await wrapper.get('[data-testid="tool-choice"]').trigger('click')
    await wrapper.get('textarea').setValue('search')

    expect((wrapper.get('button[type="submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('Choose a connector')
    wrapper.unmount()
  })
})
