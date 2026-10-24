import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { h } from 'vue'
import ConnectorCredentialForm from '@/components/connectors/ConnectorCredentialForm.vue'

describe('ConnectorCredentialForm action slot', () => {
  it('keeps status reconciliation available while withholding the secret from slot consumers', async () => {
    const submit = vi.fn().mockRejectedValue(Object.assign(new Error('response uncertain'), {
      response: { status: 503 },
    }))
    const readStatus = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error('status unavailable'), { response: { status: 503 } }))
      .mockResolvedValueOnce({ status: 'complete', result: { status: 'configured' } })
    const wrapper = mount(ConnectorCredentialForm, {
      props: { title: 'Brave Search', submitLabel: 'Save', submit, readStatus },
      slots: {
        actions: ({ blocked, submitting }: { blocked: boolean; submitting: boolean }) =>
          h('button', { 'data-testid': 'slot-action', disabled: blocked }, `External action ${submitting}`),
      },
      attachTo: document.body,
    })
    const input = wrapper.get('[data-testid="connector-api-key"]')
    await input.setValue('secret-value')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const reconciliation = wrapper.get('[data-testid="check-connector-request"]')
    expect(reconciliation.attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="slot-action"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Check request status')
    expect(wrapper.html()).not.toContain('secret-value')

    await reconciliation.trigger('click')
    await flushPromises()

    expect(submit).toHaveBeenCalledTimes(1)
    expect(readStatus).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('complete')).toHaveLength(1)
    expect(wrapper.emitted('busyChange')).toEqual([[true], [false], [true], [false]])
    wrapper.unmount()
  })
})
