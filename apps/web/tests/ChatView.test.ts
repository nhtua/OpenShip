import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithRouter } from './helpers/mountWithRouter'
import ChatView from '@/views/ChatView.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import api, { runsApi } from '@/services/api'
import type { Conversation, RunCard as RunCardType, ToolOperationSummary } from '@/types'
import {
  apiError,
  fixtureConversations,
  fixtureUser,
  healthyChatApiGet,
} from './helpers/chatFixtures'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  // The view reaches the durable endpoints only through a run's card:
  // cancelling asks, and the read answers with what the cancellation left.
  runsApi: {
    submitTurn: vi.fn(),
    cancelRun: vi.fn(),
    getSnapshot: vi.fn(),
    getRun: vi.fn(),
  },
}))

const mockApi = vi.mocked(api)
const mockRunsApi = vi.mocked(runsApi)

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

  it('shows a generated conversation title in the active chat header', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(
      () => expect(wrapper.text()).toContain('Deploy pipeline'),
      { timeout: wait },
    )
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: { conversation_id: 'c1', sequence: 0, events: [], runs: [] },
    })
    await findByText(wrapper, 'Deploy pipeline')!.trigger('click')
    await vi.waitFor(
      () => expect(wrapper.find('h1').text()).toBe('Deploy pipeline'),
      { timeout: wait },
    )

    const chat = useChatStore()
    chat.processDurableEvent({
      id: 'evt-title-1',
      conversation_id: 'c1',
      run_id: 'run-title-1',
      sequence: 1,
      type: 'title_updated',
      payload: { conversation_id: 'c1', title: 'Deploy with the staging profile' },
      created_at: '',
    } as never)
    await nextTick()

    expect(wrapper.find('h1').text()).toBe('Deploy with the staging profile')
  })

  it('places compact search operations in chronological conversation history', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    const chat = useChatStore()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deploy pipeline'), { timeout: wait })
    chat.currentConversation = { id: 'c1', title: 'Search and chat', created_at: '', updated_at: '' }
    chat.messages = [
      { id: 'user-before', role: 'user', content: 'Find Terraform modules', conversation_id: 'c1', created_at: '2026-10-20T12:00:00Z' },
      { id: 'assistant-after', role: 'assistant', content: 'I found useful modules.', conversation_id: 'c1', created_at: '2026-10-20T12:00:02Z' },
    ]
    const operation: ToolOperationSummary = {
      id: 'search-middle', run_id: 'run-search', conversation_id: 'c1',
      logical_call_id: 'manual-search', capability_id: 'brave_web_search',
      capability_version: '1.0.0', manifest_digest: 'a'.repeat(64),
      status: 'succeeded', error_code: null, error: null, query: 'terraform modules',
      result: { query: 'terraform modules', items: [{ title: 'Modules', url: 'https://docs.example.test/modules', snippet: 'Browse modules.', rank: 1 }] },
      artifacts: [], attempts: [], created_at: '2026-10-20T12:00:01Z',
      updated_at: '2026-10-20T12:00:01Z', finished_at: '2026-10-20T12:00:01Z',
    }
    chat.toolOperationsByConversation.set('c1', new Map([[operation.id, operation]]))
    await nextTick()

    const stream = wrapper.get('[data-testid="stream"]')
    const content = stream.text()
    expect(content.indexOf('Find Terraform modules')).toBeLessThan(content.indexOf('Brave Search'))
    expect(content.indexOf('Brave Search')).toBeLessThan(content.indexOf('I found useful modules.'))
    expect(stream.findAll('[data-testid="tool-operation-card"]')).toHaveLength(1)
    expect(stream.find('a[href="https://docs.example.test/modules"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="search-tool-form"]').exists()).toBe(false)
  })

  it('creates and selects a conversation immediately on click', async () => {
    const created: Conversation = {
      id: 'c3',
      title: 'New Conversation',
      created_at: '2026-01-03T00:00:00Z',
      updated_at: '2026-01-03T00:00:00Z',
    }
    mockApi.post.mockImplementation(
      (async (url: string) => {
        if (url === '/conversations') {
          return { data: created }
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

    expect(mockApi.post).toHaveBeenCalledTimes(1)
    expect(mockApi.post).toHaveBeenCalledWith('/conversations', {
      title: 'New Conversation',
    })

    await vi.waitFor(
      () => {
        expect(mockApi.get).toHaveBeenCalledWith('/conversations/c3/messages')
      },
      { timeout: wait },
    )
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

    const allButtons = wrapper.findAll('button')
    const userBtnIdx = allButtons.findIndex((b) => b.text().includes('testuser'))
    await allButtons[userBtnIdx].trigger('click')
    await nextTick()
    const signOutBtn = allButtons.find((b) => b.text().includes('Sign Out'))
    if (!signOutBtn) {
      // Re-query since the dropdown adds new buttons.
      const freshButtons = wrapper.findAll('button')
      const freshIdx = freshButtons.findIndex((b) => b.text().includes('Sign Out'))
      expect(freshIdx).toBeGreaterThanOrEqual(0)
      await freshButtons[freshIdx].trigger('click')
    } else {
      await signOutBtn.trigger('click')
    }

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

  it('offers cancel only for a run that can still be stopped', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    const chat = useChatStore()
    await vi.waitFor(
      () => {
        expect(wrapper.text()).toContain('Deploy pipeline')
      },
      { timeout: wait },
    )

    chat.currentConversation = {
      id: 'c1',
      title: 'Deploy pipeline',
      created_at: '',
      updated_at: '',
    } as Conversation
    chat.runCards = new Map([
      [
        'c1',
        {
          run_id: 'run-1',
          status: 'running',
          started_at: null,
          ended_at: null,
          usage: null,
          error_code: null,
          error_message: null,
          model: null,
          attempts: [],
        } as RunCardType,
      ],
    ])

    // The control sits beside the run it names, and it is not disabled while
    // a turn streams: stopping a run is what a person does while one is
    // running, and a control that could not be pressed would be a lie.
    await vi.waitFor(
      () => {
        expect(findByText(wrapper, 'Cancel run')).toBeTruthy()
      },
      { timeout: wait },
    )
    const cancel = findByText(wrapper, 'Cancel run')!
    expect((cancel.element as HTMLButtonElement).disabled).toBe(false)

    mockRunsApi.cancelRun.mockResolvedValue({ data: { id: 'run-1' } })
    // The read is what the card becomes: the store waits for it rather than
    // assuming a cancellation took effect.
    mockRunsApi.getRun.mockResolvedValue({
      data: { id: 'run-1', status: 'cancelled' },
    })

    await cancel.trigger('click')

    await vi.waitFor(
      () => {
        expect(mockRunsApi.cancelRun).toHaveBeenCalledWith('run-1')
      },
      { timeout: wait },
    )

    // A run that reached an end has nothing left to stop, so the control
    // disappears with it rather than offering an action that does nothing.
    await vi.waitFor(
      () => {
        expect(findByText(wrapper, 'Cancel run')).toBeFalsy()
      },
      { timeout: wait },
    )
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
