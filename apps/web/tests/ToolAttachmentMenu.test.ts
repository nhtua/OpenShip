import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolAttachmentMenu from '@/components/chat/ToolAttachmentMenu.vue'
import type { CapabilitySummary } from '@/types'

const capabilities: CapabilitySummary[] = [
  {
    id: 'brave_web_search',
    version: '1.0.0',
    manifest_digest: 'a'.repeat(64),
    input_schema: {},
    output_schema: {},
    effect: 'read_only_external',
    enabled: true,
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'tavily_web_search',
    version: '1.0.0',
    manifest_digest: 'b'.repeat(64),
    input_schema: {},
    output_schema: {},
    effect: 'read_only_external',
    enabled: true,
    available: false,
    unavailable_reason: 'missing_credentials',
  },
]

describe('ToolAttachmentMenu', () => {
  it('offers available tools, explains unavailable tools, and only attaches on selection', async () => {
    const wrapper = mount(ToolAttachmentMenu, {
      props: { capabilities, selectedCapabilityId: null, disabled: false },
    })

    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    expect(wrapper.text()).toContain('Brave Search')
    expect(wrapper.text()).toContain('Tavily Search')
    expect(wrapper.text()).toContain('Add a tool to this message')
    expect(wrapper.emitted('attach')).toBeFalsy()

    const tavily = wrapper.findAll('[data-testid="tool-choice"]').find((item) => item.text().includes('Tavily'))!
    expect(tavily.attributes('disabled')).toBeDefined()
    expect(tavily.text()).toContain('Add a search provider connection')

    await wrapper.findAll('[data-testid="tool-choice"]')[0].trigger('click')
    expect(wrapper.emitted('attach')?.[0]).toEqual([
      { id: 'brave_web_search', version: '1.0.0', manifest_digest: 'a'.repeat(64) },
    ])
  })

  it('closes with Escape and returns focus to the add button', async () => {
    const wrapper = mount(ToolAttachmentMenu, {
      props: { capabilities, selectedCapabilityId: null, disabled: false },
      attachTo: document.body,
    })
    const trigger = wrapper.get('button[aria-label="Add tool"]')
    await trigger.trigger('click')
    await wrapper.get('[data-testid="tool-menu"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[data-testid="tool-menu"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('moves between enabled tool choices with arrow keys', async () => {
    const wrapper = mount(ToolAttachmentMenu, {
      attachTo: document.body,
      props: {
        capabilities: capabilities.map((item) => ({
          ...item,
          available: true,
          unavailable_reason: null,
        })),
        selectedCapabilityId: null,
        disabled: false,
      },
    })
    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    const choices = wrapper.findAll('[data-testid="tool-choice"]')
    expect(document.activeElement).toBe(choices[0].element)
    await choices[0].trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(choices[1].element)
    await choices[1].trigger('keydown', { key: 'ArrowUp' })
    expect(document.activeElement).toBe(choices[0].element)
    wrapper.unmount()
  })
})
