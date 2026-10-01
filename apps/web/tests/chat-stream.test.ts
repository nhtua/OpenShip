import { mount } from '@vue/test-utils'
import ChatStream from '../src/components/ChatStream.vue'

describe('ChatStream', () => {
  test('renders empty state', () => {
    const wrapper = mount(ChatStream, {
      props: { messages: [] }
    })
    expect(wrapper.text()).toContain('Start a conversation')
  })

  test('renders user messages', () => {
    const messages = [{
      id: '1',
      role: 'user' as const,
      content: 'Hello',
      timestamp: Date.now()
    }]
    const wrapper = mount(ChatStream, { props: { messages } })
    expect(wrapper.find('.message-user').exists()).toBe(true)
    expect(wrapper.text()).toContain('Hello')
  })

  test('renders agent messages', () => {
    const messages = [{
      id: '1',
      role: 'agent' as const,
      content: 'Hi there',
      timestamp: Date.now()
    }]
    const wrapper = mount(ChatStream, { props: { messages } })
    expect(wrapper.find('.message-agent').exists()).toBe(true)
    expect(wrapper.text()).toContain('Hi there')
  })

  test('renders system messages', () => {
    const messages = [{
      id: '1',
      role: 'system' as const,
      content: 'System started',
      timestamp: Date.now()
    }]
    const wrapper = mount(ChatStream, { props: { messages } })
    expect(wrapper.find('.message-system').exists()).toBe(true)
    expect(wrapper.text()).toContain('System started')
  })
})