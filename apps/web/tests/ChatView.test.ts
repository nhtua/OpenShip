import { describe, it, expect, beforeEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { mountWithRouter } from './helpers/mountWithRouter'
import ChatView from '@/views/ChatView.vue'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useConnectorsStore } from '@/stores/connectors'
import api, { connectorsApi, conversationsApi, projectsApi, resourcesApi, runsApi, workspaceApi } from '@/services/api'
import type { Conversation, RunCard as RunCardType, ToolOperationSummary } from '@/types'
import { useProjectsStore } from '@/stores/projects'
import {
  apiError,
  fixtureConversations,
  fixtureUser,
  healthyChatApiGet,
} from './helpers/chatFixtures'

vi.mock('@/services/api', () => ({
  default: { get: vi.fn(), post: vi.fn() },
  authApi: { login: vi.fn(), register: vi.fn(), logout: vi.fn() },
  projectsApi: {
    list: vi.fn(), create: vi.fn(), rename: vi.fn(), archive: vi.fn(), restore: vi.fn(),
  },
  resourcesApi: {
    listEnvironments: vi.fn(),
    listBindings: vi.fn(),
    createEnvironment: vi.fn(),
    createBinding: vi.fn(),
    listObservations: vi.fn(),
  },
  conversationsApi: { list: vi.fn(), get: vi.fn(), archive: vi.fn(), restore: vi.fn() },
  connectorsApi: {
    listProjectConnectors: vi.fn(),
    getProjectDefault: vi.fn(),
    setProjectDefault: vi.fn(),
    getProjectCapabilities: vi.fn(),
    getSetupContext: vi.fn(),
    listGlobalConnectors: vi.fn(),
  },
  // The view reaches the durable endpoints only through a run's card:
  // cancelling asks, and the read answers with what the cancellation left.
  runsApi: {
    submitTurn: vi.fn(),
    cancelRun: vi.fn(),
    getSnapshot: vi.fn(),
    getRun: vi.fn(),
  },
  artifactsApi: { list: vi.fn(), download: vi.fn(), delete: vi.fn() },
  workspaceApi: {
    get: vi.fn().mockResolvedValue({
      data: {
        workspace: {
          id: 'workspace-c1', conversation_id: 'c1', project_id: 'project-1',
          schema_version: 1, relative_root: 'projects/p/conversations/c1',
          storage_id: 'storage-1', status: 'available', created_at: '',
          quota: { committed_bytes: 0, committed_count: 0, reserved_bytes: 0, reserved_count: 0, max_bytes: 1000, max_count: 10 },
        },
        files: { items: [], next_cursor: null },
      },
    }),
    readFile: vi.fn(),
  },
}))

const mockApi = vi.mocked(api)
const mockProjectsApi = vi.mocked(projectsApi)
const mockConversationsApi = vi.mocked(conversationsApi)
const mockConnectorsApi = vi.mocked(connectorsApi)
const mockRunsApi = vi.mocked(runsApi)
const mockResourcesApi = vi.mocked(resourcesApi)
const mockWorkspaceApi = vi.mocked(workspaceApi)

const wait = 3000

function findByText(
  wrapper: { findAll(selector: string): Array<{ text(): string }> },
  text: string,
) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

function searchOperation(id: string, runId: string, createdAt: string): ToolOperationSummary {
  return {
    id,
    run_id: runId,
    conversation_id: 'c1',
    logical_call_id: id,
    capability_id: 'brave_web_search',
    capability_version: '1.0.0',
    manifest_digest: 'a'.repeat(64),
    status: 'succeeded',
    error_code: null,
    error: null,
    query: id,
    result: { query: id, items: [] },
    artifacts: [],
    attempts: [],
    created_at: createdAt,
    updated_at: createdAt,
    finished_at: createdAt,
  }
}

