import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ConnectorCredentialForm from '@/components/connectors/ConnectorCredentialForm.vue'

describe('connector credential setup', () => {
  it('clears a secret immediately and checks request status without replaying an uncertain write', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('response lost after commit'))
    const readStatus = vi.fn().mockResolvedValue({ status: 'processing' })
    const wrapper = mount(ConnectorCredentialForm, {
      props: {
        title: 'Brave Search',
        submitLabel: 'Add connector',
        submit,
        readStatus,
      },
    })

    const field = wrapper.get('input[type="password"]')
    await field.setValue('synthetic-secret-sentinel')
    await wrapper.get('form').trigger('submit')

    await vi.waitFor(() => expect((field.element as HTMLInputElement).value).toBe(''))
    await vi.waitFor(() => expect(wrapper.text()).toContain('still processing'))
    expect(submit).toHaveBeenCalledTimes(1)
    expect(readStatus).toHaveBeenCalledTimes(1)
    expect(wrapper.html()).not.toContain('synthetic-secret-sentinel')

    readStatus.mockResolvedValueOnce({ status: 'complete', result: { id: 'connector-1' } })
    await wrapper.get('[data-testid="check-connector-request"]').trigger('click')

    expect(submit).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('complete')?.[0]).toEqual([{ id: 'connector-1' }])
    wrapper.unmount()
  })

  it('allows key re-entry only after status confirms the request was not accepted', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('network lost'))
    const readStatus = vi.fn().mockRejectedValue({ response: { status: 404, data: { error: { code: 'connector_not_found' } } } })
    const wrapper = mount(ConnectorCredentialForm, {
      props: { title: 'Tavily Search', submitLabel: 'Add connector', submit, readStatus },
    })

    await wrapper.get('input[type="password"]').setValue('synthetic-key-value')
    await wrapper.get('form').trigger('submit')

    await vi.waitFor(() => expect(wrapper.text()).toContain('was not accepted'))
    expect(wrapper.get('input[type="password"]').attributes('disabled')).toBeUndefined()
    expect(submit).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('checks an unresolved request again after returning to the settings view', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('response lost'))
    const readStatus = vi.fn().mockResolvedValue({ status: 'processing' })
    const props = {
      title: 'Brave Search',
      submitLabel: 'Add connector',
      requestScopeKey: 'project:p1:create:brave',
      submit,
      readStatus,
    }
    const firstView = mount(ConnectorCredentialForm, { props })
    await firstView.get('input[type="password"]').setValue('another-synthetic-key')
    await firstView.get('form').trigger('submit')
    await vi.waitFor(() => expect(readStatus).toHaveBeenCalledTimes(1))
    firstView.unmount()

    const returnedView = mount(ConnectorCredentialForm, { props })
    await vi.waitFor(() => expect(readStatus).toHaveBeenCalledTimes(2))

    expect((returnedView.get('input[type="password"]').element as HTMLInputElement).disabled).toBe(true)
    expect(submit).toHaveBeenCalledTimes(1)
    returnedView.unmount()
  })
})
