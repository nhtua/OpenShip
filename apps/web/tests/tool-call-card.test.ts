import { mount } from '@vue/test-utils'
import ToolCallCard from '../src/components/ToolCallCard.vue'

describe('ToolCallCard', () => {
  const toolCall = {
    id: '1',
    name: 'create_directory',
    args: { path: '/tmp/test' },
    status: 'success' as const
  }

  test('renders tool call name', () => {
    const wrapper = mount(ToolCallCard, { props: { toolCall } })
    expect(wrapper.text()).toContain('create_directory')
  })

  test('renders tool call status', () => {
    const wrapper = mount(ToolCallCard, { props: { toolCall } })
    expect(wrapper.text()).toContain('success')
  })

  test('renders tool call args', () => {
    const wrapper = mount(ToolCallCard, { props: { toolCall } })
    expect(wrapper.text()).toContain('/tmp/test')
  })

  test('shows success icon', () => {
    const wrapper = mount(ToolCallCard, { props: { toolCall } })
    expect(wrapper.find('.status-success').exists()).toBe(true)
  })
})