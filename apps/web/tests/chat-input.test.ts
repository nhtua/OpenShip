import { mount } from '@vue/test-utils'
import ChatInput from '../src/components/ChatInput.vue'

describe('ChatInput', () => {
  test('renders chat input', () => {
    const wrapper = mount(ChatInput)
    expect(wrapper.find('textarea').exists()).toBe(true)
    expect(wrapper.find('.send-btn').exists()).toBe(true)
  })

  test('emits send event with content', async () => {
    const wrapper = mount(ChatInput)
    const textarea = wrapper.find('textarea')
    await textarea.setValue('Test message')
    
    const sendBtn = wrapper.find('.send-btn')
    await sendBtn.trigger('click')
    
    expect(wrapper.emitted('send')).toBeTruthy()
    expect(wrapper.emitted('send')![0][0]).toBe('Test message')
  })

  test('send button is disabled when empty', () => {
    const wrapper = mount(ChatInput)
    const sendBtn = wrapper.find('.send-btn')
    expect(sendBtn.attributes('disabled')).toBeDefined()
  })

  test('send button is enabled when has content', async () => {
    const wrapper = mount(ChatInput)
    const textarea = wrapper.find('textarea')
    await textarea.setValue('Message')
    
    const sendBtn = wrapper.find('.send-btn')
    expect(sendBtn.attributes('disabled')).toBeUndefined()
  })

  test('respects loading prop', () => {
    const wrapper = mount(ChatInput, {
      props: { loading: true }
    })
    expect(wrapper.find('.send-btn').attributes('disabled')).toBeDefined()
  })
})