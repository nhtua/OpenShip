import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ConnectorCredentialForm from '@/components/connectors/ConnectorCredentialForm.vue'
import { pendingConnectorRequests } from '@/lib/pendingConnectorRequests'
import { readConnectorError } from '@/lib/connectorErrors'

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
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).disabled).toBe(false)
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

  it('normalizes flat and FastAPI nested errors without trusting server messages', () => {
    const flat = readConnectorError({
      response: { status: 403, data: { error: { code: 'connector_origin_forbidden', message: 'unsafe raw detail' } } },
    })
    const nested = readConnectorError({
      response: { status: 403, data: { detail: { error: { code: 'connector_origin_forbidden', message: 'another raw detail' } } } },
    })

    expect(nested).toEqual(flat)
    expect(flat).toMatchObject({ code: 'connector_origin_forbidden', outcome: 'rejected' })
    expect(flat.message).not.toContain('raw detail')
  })

  it('does not status-poll a definite origin rejection and allows corrected re-entry', async () => {
    const scopeKey = 'global:create:origin-rejection'
    pendingConnectorRequests.delete(scopeKey)
    const submit = vi.fn()
      .mockRejectedValueOnce({
        response: {
          status: 403,
          data: { detail: { error: { code: 'connector_origin_forbidden', message: 'unsafe server detail' } } },
        },
      })
      .mockResolvedValueOnce({ id: 'global-connector-1' })
    const readStatus = vi.fn()
    const wrapper = mount(ConnectorCredentialForm, {
      props: { title: 'Brave Search', submitLabel: 'Add connector', requestScopeKey: scopeKey, submit, readStatus },
    })

    await wrapper.get('input[type="password"]').setValue('first-synthetic-key')
    await wrapper.get('form').trigger('submit')

    await vi.waitFor(() => expect(wrapper.text()).toContain('browser origin'))
    expect(readStatus).not.toHaveBeenCalled()
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).disabled).toBe(false)
    expect(wrapper.html()).not.toContain('unsafe server detail')
    expect(pendingConnectorRequests.has(scopeKey)).toBe(false)

    await wrapper.get('input[type="password"]').setValue('corrected-synthetic-key')
    await wrapper.get('form').trigger('submit')

    expect(submit).toHaveBeenCalledTimes(2)
    expect(readStatus).not.toHaveBeenCalled()
    expect(wrapper.emitted('complete')?.[0]).toEqual([{ id: 'global-connector-1' }])
    wrapper.unmount()
  })

  it('keeps a request blocked and explains credential storage failure when status returns 503', async () => {
    const submit = vi.fn().mockRejectedValue(new Error('response lost'))
    const readStatus = vi.fn().mockRejectedValue({
      response: {
        status: 503,
        data: { detail: { error: { code: 'credential_store_unavailable', message: 'raw storage internals' } } },
      },
    })
    const wrapper = mount(ConnectorCredentialForm, {
      props: { title: 'Tavily Search', submitLabel: 'Add connector', submit, readStatus },
    })

    await wrapper.get('input[type="password"]').setValue('synthetic-storage-key')
    await wrapper.get('form').trigger('submit')

    await vi.waitFor(() => expect(wrapper.text()).toContain('credential storage is unavailable'))
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).disabled).toBe(true)
    expect(wrapper.get('[data-testid="check-connector-request"]').exists()).toBe(true)
    expect(wrapper.html()).not.toContain('raw storage internals')
    expect(submit).toHaveBeenCalledTimes(1)
    expect(readStatus).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it.each([
    { status: 404, data: { error: { code: 'connector_not_found' } } },
    { status: 410, data: { detail: { error: { code: 'connector_request_expired' } } } },
  ])('allows re-entry after nested status failure $status', async (response) => {
    const submit = vi.fn().mockRejectedValue(new Error('response lost'))
    const readStatus = vi.fn().mockRejectedValue({ response })
    const wrapper = mount(ConnectorCredentialForm, {
      props: { title: 'Brave Search', submitLabel: 'Add connector', submit, readStatus },
    })

    await wrapper.get('input[type="password"]').setValue('synthetic-recovery-key')
    await wrapper.get('form').trigger('submit')

    await vi.waitFor(() => expect(wrapper.text()).toContain('was not accepted'))
    expect((wrapper.get('input[type="password"]').element as HTMLInputElement).disabled).toBe(false)
    wrapper.unmount()
  })
})
