import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RunCard from '@/components/chat/RunCard.vue'
import type {
  ProviderAttemptSummary as Attempt,
  RunCard as RunCardType,
  RunModelSummary as Model,
  UsageSummary as Usage,
} from '@/types'

// A card is only ever what a run read said. Nothing here is filled in for a
// missing field: an older run, or one that reported nothing, says so.
function card(props: Partial<RunCardType>): RunCardType {
  return {
    run_id: '',
    status: 'queued',
    started_at: null,
    ended_at: null,
    usage: null,
    error_code: null,
    error_message: null,
    model: null,
    attempts: [],
    ...props,
  } as RunCardType
}

function render(props: Partial<RunCardType>) {
  return mount(RunCard, { props: { card: card(props) } })
}

describe('RunCard', () => {
  it('renders run status badge', () => {
    const wrapper = render({
      run_id: 'run-1',
      status: 'running',
      started_at: '2026-10-04T00:00:00Z',
    })
    expect(wrapper.text()).toContain('Running')
  })

  it('renders reported usage', () => {
    const usage: Usage = {
      input_tokens: 150,
      output_tokens: 20,
      total_tokens: 170,
      cached_input_tokens: null,
      reasoning_tokens: null,
      source: 'fixture provider',
    }
    const wrapper = render({
      run_id: 'run-2',
      status: 'succeeded',
      started_at: '2026-10-04T00:00:00Z',
      ended_at: '2026-10-04T00:00:05Z',
      usage,
    })
    expect(wrapper.text()).toContain('in 150')
    expect(wrapper.text()).toContain('out 20')
  })

  it('says so when usage was not reported', () => {
    const wrapper = render({
      run_id: 'run-3',
      status: 'succeeded',
      started_at: '2026-10-04T00:00:00Z',
      ended_at: '2026-10-04T00:00:05Z',
      usage: null,
    })
    expect(wrapper.text()).toContain('Not reported')
    // Nothing is estimated on the reader's behalf.
    expect(wrapper.text()).not.toContain('tokens')
  })

  it('says so when a provider reported only part of a count', () => {
    const usage: Usage = {
      input_tokens: null,
      output_tokens: 20,
      total_tokens: null,
      cached_input_tokens: null,
      reasoning_tokens: null,
      source: null,
    }
    const wrapper = render({
      run_id: 'run-4',
      status: 'succeeded',
      usage,
    })
    expect(wrapper.text()).toContain('out 20')
    expect(wrapper.text()).not.toContain('in ')
  })

  it('names the provider a run was pinned to', () => {
    const model: Model = {
      profile_id: 'primary-fixture',
      label: 'Fixture primary',
      adapter: 'openai',
      requested_model: 'primary-model',
      actual_model: 'primary-model-2026',
      destination_label: 'fixture.provider.test',
      fallback_profile_ids: [],
      recorded: true,
    }
    const wrapper = render({
      run_id: 'run-5',
      status: 'succeeded',
      model,
    })
    expect(wrapper.text()).toContain('Fixture primary')
    // The model that actually served is stated, not the one merely asked for.
    expect(wrapper.text()).toContain('primary-model-2026')
    expect(wrapper.text()).toContain('fixture.provider.test')
  })

  it('says so for a run from before profiles existed', () => {
    const wrapper = render({
      run_id: 'run-6',
      status: 'succeeded',
      model: null,
    })
    expect(wrapper.text()).toContain('Provider not recorded')
  })

  it('shows the requests actually made, including a fallback', () => {
    const attempts: Attempt[] = [
      {
        ordinal: 1,
        profile_id: 'primary-fixture',
        adapter: 'openai',
        requested_model: 'primary-model',
        actual_model: null,
        destination_label: 'fixture.provider.test',
        outcome: 'rate_limited',
        error_code: 'rate_limited',
        usage: null,
        started_at: null,
        ended_at: null,
      },
      {
        ordinal: 2,
        profile_id: 'secondary-fixture',
        adapter: 'openai',
        requested_model: 'backup-model',
        actual_model: 'backup-model',
        destination_label: 'backup.provider.test',
        outcome: 'success',
        error_code: null,
        usage: null,
        started_at: null,
        ended_at: null,
      },
    ]
    const wrapper = render({
      run_id: 'run-7',
      status: 'succeeded',
      attempts,
    })
    // A fallback is part of the chain rather than hidden, because it is what
    // a reader would want to know.
    expect(wrapper.text()).toContain('primary-fixture')
    expect(wrapper.text()).toContain('secondary-fixture')
    expect(wrapper.text()).toContain('→')
  })

  it('states a provider failure in settled copy, never a raw body', () => {
    const wrapper = render({
      run_id: 'run-8',
      status: 'failed',
      error_code: 'output_limit_exceeded',
      error_message: 'The answer hit the limit set for this profile.',
    })
    expect(wrapper.text()).toContain(
      'The answer hit the limit set for this profile.',
    )
  })
})