describe('ChatView', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    localStorage.clear()
    vi.clearAllMocks()
    mockProjectsApi.list.mockResolvedValue({
      data: [{ id: 'project-1', name: 'Production' }],
    } as never)
    mockProjectsApi.create.mockResolvedValue({
      data: { id: 'project-new', name: 'Sandbox' },
    } as never)
    mockConnectorsApi.listProjectConnectors.mockResolvedValue({ data: [] } as never)
    mockConnectorsApi.getProjectDefault.mockImplementation(async (_projectId, service) => ({
      data: { service, connector_id: null, revision: 0 },
    } as never))
    mockConnectorsApi.getProjectCapabilities.mockResolvedValue({ data: [] } as never)
    mockConnectorsApi.getSetupContext.mockResolvedValue({
      data: { can_manage_global: false, credential_store_ready: true, unavailable_reason: null },
    } as never)
    mockApi.get.mockImplementation(
      (async (url: string) => healthyChatApiGet(url)) as never,
    )
    mockConversationsApi.list.mockImplementation(async (state = 'active') => ({
      data: fixtureConversations
        .filter((conversation) => state === 'active' || conversation.archived_at)
        .map((conversation) => ({ ...conversation })),
    } as never))
    mockConversationsApi.get.mockImplementation(async (id: string) => {
      const conversation = fixtureConversations.find((item) => item.id === id)
      if (!conversation) throw apiError('Conversation not found')
      return { data: { ...conversation } } as never
    })
    mockConversationsApi.archive.mockImplementation(async (id: string) => ({
      data: { ...(fixtureConversations.find((item) => item.id === id) ?? fixtureConversations[0]), archived_at: '2026-10-22T00:00:00Z' },
    } as never))
    mockConversationsApi.restore.mockImplementation(async (id: string) => ({
      data: { ...(fixtureConversations.find((item) => item.id === id) ?? fixtureConversations[0]), archived_at: null },
    } as never))
    mockApi.post.mockResolvedValue({ data: null })
    mockRunsApi.getSnapshot.mockReset().mockImplementation(async (id: string) => ({
      data: { conversation_id: id, sequence: -1, events: [], runs: [] },
    }))
    mockWorkspaceApi.get.mockReset().mockResolvedValue({
      data: {
        workspace: {
          id: 'workspace-c1', conversation_id: 'c1', project_id: 'project-1',
          schema_version: 1, relative_root: 'projects/p/conversations/c1',
          storage_id: 'storage-1', status: 'available', created_at: '',
          quota: { committed_bytes: 0, committed_count: 0, reserved_bytes: 0, reserved_count: 0, max_bytes: 1000, max_count: 10 },
        },
        files: { items: [], next_cursor: null },
      },
    } as never)
    mockWorkspaceApi.readFile.mockReset()
    mockResourcesApi.listObservations.mockResolvedValue({ data: { items: [], next_cursor: null } } as never)
  })

  it('renders conversation history with the active selection', async () => {
    const { wrapper, router } = await mountWithRouter(ChatView, '/')

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
    expect(router.currentRoute.value.path).toBe('/conversation/c2')
  })

  it('refreshes resource observation history after a conversation publishes a resource event', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/conversation/c1')
    const chat = useChatStore()
    chat.currentConversation = {
      ...(chat.currentConversation as Conversation),
      id: 'c1',
      project_id: 'project-1',
    }
    chat.processDurableEvent({
      id: 'evt-resource-observed',
      conversation_id: 'c1',
      run_id: 'run-metadata',
      sequence: 9,
      type: 'resource.observed',
      payload: { resource_id: 'resource-1', binding_id: 'binding-1' },
      created_at: '2026-10-23T12:00:00Z',
    } as never)

    await vi.waitFor(() => {
      expect(mockResourcesApi.listObservations).toHaveBeenCalledWith('project-1', 'resource-1')
    })
    wrapper.unmount()
  })

  it('opens a bookmarked conversation and its files without a sidebar click', async () => {
    const { wrapper, router } = await mountWithRouter(ChatView, '/conversation/c2')

    await vi.waitFor(() => {
      expect(wrapper.find('h1').text()).toBe('Scale up web')
      expect(wrapper.text()).toContain('Scale web to 3 replicas')
      expect(mockWorkspaceApi.get).toHaveBeenCalledWith('c2', { cursor: undefined, limit: 50 })
    })
    expect(router.currentRoute.value.path).toBe('/conversation/c2')
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('uses the selected project for new conversations and filters history after switching', async () => {
    mockProjectsApi.list.mockResolvedValue({
      data: [
        { id: 'project-1', name: 'Production' },
        { id: 'project-2', name: 'Staging' },
      ],
    } as never)
    mockConversationsApi.list.mockResolvedValue({
      data: [
        { ...fixtureConversations[0], project_id: 'project-1' },
        { ...fixtureConversations[1], project_id: 'project-2' },
      ],
    } as never)
    mockApi.post.mockResolvedValue({
      data: {
        id: 'c3', project_id: 'project-2', title: 'New Conversation',
        created_at: '', updated_at: '',
      },
    })
    const { wrapper } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Production'), { timeout: wait })

    await wrapper.get('[data-testid="project-selector-trigger"]').trigger('click')
    await wrapper.findAll('[role="menuitemradio"]')[1].trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Scale up web'), { timeout: wait })
    expect(wrapper.text()).not.toContain('Deploy pipeline')

    await findByText(wrapper, 'New Conversation')!.trigger('click')

    await vi.waitFor(() => expect(mockApi.post).toHaveBeenCalledWith('/conversations', {
      title: 'New Conversation',
      project_id: 'project-2',
    }), { timeout: wait })
    await vi.waitFor(() => expect(useChatStore().currentConversation?.id).toBe('c3'), { timeout: wait })
  })

  it('selects the owning project when opened from a conversation deep link', async () => {
    mockProjectsApi.list.mockResolvedValue({
      data: [
        { id: 'project-1', name: 'Production' },
        { id: 'project-2', name: 'Staging' },
      ],
    } as never)
    mockConversationsApi.list.mockResolvedValue({
      data: [
        { ...fixtureConversations[0], project_id: 'project-1' },
        { ...fixtureConversations[1], project_id: 'project-2' },
      ],
    } as never)
    mockConversationsApi.get.mockImplementation(async (id: string) => ({
      data: { ...fixtureConversations.find((item) => item.id === id)!, project_id: id === 'c2' ? 'project-2' : 'project-1' },
    } as never))
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: { conversation_id: 'c2', project_id: 'project-2', sequence: -1, events: [], runs: [] },
    } as never)

    const { wrapper } = await mountWithRouter(ChatView, '/conversation/c2')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Scale web to 3 replicas'), { timeout: wait })

    expect(useProjectsStore().currentProjectId).toBe('project-2')
    expect(mockConnectorsApi.listProjectConnectors).toHaveBeenLastCalledWith('project-2')
  })

  it('keeps archived conversation history readable, disables work and restores without submitting', async () => {
    const archived = { ...fixtureConversations[0], project_id: 'project-1', archived_at: '2026-10-22T12:00:00Z' }
    mockConversationsApi.list.mockResolvedValue({ data: [] } as never)
    mockConversationsApi.get.mockResolvedValue({ data: archived } as never)
    mockConversationsApi.restore.mockResolvedValue({ data: { ...archived, archived_at: null } } as never)
    const { wrapper } = await mountWithRouter(ChatView, '/conversation/c1')
    const chat = useChatStore()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Staging deployed successfully.'), { timeout: wait })

    expect(chat.currentConversationReadOnly).toBe(true)
    expect(wrapper.get('[data-testid="archived-conversation-banner"]').text()).toContain('This conversation is archived')
    expect((wrapper.get('textarea#message').element as HTMLTextAreaElement).disabled).toBe(true)
    const retainedMessages = [...chat.messages]

    await wrapper.get('[data-testid="archived-conversation-banner"] button').trigger('click')
    await vi.waitFor(() => expect(chat.currentConversationReadOnly).toBe(false), { timeout: wait })

    expect((wrapper.get('textarea#message').element as HTMLTextAreaElement).disabled).toBe(false)
    expect(chat.messages).toEqual(retainedMessages)
    expect(mockRunsApi.submitTurn).not.toHaveBeenCalled()
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('does not switch to or load tools from an archived parent project on a deep link', async () => {
    mockProjectsApi.list.mockResolvedValue({
      data: [{ id: 'project-1', name: 'Production', archived_at: null }],
    } as never)
    mockConversationsApi.list.mockResolvedValue({ data: [] } as never)
    mockConversationsApi.get.mockResolvedValue({
      data: {
        ...fixtureConversations[1],
        project_id: 'project-2',
        project_name: 'Staging',
        project_archived_at: '2026-10-22T11:00:00Z',
      },
    } as never)
    mockRunsApi.getSnapshot.mockResolvedValue({
      data: { conversation_id: 'c2', project_id: 'project-2', sequence: -1, events: [], runs: [] },
    } as never)

    const { wrapper } = await mountWithRouter(ChatView, '/conversation/c2')
    const chat = useChatStore()
    const projects = useProjectsStore()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Scale web to 3 replicas'), { timeout: wait })

    expect(chat.currentConversationReadOnly).toBe(true)
    expect(chat.currentConversation?.project_archived_at).toBeTruthy()
    expect(projects.currentProjectId).toBe('project-1')
    expect(mockConnectorsApi.listProjectConnectors).not.toHaveBeenCalledWith('project-2')
    expect((wrapper.get('textarea#message').element as HTMLTextAreaElement).disabled).toBe(true)
  })

  it('switches projects without cancelling a run in the previous project', async () => {
    mockProjectsApi.list.mockResolvedValue({
      data: [
        { id: 'project-1', name: 'Production' },
        { id: 'project-2', name: 'Staging' },
      ],
    } as never)
    mockConversationsApi.list.mockResolvedValue({
      data: [
        { ...fixtureConversations[0], project_id: 'project-1' },
        { ...fixtureConversations[1], project_id: 'project-2' },
      ],
    } as never)
    const { wrapper, router } = await mountWithRouter(ChatView, '/conversation/c1')
    const chat = useChatStore()
    await vi.waitFor(() => expect(chat.currentConversation?.id).toBe('c1'), { timeout: wait })
    chat.isStreaming = true

    await wrapper.get('[data-testid="project-selector-trigger"]').trigger('click')
    await wrapper.findAll('[role="menuitemradio"]')[1].trigger('click')

    expect(useProjectsStore().currentProjectId).toBe('project-2')
    await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('chat'), { timeout: wait })
    expect(mockRunsApi.cancelRun).not.toHaveBeenCalled()
  })

  it('restores conversation content with Back and Forward and clears it at home', async () => {
    const { wrapper, router } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deploy pipeline'))
    await findByText(wrapper, 'Deploy pipeline')!.trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Staging deployed successfully.'))
    await findByText(wrapper, 'Scale up web')!.trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Scaled web to 3 replicas.'))

    router.back()
    await vi.waitFor(() => {
      expect(router.currentRoute.value.path).toBe('/conversation/c1')
      expect(wrapper.text()).toContain('Staging deployed successfully.')
      expect(wrapper.text()).not.toContain('Scaled web to 3 replicas.')
    })
    router.forward()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Scaled web to 3 replicas.'))

    await wrapper.get('a[href="/"]').trigger('click')
    await vi.waitFor(() => {
      expect(router.currentRoute.value.path).toBe('/')
      expect(wrapper.text()).toContain('Start a conversation')
      expect(useChatStore().currentConversation).toBeNull()
      expect(wrapper.text()).not.toContain('Scaled web to 3 replicas.')
    })
  })

  it('keeps home empty until the first conversation is created', async () => {
    mockApi.post.mockResolvedValue({ data: {
      id: 'unexpected', title: 'New Conversation', created_at: '', updated_at: '',
    } })
    mockConversationsApi.list.mockResolvedValue({ data: [] } as never)
    const { wrapper, router } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(() => expect(mockConversationsApi.list).toHaveBeenCalledWith('active'))
    await nextTick()

    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.text()).toContain('Start a conversation')
    expect(mockApi.post).not.toHaveBeenCalled()
  })

  it('shows a conversation link error without retaining the previous conversation content', async () => {
    const { wrapper, router } = await mountWithRouter(ChatView, '/conversation/c1')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Staging deployed successfully.'))
    mockApi.get.mockImplementation((async (url: string) => {
      if (url === '/conversations/missing/messages') throw apiError('Conversation not found')
      return healthyChatApiGet(url)
    }) as never)

    await router.push('/conversation/missing')

    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').text()).toContain('Conversation not found'))
    expect(router.currentRoute.value.path).toBe('/conversation/missing')
    expect(wrapper.text()).not.toContain('Staging deployed successfully.')
    expect(useChatStore().currentConversation).toBeNull()
  })

  it.each(['c1%2F..%2Fc2', 'c1%5C..%5Cc2', '..'])('rejects a malformed conversation link %s', async (id) => {
    const { wrapper } = await mountWithRouter(ChatView, `/conversation/${id}`)

    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').text()).toContain('Invalid conversation link'))
    expect(mockRunsApi.getSnapshot).not.toHaveBeenCalled()
    expect(useChatStore().currentConversation).toBeNull()
  })

  it('does not restore a delayed conversation after navigating home', async () => {
    const { wrapper, router } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deploy pipeline'))
    let release: ((response: { data: unknown }) => void) | undefined
    mockApi.get.mockImplementation((async (url: string) => {
      if (url === '/conversations/c1/messages') {
        return new Promise((resolve) => { release = resolve })
      }
      return healthyChatApiGet(url)
    }) as never)
    await router.push('/conversation/c1')
    await vi.waitFor(() => expect(release).toBeTypeOf('function'))

    await router.push('/')
    release?.({ data: [{ id: 'late', role: 'assistant', content: 'Old content', conversation_id: 'c1', created_at: '' }] })
    await nextTick()
    await nextTick()

    expect(useChatStore().currentConversation).toBeNull()
    expect(wrapper.text()).not.toContain('Old content')
    expect(wrapper.text()).toContain('Start a conversation')
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

  it('orders search operations by the user and assistant messages for their run', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    const chat = useChatStore()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deploy pipeline'), { timeout: wait })
    chat.currentConversation = { id: 'c1', title: 'Search timeline', created_at: '', updated_at: '' }
    chat.messages = [
      { id: 'user-run-1', role: 'user', run_id: 'run-1', content: 'First search', conversation_id: 'c1', created_at: '2026-10-20T12:00:00Z' },
      { id: 'assistant-run-1', role: 'assistant', run_id: 'run-1', content: 'First answer', conversation_id: 'c1', created_at: '2026-10-20T12:00:10Z' },
      { id: 'user-run-2', role: 'user', run_id: 'run-2', content: 'Second search', conversation_id: 'c1', created_at: '2026-10-20T12:01:00Z' },
      { id: 'assistant-run-2', role: 'assistant', run_id: 'run-2', content: 'Second answer', conversation_id: 'c1', created_at: '2026-10-20T12:01:10Z' },
    ]
    const first = searchOperation('search-run-1', 'run-1', '2026-10-20T11:00:00Z')
    const second = searchOperation('search-run-2', 'run-2', '2026-10-20T11:00:01Z')
    chat.toolOperationsByConversation.set('c1', new Map([[first.id, first], [second.id, second]]))
    await nextTick()

    const content = wrapper.get('[data-testid="stream"]').text()
    expect(content.indexOf('First search')).toBeLessThan(content.indexOf('Brave Search'))
    expect(content.indexOf('Brave Search')).toBeLessThan(content.indexOf('First answer'))
    expect(content.indexOf('First answer')).toBeLessThan(content.indexOf('Second search'))
    expect(content.indexOf('Second search')).toBeLessThan(content.lastIndexOf('Brave Search'))
    expect(content.lastIndexOf('Brave Search')).toBeLessThan(content.indexOf('Second answer'))
  })

  it('opens conversation files from the header in a right-side panel', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deploy pipeline'), { timeout: wait })
    await findByText(wrapper, 'Deploy pipeline')!.trigger('click')
    await vi.waitFor(() => expect(wrapper.find('[data-testid="conversation-files-trigger"]').exists()).toBe(true), { timeout: wait })
    await wrapper.get('[data-testid="conversation-files-trigger"]').trigger('click')

    const panel = document.body.querySelector('[data-testid="conversation-files-panel"]')
    expect(panel).toBeTruthy()
    expect(panel?.textContent).toContain('No saved files')
    expect(wrapper.find('[data-testid="conversation-files-toggle"]').exists()).toBe(false)
  })

  it('loads and reads retained conversation files by exact snapshot ID', async () => {
    const savedFile = {
      id: 'snapshot-1', sha256: 'a'.repeat(64), size_bytes: 18,
      media_type: 'text/plain', conversation_id: 'c1', project_id: 'project-1',
      name: 'search-results.json', source: 'brave_web_search', status: 'ready',
      created_at: '2026-10-20T12:00:00Z', mutability: 'immutable',
    }
    mockWorkspaceApi.get.mockResolvedValue({
      data: {
        workspace: {
          id: 'workspace-c1', conversation_id: 'c1', project_id: 'project-1',
          schema_version: 1, relative_root: 'projects/p/conversations/c1',
          storage_id: 'storage-1', status: 'available', created_at: '',
          quota: { committed_bytes: 18, committed_count: 1, reserved_bytes: 0, reserved_count: 0, max_bytes: 1000, max_count: 10 },
        },
        files: { items: [savedFile], next_cursor: null },
      },
    } as never)
    mockWorkspaceApi.readFile.mockResolvedValue({
      data: {
        workspace_id: 'workspace-c1', artifact_id: 'snapshot-1', version_id: 'snapshot-1',
        sha256: 'a'.repeat(64), media_type: 'text/plain', size_bytes: 18,
        offset: 0, content: '<script>kept as text</script>', next_offset: null,
      },
    } as never)
    const { wrapper } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deploy pipeline'), { timeout: wait })
    await findByText(wrapper, 'Deploy pipeline')!.trigger('click')
    await vi.waitFor(() => expect(mockWorkspaceApi.get).toHaveBeenCalledWith('c1', { limit: 50, cursor: undefined }), { timeout: wait })

    await wrapper.get('[data-testid="conversation-files-trigger"]').trigger('click')
    const readFile = document.body.querySelector<HTMLButtonElement>(
      '[aria-label="Preview search-results.json"]',
    )
    expect(readFile).toBeTruthy()
    readFile!.click()
    await nextTick()
    await vi.waitFor(() => expect(document.body.textContent).toContain('<script>kept as text</script>'), { timeout: wait })
    expect(document.body.querySelector('[data-testid="selected-file-content"] script')).toBeNull()
    expect(mockWorkspaceApi.readFile).toHaveBeenCalledWith('c1', 'snapshot-1', { max_bytes: 16 * 1024 })
    expect(wrapper.text()).not.toContain('/home/')
  })

  it('refreshes conversation files when a completed search publishes an artifact', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/conversation/c1')
    await vi.waitFor(() => expect(mockWorkspaceApi.get).toHaveBeenCalledTimes(1), { timeout: wait })
    await wrapper.get('[data-testid="conversation-files-trigger"]').trigger('click')

    const savedFile: ToolOperationSummary['artifacts'][number] = {
      id: 'search-artifact-1',
      sha256: 'a'.repeat(64),
      size_bytes: 128,
      media_type: 'application/json',
      conversation_id: 'c1',
      project_id: 'project-1',
      name: 'search-results.json',
      source: 'brave_web_search',
      status: 'ready',
      created_at: '2026-10-20T12:00:00Z',
      mutability: 'immutable',
    }
    mockWorkspaceApi.get.mockResolvedValueOnce({
      data: {
        workspace: {
          id: 'workspace-c1', conversation_id: 'c1', project_id: 'project-1',
          schema_version: 1, relative_root: 'projects/p/conversations/c1',
          storage_id: 'storage-1', status: 'available', created_at: '',
          quota: { committed_bytes: 128, committed_count: 1, reserved_bytes: 0, reserved_count: 0, max_bytes: 1000, max_count: 10 },
        },
        files: { items: [savedFile], next_cursor: null },
      },
    } as never)
    const operation = {
      ...searchOperation('search-with-artifact', 'run-search', '2026-10-20T12:00:00Z'),
      artifacts: [savedFile],
    }
    useChatStore().toolOperationsByConversation.set('c1', new Map([[operation.id, operation]]))

    await vi.waitFor(() => expect(mockWorkspaceApi.get).toHaveBeenCalledTimes(2), { timeout: wait })
    await vi.waitFor(() => {
      expect(document.body.querySelector('[data-artifact-id="search-artifact-1"]')).toBeTruthy()
    }, { timeout: wait })
  })

  it('shows the persisted operation failure even when the assistant claims the search succeeded', async () => {
    const { wrapper } = await mountWithRouter(ChatView, '/')
    const chat = useChatStore()
    await vi.waitFor(() => expect(wrapper.text()).toContain('Deploy pipeline'), { timeout: wait })
    chat.currentConversation = { id: 'c1', title: 'Search status', created_at: '', updated_at: '' }
    chat.messages = [
      { id: 'user-query', role: 'user', content: 'Search for current guidance', conversation_id: 'c1', created_at: '2026-10-20T12:00:00Z' },
      { id: 'assistant-claim', role: 'assistant', content: 'The search completed successfully.', conversation_id: 'c1', created_at: '2026-10-20T12:00:02Z' },
    ]
    const failedOperation: ToolOperationSummary = {
      id: 'failed-search', run_id: 'run-auto-search', conversation_id: 'c1',
      logical_call_id: 'model:0:call:0', capability_id: 'brave_web_search',
      capability_version: '1.0.0', manifest_digest: 'a'.repeat(64),
      status: 'failed', error_code: 'provider_timeout',
      error: { code: 'provider_timeout', message: 'The search service did not respond within the allowed time.' },
      query: 'current guidance', result: null, artifacts: [], attempts: [],
      created_at: '2026-10-20T12:00:01Z', updated_at: '2026-10-20T12:00:01Z', finished_at: '2026-10-20T12:00:01Z',
    }
    chat.toolOperationsByConversation.set('c1', new Map([[failedOperation.id, failedOperation]]))
    await nextTick()

    const card = wrapper.get('[data-testid="tool-operation-card"]')
    expect(card.text()).toContain('Failed')
    expect(card.text()).toContain('The search service did not respond within the allowed time.')
    expect(wrapper.get('[data-testid="stream"]').text()).toContain('The search completed successfully.')
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

    const { wrapper, router } = await mountWithRouter(ChatView, '/')
    await vi.waitFor(
      () => {
        expect(findByText(wrapper, 'New Conversation')!.attributes('disabled')).toBeUndefined()
      },
      { timeout: wait },
    )

    await findByText(wrapper, 'New Conversation')!.trigger('click')

    expect(mockApi.post).toHaveBeenCalledTimes(1)
    expect(mockApi.post).toHaveBeenCalledWith('/conversations', {
      title: 'New Conversation',
      project_id: 'project-1',
    })

    await vi.waitFor(
      () => {
        expect(mockApi.get).toHaveBeenCalledWith('/conversations/c3/messages')
        expect(router.currentRoute.value.path).toBe('/conversation/c3')
      },
      { timeout: wait },
    )
  })

  it('shows an actionable error with Retry when history loading fails', async () => {
    let failHistory = true
    mockConversationsApi.list.mockImplementation(async () => {
      if (failHistory) {
        failHistory = false
        throw apiError('Server exploded')
      }
      return { data: fixtureConversations } as never
    })
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
    expect(mockConversationsApi.list).toHaveBeenCalledWith('active')
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
    const connectorState = useConnectorsStore()
    auth.token = 'test-token'
    auth.user = fixtureUser
    connectorState.connectors = [{
      id: 'global-brave', kind: 'brave', visibility: 'global', name: 'Shared Brave',
      status: 'configured', revision: 1, is_default: true,
    }]
    connectorState.defaults = {
      brave: { service: 'brave', connector_id: 'global-brave', revision: 1 },
    }

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
    expect(useProjectsStore().projects).toEqual([])
    expect(useProjectsStore().currentProjectId).toBeNull()
    expect(useConnectorsStore().connectors).toEqual([])
    expect(useConnectorsStore().defaults).toEqual({})
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
    chat.conversationMetadataReady = true
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
    await nextTick()
    expect(wrapper.find('[data-testid="run-details"]').exists()).toBe(false)
    await vi.waitFor(
      () => {
        expect(wrapper.find('[aria-label="Cancel run"]').exists()).toBe(true)
      },
      { timeout: wait },
    )
    const cancel = wrapper.get('[aria-label="Cancel run"]')
    expect((cancel.element as HTMLButtonElement).disabled).toBe(false)
    expect(cancel.element.closest('[data-testid="run-card"]')).toBeTruthy()
    chat.pendingRequests.set('pending-retry', {
      conversation_id: 'c1',
      payload: {
        content: 'please retry',
        client_request_id: 'pending-retry',
        provider_profile_id: null,
        fallback_profile_ids: [],
        enable_tools: false,
      },
    })
    await nextTick()
    const retry = wrapper.get('[aria-label="Retry submission"]')
    expect(retry.element.closest('[data-testid="run-card"]')).toBeTruthy()

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
        expect(wrapper.find('[aria-label="Cancel run"]').exists()).toBe(false)
      },
      { timeout: wait },
    )
  })

  it('keeps retry available for an uncertain manual search submission', async () => {
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
    chat.conversationMetadataReady = true
    chat.runCards = new Map([
      ['c1', {
        run_id: 'tool-run-1',
        kind: 'tool',
        graph_version: 1,
        status: 'succeeded',
        started_at: null,
        ended_at: null,
        usage: null,
        error_code: null,
        error_message: null,
        model: null,
        attempts: [],
        operations: [],
        operations_next_cursor: null,
      }],
    ])
    chat.pendingToolRequests.set('retry-search-1', {
      conversation_id: 'c1',
      payload: {
        client_request_id: 'retry-search-1',
        capability: { id: 'brave_web_search', version: '1.0.0', manifest_digest: 'digest' },
        arguments: { query: 'nearest star', max_results: 5 },
      },
    })

    await nextTick()
    const retryAction = vi.spyOn(chat, 'retryToolSubmission').mockResolvedValue()
    const retry = wrapper.get('[aria-label="Retry submission"]')
    expect(chat.currentConversationReadOnly).toBe(false)
    expect(retry.attributes('disabled')).toBeUndefined()
    expect(retry.element.closest('[data-testid="run-card"]')).toBeTruthy()
    await retry.trigger('click')
    expect(retryAction).toHaveBeenCalledWith('retry-search-1')
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
