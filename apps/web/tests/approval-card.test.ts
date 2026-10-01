import { mount } from '@vue/test-utils'
import ApprovalCard from '../src/components/ApprovalCard.vue'

describe('ApprovalCard', () => {
  test('renders approval card', () => {
    const wrapper = mount(ApprovalCard, {
      props: {
        title: 'Approve tool call',
        description: 'This will create a directory'
      }
    })
    expect(wrapper.text()).toContain('Approve tool call')
    expect(wrapper.text()).toContain('This will create a directory')
  })

  test('emits approve event', async () => {
    const wrapper = mount(ApprovalCard, {
      props: { title: 'Approve' }
    })
    await wrapper.find('.btn-approve').trigger('click')
    expect(wrapper.emitted('approve')).toBeTruthy()
  })

  test('emits reject event', async () => {
    const wrapper = mount(ApprovalCard, {
      props: { title: 'Approve' }
    })
    await wrapper.find('.btn-reject').trigger('click')
    expect(wrapper.emitted('reject')).toBeTruthy()
  })
})