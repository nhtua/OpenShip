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
  it('names itself for a screen reader', () => {
    const wrapper = mountSelector({ mode: 'copilot' as AgentMode, revision: 4 })
    const selector = wrapper.find('[data-testid="agent-mode-selector"]')

    expect(selector.attributes('role')).toBe('radiogroup')
    expect(selector.attributes('aria-label')).toBe('Agent execution mode')
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

  it('labels the other mode as the account default until it is chosen', () => {
    const wrapper = mountSelector({ mode: 'copilot' as AgentMode, revision: 4 })
    const choices = wrapper.findAll('button')

    expect(choices).toHaveLength(2)
    expect(choices.map((button) => button.attributes('aria-label'))).toEqual([
      'Use Copilot for new tasks',
      'Use Auto for new tasks',
    ])
    expect(choices[0].attributes('aria-current')).toBe('true')
    expect(choices[1].attributes('aria-current')).toBe(undefined)
    wrapper.unmount()
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

    expect(wrapper.get('[data-testid="agent-mode-current"]').text()).toBe('Mode unknown')
    expect(wrapper.get('[data-testid="agent-mode-message"]').text()).toBe('The agent mode could not be loaded. Retry before a task send.')
    wrapper.unmount()
  })

  it('refuses a choice while the mode is unresolved, without dropping keyboard access', async () => {
    const wrapper = mountSelector({ blocked: true })
    const choices = wrapper.findAll('button')

    expect(choices.map((button) => button.attributes('aria-disabled'))).toEqual(['true', 'true'])
    await wrapper.get('[data-testid="agent-mode-auto-choice"]').trigger('click')
    expect(wrapper.emitted('choose')).toBe(undefined)

    // A keyboard user can still reach both controls: disabling a control is not
    // removing it.
    expect(wrapper.find('[data-testid="agent-mode-copilot-choice"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('asks for a choice a listener can save, and only for one control', async () => {
    const wrapper = mountSelector({ mode: 'copilot' as AgentMode, revision: 4 })

    await wrapper.get('[data-testid="agent-mode-auto-choice"]').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([['auto']])

    await wrapper.get('[data-testid="agent-mode-copilot-choice"]').trigger('click')
    expect(wrapper.emitted('choose')).toEqual([['auto'], ['copilot']])
    wrapper.unmount()
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
