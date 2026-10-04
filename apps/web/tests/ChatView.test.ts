import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mountWithRouter } from './helpers/mountWithRouter'
import ChatView from '@/views/ChatView.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import api from '@/services/api'
import type { Conversation } from '@/types'
import {
  apiError,
  fixtureConversations,
  fixtureUser,
  healthyChatApiGet,
} from './helpers/chatFixtures'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
}))

const mockApi = vi.mocked(api)

const wait = 3000

function findByText(
  wrapper: { findAll(selector: string): Array<{ text(): string }> },
  text: string,
) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

describe('ChatView', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    localStorage.clear()
    vi.clearAllMocks()
    mockApi.get.mockImplementation(
      (async (url: string) => healthyChatApiGet(url)) as never,
    )
    mockApi.post.mockResolvedValue({ data: null })
  })

  it('renders conversation history with the active selection', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')

    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('Deploy pipeline')
        expect(wrapper.text()).toContain('Scale up web')
      },
      { timeout: wait },
    )

    const row = findByText(wrapper, 'Scale up web')!
    await row.trigger('click')

    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('Scale web to 3 replicas')
      },
      { timeout: wait },
    )
    expect(row.attributes('data-active')).toBe('true')
  })

  it('creates and selects a conversation with a trimmed title', async () => {
    const created: Conversation = {
      id: 'c3',
      title: '',
      created_at: '2026-01-03T00:00:00Z',
      updated_at: '2026-01-03T00:00:00Z',
    }
    mockApi.post.mockImplementation(
      (async (url: string, body?: { title?: string }) => {
        if (url === '/conversations') {
          return { data: { ...created, title: body?.title ?? 'New Conversation' } }
        }
        throw new Error(`unexpected POST ${url}`)
      }) as never,
    )

    const { wrapper } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(
      () => {
        expect(findByText(wrapper, 'New Conversation')).toBeTruthy()
      },
      { timeout: wait },
    )

    await findByText(wrapper, 'New Conversation')!.trigger('click')
    const input = wrapper.find('input[name="new-conversation-title"]')
    expect(input.exists()).toBe(true)
    await input.setValue('  My new chat  ')
    await wrapper.find('form').trigger('submit')

    expect(mockApi.post).toHaveBeenCalledTimes(1)
    expect(mockApi.post).toHaveBeenCalledWith('/conversations', {
      title: 'My new chat',
    })

    await vi.waitFor(
      () => {
        expect(mockApi.get).toHaveBeenCalledWith('/conversations/c3/messages')
      },
      { timeout: wait },
    )

    const row = findByText(wrapper, 'My new chat')!
    expect(row.attributes('data-active')).toBe('true')
  })

  it('shows an actionable error with Retry when history loading fails', async () => {
    mockApi.get.mockRejectedValueOnce(apiError('Server exploded'))
    const { wrapper } = await mountWithRouter(ChatView, '/')

    await vi.waitFor(
      () => {
        expect(wrapper.find('[role="alert"]').exists()).toBe(true)
      },
      { timeout: wait },
    )
    expect(wrapper.find('[role="alert"]').text()).toContain('Server exploded')

    await findByText(wrapper, 'Retry')!.trigger('click')

    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('Deploy pipeline')
        expect(wrapper.find('[role="alert"]').exists()).toBe(false)
      },
      { timeout: wait },
    )
    // Retry hit the history endpoint again, not a message resend.
    expect(mockApi.get).toHaveBeenCalledWith('/workspace/conversations')
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('dismisses a chat error without retrying the failed request', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('Deploy pipeline')
      },
      { timeout: wait },
    )

    const getBefore = mockApi.get.mock.calls.length
    mockApi.get.mockImplementation(
      (async (url: string) => {
        if (String(url).endsWith('/messages')) {
          throw apiError('Conversation unavailable')
        }
        return healthyChatApiGet(url)
      }) as never,
    )

    await findByText(wrapper, 'Deploy pipeline')!.trigger('click')

    await vi.waitFor(
      () => {
        expect(wrapper.find('[role="alert"]').text()).toContain(
          'Conversation unavailable',
        )
      },
      { timeout: wait },
    )

    await findByText(wrapper, 'Dismiss')!.trigger('click')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    // Dismiss does not refetch anything.
    expect(mockApi.get.mock.calls.length).toBe(getBefore + 1)
  })

  it('logout clears chat state and returns to login', async () => {
    const { wrapper, router } = await mountWithRouter(ChatView, '/')
    const auth = useAuthStore()
    auth.token = 'test-token'
    auth.user = fixtureUser

    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('testuser')
      },
      { timeout: wait },
    )

    await findByText(wrapper, 'Sign Out')!.trigger('click')

    expect(auth.token).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
    const chat = useChatStore()
    expect(chat.messages).toEqual([])
    expect(chat.conversations).toEqual([])
    expect(chat.error).toBeNull()
    await vi.waitFor(
      () => {
        expect(router.currentRoute.value.name).toBe('login')
      },
      { timeout: wait },
    )
  })

  it('disables selection and creation while streaming', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    const chat = useChatStore()
    chat.isStreaming = true

    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('Deploy pipeline')
      },
      { timeout: wait },
    )

    const newButton = findByText(wrapper, 'New Conversation')!
    expect((newButton.element as HTMLButtonElement).disabled).toBe(true)
    const row = findByText(wrapper, 'Deploy pipeline')!
    expect((row.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps the empty chat state when there are no messages', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('Deploy pipeline')
      },
      { timeout: wait },
    )
    expect(wrapper.text()).toContain('Start a conversation')
    expect(fixtureConversations).toHaveLength(2)
  })
})
