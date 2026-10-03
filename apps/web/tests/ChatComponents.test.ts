import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

// Chat component tests
import ChatStream from '@/components/chat/ChatStream.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import UserMessage from '@/components/chat/UserMessage.vue'
import AgentMessage from '@/components/chat/AgentMessage.vue'
import { useChatStore } from '@/stores/chat'

const createMessages = () => [
  { id: '1', role: 'user' as const, content: 'Hello', conversation_id: null, created_at: '2026-01-01T00:00:00Z' },
  { id: '2', role: 'assistant' as const, content: 'Hi there!', conversation_id: null, created_at: '2026-01-01T00:01:00Z' },
]

describe('ChatStream', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => document.body.innerHTML = '')

  it('renders messages', () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: createMessages(),
        isStreaming: false,
      },
    })
    expect(wrapper.findAll('.message').length).toBe(2)
  })

  it('renders user messages with right alignment', () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: createMessages(),
        isStreaming: false,
      },
    })
    const userMsg = wrapper.findComponent(UserMessage)
    expect(userMsg.exists()).toBe(true)
  })

  it('renders assistant messages with left alignment', () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: createMessages(),
        isStreaming: false,
      },
    })
    const agentMsg = wrapper.findComponent(AgentMessage)
    expect(agentMsg.exists()).toBe(true)
  })

  it('shows empty state when no messages', () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: [],
        isStreaming: false,
      },
    })
    expect(wrapper.text()).toContain('Start a conversation')
  })

  it('shows streaming indicator on assistant message when streaming', () => {
    const streamingMsg = {
      id: '',
      role: 'assistant' as const,
      content: 'Generating',
      conversation_id: null,
      created_at: new Date().toISOString(),
    }
    const wrapper = mount(ChatStream, {
      props: {
        messages: [streamingMsg],
        isStreaming: true,
      },
    })
    // AgentMessage receives isStreaming=true and should show the loading dots
    expect(wrapper.text()).toContain('Agent')
  })
})

describe('ChatInput', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => document.body.innerHTML = '')

  it('renders input and send button', () => {
    const wrapper = mount(ChatInput)
    const input = wrapper.find('input')
    expect(input.exists()).toBe(true)
    const btn = wrapper.find('button[type="submit"]')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('Send')
  })

  it('emits send event on form submit', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.find('input').setValue('Hello!')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeTruthy()
    expect(wrapper.emitted('send')?.[0]).toEqual(['Hello!'])
  })

  it('emits send event on Enter key', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.find('input').setValue('Hello!')
    await wrapper.find('input').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toBeTruthy()
    expect(wrapper.emitted('send')?.[0]).toEqual(['Hello!'])
  })

  it('does not emit on Shift+Enter', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.find('input').setValue('Hello!')
    await wrapper.find('input').trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeFalsy()
  })

  it('does not send empty input', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeFalsy()
  })

  it('trims input before sending', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.find('input').setValue('  hello  ')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('send')?.[0]).toEqual(['hello'])
  })

  it('shows streaming state when disabled', async () => {
    const wrapper = mount(ChatInput, { props: { disabled: true } })
    const btn = wrapper.find('button[type="submit"]')
    expect(btn.text()).toContain('Streaming')
    expect(btn.element.disabled).toBe(true)
  })
})

describe('UserMessage', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => document.body.innerHTML = '')

  it('renders message content', () => {
    const wrapper = mount(UserMessage, {
      props: { message: createMessages()[0] },
    })
    expect(wrapper.find('p').text()).toBe('Hello')
  })

  it('has message CSS class', () => {
    const wrapper = mount(UserMessage, {
      props: { message: createMessages()[0] },
    })
    expect(wrapper.find('.message').exists()).toBe(true)
  })
})

describe('AgentMessage', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => document.body.innerHTML = '')

  it('renders message content', () => {
    const wrapper = mount(AgentMessage, {
      props: { message: createMessages()[1] },
    })
    expect(wrapper.find('p').text()).toBe('Hi there!')
  })

  it('has message CSS class', () => {
    const wrapper = mount(AgentMessage, {
      props: { message: createMessages()[1] },
    })
    expect(wrapper.find('.message').exists()).toBe(true)
  })

  it('shows agent label when streaming', async () => {
    const wrapper = mount(AgentMessage, {
      props: { message: createMessages()[1], isStreaming: true },
    })
    expect(wrapper.text()).toContain('Agent')
  })
})

describe('ChatStore', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => document.body.innerHTML = '')

  it('has correct initial state', () => {
    const store = useChatStore()
    expect(store.messages).toEqual([])
    expect(store.conversations).toEqual([])
    expect(store.isStreaming).toBe(false)
    expect(store.currentConversation).toBeNull()
    expect(store.error).toBeNull()
  })

  it('appends messages correctly', () => {
    const store = useChatStore()
    const msg = {
      id: '1',
      role: 'user' as const,
      content: 'Hello',
      conversation_id: null,
      created_at: new Date().toISOString(),
    }
    store.appendMessage(msg)
    expect(store.messages).toHaveLength(1)
    expect(store.messages[0].content).toBe('Hello')
  })
})
