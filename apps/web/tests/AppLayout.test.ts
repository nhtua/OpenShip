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

  it('emits createConversation with a trimmed title', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/')
    const newButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('New Conversation'))
    expect(newButton, 'New Conversation button').toBeTruthy()
    await newButton!.trigger('click')

    const input = wrapper.find('input[name="new-conversation-title"]')
    expect(input.exists()).toBe(true)
    await input.setValue('  Scale up web  ')

    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('createConversation')).toEqual([['Scale up web']])
  })

  it('defaults a blank conversation title to New Conversation', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/')
    const newButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('New Conversation'))
    await newButton!.trigger('click')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.emitted('createConversation')).toEqual([['New Conversation']])
  })

  it('cancels the create form without emitting', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/')
    const newButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('New Conversation'))
    await newButton!.trigger('click')

    const cancel = wrapper
      .findAll('button')
      .find((b) => b.text().includes('Cancel'))
    await cancel!.trigger('click')

    expect(wrapper.find('form').exists()).toBe(false)
    expect(wrapper.emitted('createConversation')).toBeUndefined()
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
