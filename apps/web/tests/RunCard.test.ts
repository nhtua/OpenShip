import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import RunCard from '@/components/chat/RunCard.vue'
import type { RunCard as RunCardType } from '@/types'

describe('RunCard', () => {
  it('renders run status badge', () => {
    const card: RunCardType = {
      run_id: 'run-1',
      status: 'running',
      started_at: '2026-10-04T00:00:00Z',
      ended_at: null,
      usage: null,
      error_code: null,
    }
    const wrapper = mount(RunCard, {
      props: { card },
    })
    expect(wrapper.text()).toContain('Running')
  })

  it('renders usage when provided', () => {
    const card: RunCardType = {
      run_id: 'run-2',
      status: 'succeeded',
      started_at: '2026-10-04T00:00:00Z',
      ended_at: '2026-10-04T00:00:05Z',
      usage: '150 tokens',
      error_code: null,
    }
    const wrapper = mount(RunCard, {
      props: { card },
    })
    expect(wrapper.text()).toContain('150 tokens')
  })

  it('does not render usage when null', () => {
    const card: RunCardType = {
      run_id: 'run-3',
      status: 'succeeded',
      started_at: '2026-10-04T00:00:00Z',
      ended_at: '2026-10-04T00:00:05Z',
      usage: null,
      error_code: null,
    }
    const wrapper = mount(RunCard, {
      props: { card },
    })
    expect(wrapper.text()).not.toContain('tokens')
  })
})
