import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SearchToolForm from '@/components/chat/SearchToolForm.vue'
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
    available: false,
    unavailable_reason: 'missing_credentials',
  },
  {
    id: 'tavily_web_search',
    version: '1.0.0',
    manifest_digest: 'b'.repeat(64),
    input_schema: {},
    output_schema: {},
    effect: 'read_only_external',
    enabled: true,
    available: true,
    unavailable_reason: null,
  },
]

describe('SearchToolForm', () => {
  it('keeps missing credentials isolated to that search tool', async () => {
    const wrapper = mount(SearchToolForm, { props: { capabilities } })
    const select = wrapper.get('select[aria-label="Search service"]')
    await select.setValue('brave_web_search')

    expect(wrapper.text()).toContain('Brave Search is not configured on this OpenShip installation.')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()

    await select.setValue('tavily_web_search')
    await wrapper.get('input[aria-label="Search query"]').setValue('terraform')
    expect(wrapper.text()).not.toContain('Brave Search is not configured on this OpenShip installation.')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
  })

  it('submits the selected service and bounded query without any credential input', async () => {
    const wrapper = mount(SearchToolForm, { props: { capabilities } })
    await wrapper.get('select[aria-label="Search service"]').setValue('tavily_web_search')
    await wrapper.get('input[aria-label="Search query"]').setValue('terraform module registry')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('submit')?.[0][0]).toEqual({
      capability: {
        id: 'tavily_web_search',
        version: '1.0.0',
        manifest_digest: 'b'.repeat(64),
      },
      arguments: { query: 'terraform module registry', max_results: 5 },
    })
    expect(wrapper.find('input[type="password"]').exists()).toBe(false)
  })
})
