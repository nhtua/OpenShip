import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'

// Chat component tests
import ChatStream from '@/components/chat/ChatStream.vue'
import ChatInput from '@/components/chat/ChatInput.vue'
import UserMessage from '@/components/chat/UserMessage.vue'
import AgentMessage from '@/components/chat/AgentMessage.vue'
import { useChatStore } from '@/stores/chat'
import type { ChatMessage } from '@/types'

const createMessages = (): ChatMessage[] => [
  {
    id: '1',
    role: 'user',
    content: 'Hello',
    conversation_id: null,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: '2',
    role: 'assistant',
    content: 'Hi there!',
    conversation_id: null,
    created_at: '2026-01-01T00:01:00Z',
  },
]

const streamingPlaceholder = (content = ''): ChatMessage => ({
  id: '',
  role: 'assistant',
  content,
  conversation_id: null,
  created_at: '2026-01-01T00:02:00Z',
})

// Deterministic scroll geometry for the stream container (plan Task 6.3).
function mockStreamGeometry(
  el: HTMLElement,
  scrollHeight: number,
  clientHeight: number,
) {
  Object.defineProperty(el, 'scrollHeight', {
    value: scrollHeight,
    configurable: true,
    writable: true,
  })
  Object.defineProperty(el, 'clientHeight', {
    value: clientHeight,
    configurable: true,
    writable: true,
  })
}

describe('ChatStream', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => {
    document.body.innerHTML = ''
  })

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

  it('shows centered empty state when no messages', () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: [],
        isStreaming: false,
      },
    })
    expect(wrapper.text()).toContain('Start a conversation')
    const empty = wrapper.get('[data-testid="empty-state"]')
    expect(empty.classes().join(' ')).toContain('justify-center')
  })

  it('shows streaming indicator on assistant message when streaming', () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: [streamingPlaceholder('Generating')],
        isStreaming: true,
      },
    })
    // AgentMessage receives isStreaming=true and shows the Agent label
    expect(wrapper.text()).toContain('Agent')
  })

  it('renders exactly one assistant row before and after the first chunk', async () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: [streamingPlaceholder()],
        isStreaming: true,
      },
    })
    expect(wrapper.findAll('.message').length).toBe(1)
    expect(wrapper.text()).toContain('Agent')

    await wrapper.setProps({
      messages: [streamingPlaceholder('He')],
      isStreaming: true,
    })
    expect(wrapper.findAll('.message').length).toBe(1)
    expect(wrapper.text()).toContain('He')

    await wrapper.setProps({
      messages: [
        { ...streamingPlaceholder('Hello'), id: 'm1' },
      ],
      isStreaming: false,
    })
    expect(wrapper.findAll('.message').length).toBe(1)
    expect(wrapper.text()).toContain('Hello')
    expect(wrapper.text()).not.toContain('Agent')
  })

  it('renders one standalone status when streaming with no messages', () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: [],
        isStreaming: true,
      },
    })
    expect(wrapper.findAll('.message').length).toBe(1)
    expect(wrapper.text()).toContain('Agent')
  })

  it('scrolls to the bottom on initial history load', async () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: createMessages(),
        isStreaming: false,
      },
    })
    const el = wrapper.get('[data-testid="stream"]').element as HTMLElement
    mockStreamGeometry(el, 500, 300)
    await nextTick()
    expect(el.scrollTop).toBe(500)
  })

  it('followsStreamingContentNearBottom', async () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: [streamingPlaceholder()],
        isStreaming: true,
      },
    })
    const el = wrapper.get('[data-testid="stream"]').element as HTMLElement
    mockStreamGeometry(el, 500, 300)
    await nextTick()
    // Initial load scrolled to the bottom; the reader is near the bottom.
    expect(el.scrollTop).toBe(500)

    // New chunk changes the last message content, not the array length.
    await wrapper.setProps({
      messages: [streamingPlaceholder('He')],
      isStreaming: true,
    })
    await nextTick()
    expect(el.scrollTop).toBe(500)
  })

  it('doesNotForceScrollWhenReadingHistory', async () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: createMessages(),
        isStreaming: false,
      },
    })
    const el = wrapper.get('[data-testid="stream"]').element as HTMLElement
    mockStreamGeometry(el, 500, 300)
    await nextTick()
    expect(el.scrollTop).toBe(500)

    // Simulate the user reading history: more than 48px from the bottom.
    el.scrollTop = 100
    el.dispatchEvent(new Event('scroll'))
    await nextTick()

    await wrapper.setProps({
      messages: [
        ...createMessages(),
        {
          id: '3',
          role: 'user',
          content: 'More?',
          conversation_id: null,
          created_at: '2026-01-01T00:02:00Z',
        },
      ],
      isStreaming: true,
    })
    await nextTick()
    expect(el.scrollTop).toBe(100)
    expect(wrapper.get('[data-testid="jump-to-latest"]').exists()).toBe(true)
  })

  it('jumpToLatestResumesFollowing', async () => {
    const wrapper = mount(ChatStream, {
      props: {
        messages: createMessages(),
        isStreaming: false,
      },
    })
    const el = wrapper.get('[data-testid="stream"]').element as HTMLElement
    mockStreamGeometry(el, 500, 300)
    await nextTick()

    el.scrollTop = 100
    el.dispatchEvent(new Event('scroll'))
    await nextTick()

    const jump = wrapper.get('[data-testid="jump-to-latest"]')
    await jump.trigger('click')
    await nextTick()
    expect(el.scrollTop).toBe(500)
    expect(wrapper.find('[data-testid="jump-to-latest"]').exists()).toBe(false)

    // The next chunk is followed again.
    await wrapper.setProps({
      messages: [
        ...createMessages(),
        {
          id: '4',
          role: 'assistant',
          content: 'New',
          conversation_id: null,
          created_at: '2026-01-01T00:03:00Z',
        },
      ],
      isStreaming: true,
    })
    await nextTick()
    expect(el.scrollTop).toBe(500)
  })
})

