import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ChatInput from '@/components/chat/ChatInput.vue'
import { modelProfileCatalog } from './helpers/chatFixtures'
import type { CapabilitySummary } from '@/types'

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

describe('ChatInput', () => {
  it('sends ordinary chat when no tool is attached', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('hello agent')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('send')?.[0]).toEqual(['hello agent'])
    expect(wrapper.emitted('search')).toBeFalsy()
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
