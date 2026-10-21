import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ProviderSelector from '@/components/ProviderSelector.vue'
import { modelProfileCatalog } from './helpers/chatFixtures'

const profiles = modelProfileCatalog.profiles

// The control is a native button per option, which is what makes the choice
// reachable by keyboard; a test that cannot see one has lost that guarantee.
function findButton(wrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

describe('ProviderSelector', () => {
  it('keeps the default compact and names gpt-6-luna', () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })

    expect(wrapper.get('[data-testid="model-trigger"]').text()).toContain('gpt-6-luna')
    expect(wrapper.findAll('[data-testid="provider-option"]')).toHaveLength(0)
  })

  it('lists every profile including one that cannot answer, with its reason, when opened', async () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })

    await wrapper.get('[data-testid="model-trigger"]').trigger('click')
    expect(wrapper.findAll('[data-testid="provider-option"]')).toHaveLength(3)
    const offline = profiles.find((p) => p.profile_id === 'offline-fixture')
    // A profile this build cannot serve is still shown, with the reason it
    // needs — so the browser can act instead of guess.
    expect(wrapper.text()).toContain(offline.label)
    expect(wrapper.text()).toContain(offline.explanation)
    // It is not offered as something to click.
    expect(findButton(wrapper, offline.label)).toBeUndefined()
  })

  it('choosing a profile emits that selection', async () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })

    await wrapper.get('[data-testid="model-trigger"]').trigger('click')
    const backup = profiles.find((p) => p.profile_id === 'secondary-fixture')
    await findButton(wrapper, backup.label)!.trigger('click')

    expect(wrapper.emitted('select')).toBeTruthy()
    // Fallbacks the new profile does not allow are dropped rather than kept
    // as a promise it cannot keep.
    expect(wrapper.emitted('select')?.[0]).toEqual(['secondary-fixture', []])
  })

  it('closes the model menu with Escape and restores focus to the trigger', async () => {
    const wrapper = mount(ProviderSelector, {
      attachTo: document.body,
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })
    const trigger = wrapper.get('[data-testid="model-trigger"]')
    await trigger.trigger('click')
    expect(wrapper.find('[role="menu"]').exists()).toBe(true)
    await wrapper.get('[role="menu"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('closes when the user clicks outside the model menu', async () => {
    const wrapper = mount(ProviderSelector, {
      attachTo: document.body,
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })
    const outside = document.createElement('button')
    document.body.append(outside)

    try {
      await wrapper.get('[data-testid="model-trigger"]').trigger('click')
      expect(wrapper.find('[role="menu"]').exists()).toBe(true)

      outside.click()
      await nextTick()

      expect(wrapper.find('[role="menu"]').exists()).toBe(false)
    } finally {
      outside.remove()
      wrapper.unmount()
    }
  })

  it('moves between enabled model choices with arrow keys', async () => {
    const wrapper = mount(ProviderSelector, {
      attachTo: document.body,
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })
    await wrapper.get('[data-testid="model-trigger"]').trigger('click')
    const choices = wrapper.findAll('[role="menuitemradio"]')
    expect(choices.length).toBeGreaterThan(1)
    expect(document.activeElement).toBe(choices[0].element)
    await choices[0].trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(choices[1].element)
    await choices[1].trigger('keydown', { key: 'ArrowUp' })
    expect(document.activeElement).toBe(choices[0].element)
    wrapper.unmount()
  })

  it('holds a change back while a submission is in flight', async () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
        changeDisabled: true,
      },
    })

    await wrapper.get('[data-testid="model-trigger"]').trigger('click')
    const backup = profiles.find((p) => p.profile_id === 'secondary-fixture')
    await findButton(wrapper, backup.label)!.trigger('click')

    // That turn's provider is already fixed, so a change here would describe
    // a different turn.
    expect(wrapper.emitted('select')).toBeFalsy()
  })

  it('offers a fallback only where the chosen profile allows one', async () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: 'primary-fixture',
        fallbackIds: [],
      },
    })

    await wrapper.get('[data-testid="model-trigger"]').trigger('click')
    const backup = profiles.find((p) => p.profile_id === 'secondary-fixture')
    // The configured fallback is shown as an optional choice in the expanded menu.
    expect(wrapper.text()).toContain('Fallback models')
    const toggle = wrapper.find('[data-testid="fallback-option"]')
    expect(toggle).toBeTruthy()

    await toggle!.trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual([
      'primary-fixture',
      ['secondary-fixture'],
    ])
  })

  it('does not offer a destination the chosen profile does not allow', async () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'secondary-fixture',
        selectedProfileId: 'secondary-fixture',
        fallbackIds: [],
      },
    })

    // The backup allows no fallbacks, so there is nothing to opt into.
    await wrapper.get('[data-testid="model-trigger"]').trigger('click')
    expect(wrapper.text()).not.toContain('Falls back to')
  })

  it('says so when a selection is no longer offered', () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: 'retired-fixture',
        fallbackIds: [],
      },
    })

    // An earlier choice is kept visible rather than dropped quietly.
    expect(wrapper.get('[data-testid="model-trigger"]').text()).toContain('Needs replacement')
  })

  it('states a missing catalog where the choice is made', async () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles: [],
        defaultProfileId: null,
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: 'catalog unavailable',
      },
    })

    expect(wrapper.get('[data-testid="model-trigger"]').text()).toContain('Catalog unavailable')
    await wrapper.get('[data-testid="model-trigger"]').trigger('click')
    expect(wrapper.text()).toContain('catalog unavailable')
    expect(wrapper.text()).not.toContain('Fixture primary')
  })
})