describe('ChatInput', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('renders composer textarea and send button', () => {
    const wrapper = mount(ChatInput)
    const textarea = wrapper.get('textarea[name="message"]')
    expect(textarea.exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(true)
    const btn = wrapper.find('button[type="submit"]')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('Send')
  })

  it('emits send event on form submit', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.get('textarea[name="message"]').setValue('Hello!')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeTruthy()
    expect(wrapper.emitted('send')?.[0]).toEqual(['Hello!'])
  })

  it('emits send event on Enter key', async () => {
    const wrapper = mount(ChatInput)
    const textarea = wrapper.get('textarea[name="message"]')
    await textarea.setValue('Hello!')
    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toBeTruthy()
    expect(wrapper.emitted('send')?.[0]).toEqual(['Hello!'])
  })

  it('shiftEnterDoesNotSend', async () => {
    const wrapper = mount(ChatInput)
    const textarea = wrapper.get('textarea[name="message"]')
    await textarea.setValue('Hello!')
    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeFalsy()
  })

  it('ignoresEnterDuringComposition', async () => {
    const wrapper = mount(ChatInput)
    const textarea = wrapper.get('textarea[name="message"]')
    await textarea.setValue('こんにちは')
    await textarea.trigger('keydown', { key: 'Enter', isComposing: true })
    expect(wrapper.emitted('send')).toBeFalsy()
    // The draft is preserved for the composition to continue.
    expect(
      (textarea.element as HTMLTextAreaElement).value,
    ).toBe('こんにちは')
  })

  it('does not send empty input', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeFalsy()
  })

  it('whitespaceDoesNotSend', async () => {
    const wrapper = mount(ChatInput)
    const textarea = wrapper.get('textarea[name="message"]')
    await textarea.setValue('   \n\t  ')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeFalsy()
    expect((textarea.element as HTMLTextAreaElement).value).toBe('   \n\t  ')
  })

  it('trims input before sending', async () => {
    const wrapper = mount(ChatInput)
    await wrapper.get('textarea[name="message"]').setValue('  hello  ')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')).toEqual([['hello']])
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(
      '',
    )
  })

  it('disabledSubmitDoesNotClearDraft', async () => {
    const wrapper = mount(ChatInput, { props: { disabled: true } })
    const textarea = wrapper.get('textarea[name="message"]')
    await textarea.setValue('draft')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('send')).toBeFalsy()
    expect((textarea.element as HTMLTextAreaElement).value).toBe('draft')
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
  afterEach(() => {
    document.body.innerHTML = ''
  })

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

  it('aligns right and keeps long unbroken text intact', () => {
    const longText = 'x'.repeat(300)
    const wrapper = mount(UserMessage, {
      props: {
        message: { ...createMessages()[0], content: longText },
      },
    })
    expect(wrapper.get('.message').classes().join(' ')).toContain('justify-end')
    expect(wrapper.get('p').text()).toBe(longText)
    // No v-html: the text stays text, never parsed markup.
    expect(wrapper.find('p > *').exists()).toBe(false)
  })

  it('escapes markup and preserves newlines', () => {
    const wrapper = mount(UserMessage, {
      props: {
        message: {
          ...createMessages()[0],
          content: '<b>bold</b>\nsecond line',
        },
      },
    })
    expect(wrapper.get('p').text()).toBe('<b>bold</b>\nsecond line')
    expect(wrapper.find('b').exists()).toBe(false)
  })
})

describe('AgentMessage', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => {
    document.body.innerHTML = ''
  })

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

  it('shows agent label when streaming', () => {
    const wrapper = mount(AgentMessage, {
      props: { message: createMessages()[1], isStreaming: true },
    })
    expect(wrapper.text()).toContain('Agent')
  })

  it('shows a thinking indicator only while streaming with empty content', async () => {
    const wrapper = mount(AgentMessage, {
      props: { message: streamingPlaceholder(), isStreaming: true },
    })
    expect(wrapper.text()).toContain('Agent')
    expect(wrapper.find('[data-testid="thinking-dots"]').exists()).toBe(true)

    await wrapper.setProps({
      message: streamingPlaceholder('He'),
      isStreaming: true,
    })
    expect(wrapper.text()).toContain('Agent')
    expect(wrapper.text()).toContain('He')
    expect(wrapper.find('[data-testid="thinking-dots"]').exists()).toBe(false)

    await wrapper.setProps({
      message: { ...streamingPlaceholder('He'), id: 'm1' },
      isStreaming: false,
    })
    expect(wrapper.text()).not.toContain('Agent')
    expect(wrapper.find('[data-testid="thinking-dots"]').exists()).toBe(false)
  })

  it('renders long unbroken text without breaking the layout', () => {
    const longText = 'a'.repeat(400)
    const wrapper = mount(AgentMessage, {
      props: {
        message: { ...createMessages()[1], content: longText },
      },
    })
    expect(wrapper.get('p').text()).toBe(longText)
    expect(wrapper.find('p > *').exists()).toBe(false)
  })
})

describe('ChatStore', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => {
    document.body.innerHTML = ''
  })

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
