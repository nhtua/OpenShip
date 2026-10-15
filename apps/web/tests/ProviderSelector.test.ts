import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ProviderSelector from '@/components/ProviderSelector.vue'
import { modelProfileCatalog } from './helpers/chatFixtures'

const profiles = modelProfileCatalog.profiles

// The control is a native button per option, which is what makes the choice
// reachable by keyboard; a test that cannot see one has lost that guarantee.
function findButton(wrapper, text: string) {
  return wrapper.findAll('button').find((button) => button.text().includes(text))
}

describe('ProviderSelector', () => {
  it('names the default when nothing is selected', () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })

    // "No selection" is a claim a reader can check, not a guess.
    expect(wrapper.text()).toContain('Answering with')
    expect(wrapper.text()).toContain('Fixture primary')
    expect(wrapper.text()).toContain('(chosen)')
  })

  it('lists every profile including one that cannot answer, with its reason', () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles,
        defaultProfileId: 'primary-fixture',
        selectedProfileId: null,
        fallbackIds: [],
      },
    })

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

    const backup = profiles.find((p) => p.profile_id === 'secondary-fixture')
    await findButton(wrapper, backup.label)!.trigger('click')

    expect(wrapper.emitted('select')).toBeTruthy()
    // Fallbacks the new profile does not allow are dropped rather than kept
    // as a promise it cannot keep.
    expect(wrapper.emitted('select')?.[0]).toEqual(['secondary-fixture', []])
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

    const backup = profiles.find((p) => p.profile_id === 'secondary-fixture')
    // The destination is shown as something to opt into, and nothing else.
    expect(wrapper.text()).toContain('Falls back to')
    const toggle = findButton(wrapper, `Fall back to ${backup.label}`)
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
    expect(wrapper.text()).toContain('Needs replacement')
  })

  it('states a missing catalog where the choice is made', () => {
    const wrapper = mount(ProviderSelector, {
      props: {
        profiles: [],
        defaultProfileId: null,
        selectedProfileId: null,
        fallbackIds: [],
        catalogError: 'catalog unavailable',
      },
    })

    expect(wrapper.text()).toContain('Catalog unavailable')
    expect(wrapper.text()).toContain('catalog unavailable')
    // Nothing is claimed about a provider that could not be read.
    expect(wrapper.text()).not.toContain('Fixture primary')
  })
})
