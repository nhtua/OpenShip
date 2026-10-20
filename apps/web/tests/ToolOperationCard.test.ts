import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolOperationCard from '@/components/chat/ToolOperationCard.vue'
import type { ToolOperationSummary } from '@/types'

function operation(overrides: Partial<ToolOperationSummary> = {}): ToolOperationSummary {
  return {
    id: 'operation-1',
    run_id: 'run-1',
    conversation_id: 'conversation-1',
    logical_call_id: 'manual-1',
    capability_id: 'brave_web_search',
    capability_version: '1.0.0',
    manifest_digest: 'a'.repeat(64),
    status: 'succeeded',
    error_code: null,
    error: null,
    result: {
      observed_at: '2026-10-20T12:00:00Z',
      query: 'terraform modules',
      items: [
        { title: 'Terraform modules', url: 'https://docs.example.test/modules', snippet: 'Docs', rank: 1 },
        { title: 'Unsafe scheme', url: 'javascript:alert(1)', snippet: 'No', rank: 2 },
      ],
    },
    artifacts: [
      {
        id: 'artifact-1',
        sha256: 'b'.repeat(64),
        size_bytes: 512,
        media_type: 'application/json',
        conversation_id: 'conversation-1',
        project_id: 'project-1',
        name: 'search-results.json',
        source: 'brave_web_search',
        status: 'ready',
        created_at: '2026-10-20T12:00:00Z',
        mutability: 'immutable',
      },
    ],
    attempts: [],
    created_at: '2026-10-20T12:00:00Z',
    updated_at: '2026-10-20T12:00:00Z',
    finished_at: '2026-10-20T12:00:00Z',
    next_cursor: null,
    ...overrides,
  }
}

describe('ToolOperationCard', () => {
  it('shows service identity, observation time, safe result links, and retained download', async () => {
    const wrapper = mount(ToolOperationCard, { props: { operation: operation() } })

    expect(wrapper.text()).toContain('Brave Search')
    expect(wrapper.text()).toContain('terraform modules')
    expect(wrapper.text()).toContain('Completed')
    expect(wrapper.findAll('a[href="https://docs.example.test/modules"]')).toHaveLength(1)
    expect(wrapper.find('a[href="javascript:alert(1)"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Observed Oct')

    await wrapper.get('button[aria-label="Download search-results.json"]').trigger('click')
    expect(wrapper.emitted('download')?.[0]).toEqual(['artifact-1'])
  })

  it('shows safe failure copy and never promotes an event payload into a result', () => {
    const wrapper = mount(ToolOperationCard, {
      props: {
        operation: operation({
          status: 'failed',
          error_code: 'provider_timeout',
          error: {
            code: 'provider_timeout',
            message: 'The search service did not respond within the allowed time.',
          },
          result: null,
          artifacts: [],
        }),
      },
    })

    expect(wrapper.text()).toContain('Failed')
    expect(wrapper.text()).toContain('The search service did not respond within the allowed time.')
    expect(wrapper.text()).not.toContain('terraform modules')
  })
})
