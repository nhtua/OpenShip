import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentModeSelector from '@/components/chat/AgentModeSelector.vue'
import type { AgentMode } from '@/types'

const WORD = { auto: 'Auto', copilot: 'Copilot' } as Record<AgentMode, string>

function mountSelector(props: Record<string, unknown> = {}) {
  return mount(AgentModeSelector, { props })
}

describe('AgentModeSelector', () => {
  it('offers the mode in a compact dropdown', () => {
    const wrapper = mountSelector({ mode: 'copilot' })
    expect(wrapper.find('button[aria-haspopup="menu"]').exists()).toBe(true)
  })
  it('names itself for a screen reader', () => {
    const wrapper = mountSelector({ mode: 'copilot' as AgentMode, revision: 4 })
    const selector = wrapper.find('[data-testid="agent-mode-selector"]')

    expect(wrapper.get('[data-testid="agent-mode-current"]').attributes('aria-label')).toBe('Agent execution mode')
    expect(selector.attributes('aria-live')).toBe('polite')
    wrapper.unmount()
  })

  it('shows the committed account mode and that it only applies to new tasks', () => {
    const wrapper = mountSelector({ mode: 'copilot' as AgentMode, revision: 4 })

    expect(wrapper.get('[data-testid="agent-mode-current"]').text()).toContain('Copilot')
    expect(wrapper.get('[data-testid="agent-mode-scope"]').text()).toBe('Applies to new tasks')
    expect(wrapper.find('[data-testid="agent-mode-pending"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('offers both modes and selects the committed mode', async () => {
    const wrapper = mountSelector({ mode: 'copilot' as AgentMode })
    await wrapper.get('[data-testid="agent-mode-current"]').trigger('click')
    const choices = wrapper.findAll('[role="menuitemradio"]')
    expect(choices.map(option => option.text())).toEqual(['CopilotSelected', 'Auto'])
    expect(choices[0].attributes('aria-checked')).toBe('true')
  })

  it('marks a choice the browser asked for but the server has not confirmed', () => {
    const wrapper = mountSelector({
      mode: 'copilot' as AgentMode,
      pendingMode: 'auto' as AgentMode,
      revision: 4,
    })

    // The saved-mode line still names the account's current mode: an unsaved
    // Auto is never presented as the mode new tasks will use.
    expect(wrapper.get('[data-testid="agent-mode-current"]').text()).toContain('Copilot')
    expect(wrapper.get('[data-testid="agent-mode-pending"]').text()).toBe('Auto · not saved')
    wrapper.unmount()
  })

  it('says a save is in flight before any server acknowledgement', () => {
    const wrapper = mountSelector({
      mode: 'copilot' as AgentMode,
      pendingMode: 'auto' as AgentMode,
      saving: true,
      revision: 4,
    })

    expect(wrapper.get('[data-testid="agent-mode-pending"]').text()).toBe('Saving Auto…')
    expect(wrapper.get('[data-testid="agent-mode-current"]').text()).toContain('Copilot')
    wrapper.unmount()
  })

  it('states an unavailable preference instead of guessing a mode', () => {
    const wrapper = mountSelector({ revision: 0, blocked: true, message: 'The agent mode could not be loaded. Retry before a task send.' })

    expect(wrapper.get('[data-testid="agent-mode-current"]').text()).toContain('Mode unknown')
    expect(wrapper.get('[data-testid="agent-mode-message"]').text()).toBe('The agent mode could not be loaded. Retry before a task send.')
    wrapper.unmount()
  })

  it('refuses a choice while the mode is unresolved', async () => {
    const wrapper = mountSelector({ blocked: true })
    await wrapper.get('[data-testid="agent-mode-current"]').trigger('click')
    expect(wrapper.get('[data-testid="agent-mode-auto-choice"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="agent-mode-auto-choice"]').trigger('click')
    expect(wrapper.emitted('choose')).toBeUndefined()
  })

  it('emits a selected mode for saving', async () => {
    const wrapper = mountSelector({ mode: 'copilot' as AgentMode })
    await wrapper.get('[data-testid="agent-mode-current"]').trigger('click')
    await wrapper.get('[data-testid="agent-mode-auto-choice"]').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([['auto']])
  })

  it('shows the recorded history after a conflict, beside the current mode', () => {
    const wrapper = mountSelector({
      mode: 'auto' as AgentMode,
      revision: 5,
      conflict: true,
      message: 'Another tab saved this preference first. The current account mode has been loaded.',
    })

    expect(wrapper.get('[data-testid="agent-mode-current"]').text()).toContain('Auto')
    expect(wrapper.get('[data-testid="agent-mode-message"]').text()).toContain('Another tab saved this preference first.')
    wrapper.unmount()
  })
})
