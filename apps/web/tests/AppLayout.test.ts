import { describe, it, expect, beforeEach } from 'vitest'
import { mountWithRouter } from './helpers/mountWithRouter'
import AppLayout from '@/layouts/AppLayout.vue'
import type { Conversation } from '@/types'

const conversations: Conversation[] = [
  {
    id: 'c1',
    title: 'Deploy pipeline',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'c2',
    title: 'Scale up web',
    created_at: '2026-01-02T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
  },
]

describe('AppLayout', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('renders named navigation, header, and main landmarks', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/')
    expect(wrapper.find('nav').exists()).toBe(true)
    expect(wrapper.find('header').exists()).toBe(true)
    expect(wrapper.find('main').exists()).toBe(true)
  })

  it('marks exactly one route as the active page', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/')
    expect(wrapper.findAll('[aria-current="page"]').length).toBe(1)
  })

  it('opens a project dropdown with the current workspace selected', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      projects: [{ id: 'project-1', name: 'Personal Workspace' }],
      currentProjectId: 'project-1',
    })
    const trigger = wrapper.get('[data-testid="project-selector-trigger"]')

    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(trigger.classes()).toContain('gap-2')
    await trigger.trigger('click')

    expect(trigger.attributes('aria-expanded')).toBe('true')
    const menu = wrapper.get('[data-testid="project-selector-menu"]')
    expect(menu.text()).toContain('Personal Workspace')
    expect(menu.find('[aria-checked="true"]').text()).toContain('Personal Workspace')
  })

  it('emits an owned project selection from the project menu', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      projects: [
        { id: 'project-1', name: 'Production' },
        { id: 'project-2', name: 'Sandbox' },
      ],
      currentProjectId: 'project-1',
    })
    await wrapper.get('[data-testid="project-selector-trigger"]').trigger('click')
    await wrapper.findAll('[role="menuitemradio"]')[1].trigger('click')

    expect(wrapper.emitted('selectProject')).toEqual([['project-2']])
  })

  it('closes the project dropdown on outside click and Escape', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      projects: [{ id: 'project-1', name: 'Personal Workspace' }],
      currentProjectId: 'project-1',
    })
    const trigger = wrapper.get('[data-testid="project-selector-trigger"]')

    await trigger.trigger('click')
    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(trigger.attributes('aria-expanded')).toBe('false')

    await trigger.trigger('click')
    await wrapper.get('[data-testid="project-selector-menu"] [role="menuitemradio"]')
      .trigger('keydown', { key: 'Escape' })
    expect(trigger.attributes('aria-expanded')).toBe('false')
  })

  it('uses compact conversation labels and clearly highlights the selected conversation', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      conversations,
      currentConversationId: 'c2',
    })
    const activeRow = wrapper
      .findAll('[data-slot="sidebar-menu-button"]')
      .find((row) => row.text().includes('Scale up web'))
    const inactiveRow = wrapper
      .findAll('[data-slot="sidebar-menu-button"]')
      .find((row) => row.text().includes('Deploy pipeline'))

    expect(activeRow?.attributes('data-active')).toBe('true')
    expect(activeRow?.classes()).toContain('text-[13px]')
    expect(activeRow?.get('[data-testid="conversation-title"]').classes()).toContain('text-primary')
    expect(activeRow?.get('[data-testid="conversation-title"]').classes()).toContain('font-semibold')
    expect(inactiveRow?.classes()).toContain('text-[13px]')
    expect(inactiveRow?.get('[data-testid="conversation-title"]').classes()).not.toContain('text-primary')
  })

  it('emits createConversation immediately on button click', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/')
    const newButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('New Conversation'))
    expect(newButton, 'New Conversation button').toBeTruthy()
    await newButton!.trigger('click')
    expect(wrapper.emitted('createConversation')).toEqual([['New Conversation']])
  })

  it('emits selectConversation when a conversation row is clicked', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      conversations,
      currentConversationId: 'c2',
    })
    const row = wrapper
      .findAll('button')
      .find((b) => b.text().includes('Deploy pipeline'))
    expect(row, 'conversation row').toBeTruthy()
    await row!.trigger('click')
    expect(wrapper.emitted('selectConversation')).toEqual([['c1']])
  })

  it('shows the empty state when there are no conversations', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/')
    expect(wrapper.text()).toContain('No conversations yet')
  })

  it('shows a compact conversation-files control in the top navigation', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      conversations,
      currentConversationId: 'c1',
      showConversationFiles: true,
      conversationFilesOpen: false,
      conversationFileCount: 3,
    })

    const trigger = wrapper.get('[data-testid="conversation-files-trigger"]')
    expect(trigger.attributes('aria-label')).toContain('Open conversation files')
    expect(trigger.text()).toContain('3')
    await trigger.trigger('click')
    expect(wrapper.emitted('toggleConversationFiles')).toHaveLength(1)
  })

  it('has no fake workspace/session text or enabled no-op controls', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      conversations,
      currentConversationId: 'c1',
    })
    const text = wrapper.text()
    for (const forbidden of [
      'web-platform',
      'Session #',
      'Provision & Build',
      'staging-env',
      'prod-infra',
      'Export',
      'Stop',
    ]) {
      expect(text, `forbidden text "${forbidden}"`).not.toContain(forbidden)
    }
  })

  it('disables conversation mutations while busy', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      conversations,
      currentConversationId: 'c1',
      busy: true,
    })
    const newButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('New Conversation'))
    expect(newButton!.element).toBeInstanceOf(HTMLButtonElement)
    expect((newButton!.element as HTMLButtonElement).disabled).toBe(true)

    const row = wrapper
      .findAll('button')
      .find((b) => b.text().includes('Deploy pipeline'))
    expect((row!.element as HTMLButtonElement).disabled).toBe(true)
  })
})
