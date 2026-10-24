import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ConnectorCard from '@/components/connectors/ConnectorCard.vue'
import { connectorCatalog } from '@/lib/connectorCatalog'

const connector = (overrides: Record<string, unknown> = {}) => ({
  id: 'global-brave-1',
  kind: 'brave',
  visibility: 'global',
  name: 'Shared Brave',
  status: 'configured',
  revision: 3,
  is_default: false,
  ...overrides,
})

describe('Global connector catalog', () => {
  it('declares Brave and Tavily as OpenShip-published Global integrations with local initials', () => {
    expect(connectorCatalog).toEqual([
      expect.objectContaining({ kind: 'brave', name: 'Brave Search', publisher: 'OpenShip', visibility: 'global', initials: 'B' }),
      expect.objectContaining({ kind: 'tavily', name: 'Tavily Search', publisher: 'OpenShip', visibility: 'global', initials: 'T' }),
    ])
  })

  it('renders an unconfigured Global service card without rendering a credential input', async () => {
    const select = vi.fn()
    const entry = connectorCatalog.find((item) => item.kind === 'brave')!
    const wrapper = mount(ConnectorCard, { props: { entry, onSelect: select } })

    expect(wrapper.text()).toContain('Brave Search')
    expect(wrapper.text()).toContain('OpenShip')
    expect(wrapper.text()).toContain('Global')
    expect(wrapper.text()).toContain('Not configured')
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
    await wrapper.get('button').trigger('click')
    expect(select).toHaveBeenCalledOnce()
  })

  it('renders one immutable Global card per instance when a service has multiple instances', () => {
    const entry = connectorCatalog.find((item) => item.kind === 'brave')!
    const first = mount(ConnectorCard, { props: { entry, connector: connector() as never } })
    const second = mount(ConnectorCard, {
      props: { entry, connector: connector({ id: 'global-brave-2', name: 'Backup Brave' }) as never },
    })

    expect(first.text()).toContain('Shared Brave')
    expect(second.text()).toContain('Backup Brave')
    expect(first.attributes('data-visibility')).toBe('global')
    expect(second.attributes('data-visibility')).toBe('global')
  })

  it('shows configured, disabled, and needs-attention status labels without implying a live check', () => {
    const entry = connectorCatalog.find((item) => item.kind === 'tavily')!
    const configured = mount(ConnectorCard, {
      props: { entry, connector: connector({ id: 'configured', kind: 'tavily', name: 'Configured Tavily' }) as never },
    })
    const disabled = mount(ConnectorCard, {
      props: { entry, connector: connector({ id: 'disabled', kind: 'tavily', name: 'Disabled Tavily', status: 'disabled' }) as never },
    })
    const expired = mount(ConnectorCard, {
      props: { entry, connector: connector({ id: 'expired', kind: 'tavily', name: 'Expired Tavily', status: 'expired' }) as never },
    })

    expect(configured.text()).toContain('Configured')
    expect(disabled.text()).toContain('Disabled')
    expect(expired.text()).toContain('Needs attention')
    expect(configured.text()).not.toContain('Connection tested')
    expect(configured.attributes('data-status')).toBe('configured')
    expect(disabled.attributes('data-status')).toBe('disabled')
    expect(expired.attributes('data-status')).toBe('needs-attention')
  })
})
