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

  it('explains a pre-dispatch connector wait and exposes resume, setup, resubmit, and cancel actions', async () => {
    const wrapper = mount(RunCard, {
      props: {
        card: card({
          run_id: 'run-waiting',
          status: 'waiting_connector',
          connector_blocker: {
            id: 'blocker-1', request_id: 'request-1', reason: 'credential_unavailable',
            configuration_digest: 'a'.repeat(64), expires_at: '2026-10-23T00:00:00Z',
            connector_kind: 'aws', connector_visibility: 'project', connector_revision: 2,
            connector_id: 'connector-1',
          },
        }),
        canResumeConnector: true,
      },
    })

    expect(wrapper.get('[data-testid="run-summary"]').text()).toContain('Waiting for connector')
    expect(wrapper.get('[data-testid="connector-blocker"]').text()).toContain('No provider or cloud request was sent')
    await wrapper.get('[data-testid="resume-connector"]').trigger('click')
    await wrapper.get('[data-testid="setup-connector"]').trigger('click')
    await wrapper.get('[data-testid="resubmit-connector"]').trigger('click')
    await wrapper.get('[data-testid="dismiss-connector"]').trigger('click')

    expect(wrapper.emitted('resume-connector')).toHaveLength(1)
    expect(wrapper.emitted('setup-connector')).toHaveLength(1)
    expect(wrapper.emitted('resubmit-connector')).toHaveLength(1)
    expect(wrapper.emitted('dismiss-connector')).toHaveLength(1)
  })

  it('does not offer exact resume when current connector settings do not match the blocker', () => {
    const wrapper = mount(RunCard, {
      props: {
        card: card({
          run_id: 'run-changed',
          status: 'waiting_connector',
          connector_blocker: {
            id: 'blocker-1', request_id: 'request-1', reason: 'credential_unavailable',
            configuration_digest: 'a'.repeat(64), expires_at: '2026-10-23T00:00:00Z',
            connector_kind: 'aws', connector_visibility: 'project', connector_revision: 1,
            connector_id: 'connector-1',
          },
        }),
        canResumeConnector: false,
      },
    })

    expect(wrapper.find('[data-testid="resume-connector"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="connector-blocker"]').text()).toContain('Resubmit it as a new turn')
    expect(wrapper.find('[data-testid="resubmit-connector"]').exists()).toBe(true)
  })

  it('shows only duration, context use, and provider-reported total tokens when collapsed', async () => {
    const usage: Usage = {
      input_tokens: 613,
      output_tokens: 105,
      total_tokens: 718,
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
      model: {
        profile_id: 'primary-fixture',
        label: 'Fixture primary',
        adapter: 'openai',
        requested_model: 'primary-model',
        actual_model: 'primary-model',
        destination_label: 'fixture.provider.test',
        serving_profile_id: 'primary-fixture',
        serving_context_tokens: 128000,
        serving_label: 'Fixture primary',
        serving_adapter: 'openai',
        serving_destination_label: 'fixture.provider.test',
        fallback_profile_ids: [],
        recorded: true,
      },
    })

    const summary = wrapper.get('[data-testid="run-summary"]')
    expect(summary.attributes('aria-expanded')).toBe('false')
    expect(summary.text()).toContain('Duration 5s')
    expect(summary.text()).toContain('Context 0.5%')
    expect(summary.text()).toContain('718 tokens')
    expect(wrapper.find('[data-testid="run-details"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Requested:')
  })

  it('omits unavailable statistics', () => {
    const wrapper = render({
      run_id: 'run-3',
      status: 'succeeded',
      usage: null,
    })
    expect(wrapper.get('[data-testid="run-summary"]').text()).toContain('Completed')
    expect(wrapper.text()).not.toContain('Duration')
    expect(wrapper.text()).not.toContain('Context')
    expect(wrapper.text()).not.toContain('tokens')
  })

  it('keeps actionable controls visible while details stay collapsed', () => {
    const wrapper = mount(RunCard, {
      props: {
        card: card({ run_id: 'run-actions', status: 'running' }),
        hasActions: true,
      },
      slots: {
        default: '<button aria-label="Cancel run">Cancel</button>',
      },
    })

    const action = wrapper.get('[aria-label="Cancel run"]')
    expect(wrapper.get('[data-testid="run-card"]').attributes('data-has-actions')).toBe('true')
    expect(wrapper.find('[data-testid="run-details"]').exists()).toBe(false)
    expect(action.element.closest('[data-testid="run-details"]')).toBeNull()
    expect(action.isVisible()).toBe(true)
  })

  it('does not reserve row space for actions when none are actionable', () => {
    const wrapper = mount(RunCard, {
      props: {
        card: card({ run_id: 'run-settled', status: 'succeeded' }),
        hasActions: false,
      },
      slots: {
        default: '<button aria-label="Cancel run">Cancel</button>',
      },
    })

    expect(wrapper.get('[data-testid="run-card"]').attributes('data-has-actions')).toBe('false')
    expect(wrapper.find('[data-testid="run-actions"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="run-summary"]').text()).toContain('Completed')
  })

  it('omits token and context statistics when their provider counts are absent', () => {
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
    expect(wrapper.text()).not.toContain('Context')
    expect(wrapper.text()).not.toContain('tokens')
  })

  it('reveals provider details when the compact status is expanded', async () => {
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
    expect(wrapper.text()).not.toContain('Fixture primary')
    await wrapper.get('[data-testid="run-summary"]').trigger('click')
    expect(wrapper.text()).toContain('Fixture primary')
    // The model that actually served is stated, not the one merely asked for.
    expect(wrapper.text()).toContain('primary-model-2026')
    expect(wrapper.text()).toContain('fixture.provider.test')
  })

  it('separates requested primary details from the successful fallback destination', async () => {
    const model: Model = {
      profile_id: 'primary-fixture',
      label: 'Primary profile',
      adapter: 'openai',
      requested_model: 'primary-model',
      actual_model: 'backup-served-model',
      destination_label: 'primary.provider.test',
      serving_profile_id: 'secondary-fixture',
      serving_label: 'Backup profile',
      serving_adapter: 'anthropic',
      serving_destination_label: 'backup.provider.test',
      fallback_profile_ids: ['secondary-fixture'],
      recorded: true,
    }
    const wrapper = render({
      run_id: 'run-fallback',
      status: 'succeeded',
      model,
    })
    await wrapper.get('[data-testid="run-summary"]').trigger('click')
    const text = wrapper.text()

    expect(text).toContain('Requested: Primary profile · primary-model at primary.provider.test')
    expect(text).toContain('Served by: Backup profile · anthropic · backup-served-model at backup.provider.test')
    expect(text).not.toContain('backup-served-model at primary.provider.test')
  })

  it('says so for a run from before profiles existed after expanding details', async () => {
    const wrapper = render({
      run_id: 'run-6',
      status: 'succeeded',
      model: null,
    })
    await wrapper.get('[data-testid="run-summary"]').trigger('click')
    expect(wrapper.text()).toContain('Provider not recorded')
  })

  it('shows the requests actually made, including a fallback, when expanded', async () => {
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
    await wrapper.get('[data-testid="run-summary"]').trigger('click')
    // A fallback is part of the chain rather than hidden, because it is what
    // a reader would want to know.
    expect(wrapper.text()).toContain('primary-fixture')
    expect(wrapper.text()).toContain('secondary-fixture')
    expect(wrapper.text()).toContain('→')
  })

  it('states a provider failure in settled copy, never a raw body', async () => {
    const wrapper = render({
      run_id: 'run-8',
      status: 'failed',
      error_code: 'output_limit_exceeded',
      error_message: 'The answer hit the limit set for this profile.',
    })
    await wrapper.get('[data-testid="run-summary"]').trigger('click')
    expect(wrapper.text()).toContain(
      'The answer hit the limit set for this profile.',
    )
  })
})
