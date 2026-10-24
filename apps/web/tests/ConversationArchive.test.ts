import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { mountWithRouter } from './helpers/mountWithRouter'
import ConversationActionsMenu from '@/components/chat/ConversationActionsMenu.vue'
import ArchivedConversationsDialog from '@/components/chat/ArchivedConversationsDialog.vue'
import AppLayout from '@/layouts/AppLayout.vue'
import type { Conversation } from '@/types'

const archivedConversation: Conversation = {
  id: 'conversation-1',
  project_id: 'project-1',
  project_name: 'Production',
  project_archived_at: null,
  title: 'Apply infrastructure changes',
  created_at: '2026-10-20T12:00:00Z',
  updated_at: '2026-10-20T12:30:00Z',
  archived_at: '2026-10-22T12:00:00Z',
}

describe('conversation archive controls', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('offers Archive in the row overflow without selecting the conversation', async () => {
    const wrapper = mount(ConversationActionsMenu, {
      props: { conversationId: archivedConversation.id, title: archivedConversation.title },
      attachTo: document.body,
    })
    const trigger = wrapper.get('button[aria-label="Conversation actions for Apply infrastructure changes"]')

    await trigger.trigger('click')
    await flushPromises()
    const archiveItem = document.body.querySelector('[role="menuitem"]')
    expect(archiveItem?.textContent).toContain('Archive')
    archiveItem?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(wrapper.emitted('archive')).toEqual([['conversation-1']])
    expect(wrapper.emitted('select')).toBeUndefined()
    wrapper.unmount()
  })

  it('returns focus from the row menu after Escape', async () => {
    const wrapper = mount(ConversationActionsMenu, {
      props: { conversationId: archivedConversation.id, title: archivedConversation.title },
      attachTo: document.body,
    })
    const trigger = wrapper.get('button[aria-label="Conversation actions for Apply infrastructure changes"]')
    await trigger.trigger('click')
    await flushPromises()

    document.activeElement && document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Escape', bubbles: true,
    }))
    await flushPromises()

    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('keeps archived recovery reachable when active history is empty', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', { conversations: [] })
    document.body.appendChild(wrapper.element)
    await wrapper.get('button[aria-label="Conversation history actions"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('No conversations yet')
    expect(document.body.textContent).toContain('Archived conversations')
  })

  it('archives a row without opening it or adding a persistent toolbar control', async () => {
    const { wrapper } = await mountWithRouter(AppLayout, '/', {
      conversations: [{ ...archivedConversation, archived_at: null }],
    })
    document.body.appendChild(wrapper.element)
    expect(wrapper.find('button[aria-label="Archive conversation"]').exists()).toBe(false)
    await wrapper.get('button[aria-label="Conversation actions for Apply infrastructure changes"]').trigger('click')
    await flushPromises()
    const archiveItem = wrapper.find('[role="menuitem"]')
    expect(archiveItem.text()).toContain('Archive')
    await archiveItem.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('archiveConversation')).toEqual([['conversation-1']])
    expect(wrapper.emitted('selectConversation')).toBeUndefined()
  })

  it('asks for parent project restoration before conversation restoration', async () => {
    const wrapper = mount(ArchivedConversationsDialog, {
      props: {
        open: true,
        conversations: [{ ...archivedConversation, project_archived_at: '2026-10-22T11:00:00Z' }],
        loading: false,
        restoringId: null,
        error: null,
      },
      attachTo: document.body,
    })
    await flushPromises()

    expect(document.body.textContent).toContain('Production is archived')
    const restoreProject = [...document.body.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('Restore project'))
    restoreProject?.click()
    await flushPromises()

    expect(wrapper.emitted('restoreProject')).toEqual([['project-1']])
    expect(wrapper.emitted('restoreConversation')).toBeUndefined()
    wrapper.unmount()
  })
})
