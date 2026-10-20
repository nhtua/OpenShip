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
  it('keeps a compact search row collapsed, then shows safe results and retained downloads', async () => {
    const wrapper = mount(ToolOperationCard, { props: { operation: operation() } })

    expect(wrapper.text()).toContain('Brave Search')
    expect(wrapper.text()).toContain('terraform modules')
    expect(wrapper.text()).toContain('Completed')
    expect(wrapper.text()).toContain('2 results')
    expect(wrapper.findAll('[data-testid="operation-status"]')).toHaveLength(1)
    expect(wrapper.find('a[href="https://docs.example.test/modules"]').exists()).toBe(false)

    await wrapper.get('button[aria-label="Show results for terraform modules"]').trigger('click')

    expect(wrapper.get('[data-testid="operation-provenance"]').text()).toContain('brave_web_search@1.0.0')
    expect(wrapper.get('[data-testid="operation-provenance"]').text()).toContain('operation-1')
    expect(wrapper.findAll('a[href="https://docs.example.test/modules"]')).toHaveLength(1)
    expect(wrapper.find('a[href="javascript:alert(1)"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Searched Oct')

    await wrapper.get('button[aria-label="Download search-results.json"]').trigger('click')
    expect(wrapper.emitted('download')?.[0]).toEqual(['artifact-1', 'search-results.json'])
    expect(wrapper.get('[data-testid="artifact-reference"]').text()).toContain('artifact-1')
  })

  it('shows safe failure copy, keeps a persisted query when there is no result, and never trusts event payloads', () => {
    const wrapper = mount(ToolOperationCard, {
      props: {
        operation: operation({
          status: 'failed',
          query: 'persisted failed query',
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
    expect(wrapper.text()).toContain('persisted failed query')
    expect(wrapper.text()).toContain('The search service did not respond within the allowed time.')
    expect(wrapper.text()).not.toContain('terraform modules')
  })

  it('states an empty result set and offers cancellation only while work can still stop', async () => {
    const wrapper = mount(ToolOperationCard, {
      props: {
        operation: operation({
          status: 'running',
          result: null,
          artifacts: [],
          attempts: [],
        }),
      },
    })
    expect(wrapper.text()).toContain('Running')
    expect(wrapper.findAll('[data-testid="operation-status"]')).toHaveLength(1)
    await wrapper.get('button[aria-label="Cancel Brave Search"]').trigger('click')
    expect(wrapper.emitted('cancel')?.[0]).toEqual(['run-1'])

    await wrapper.setProps({
      operation: operation({ result: { query: 'nothing found', items: [] } }),
    })
    await wrapper.get('button[aria-label="Show results for nothing found"]').trigger('click')
    expect(wrapper.text()).toContain('No results returned')
    expect(wrapper.find('button[aria-label="Cancel Brave Search"]').exists()).toBe(false)
  })

  it('renders hostile titles and snippets as text', async () => {
    const wrapper = mount(ToolOperationCard, {
      props: {
        operation: operation({
          result: {
            query: 'safe query',
            items: [{
              title: '<img src=x onerror=alert(1)>',
              url: 'https://docs.example.test/safe',
              snippet: '<script>steal()</script>',
              rank: 1,
            }],
          },
        }),
      },
    })
    await wrapper.get('button[aria-label="Show results for safe query"]').trigger('click')
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<script>steal()</script>')
  })

  it('labels a legacy operation with no retained query honestly', async () => {
    const wrapper = mount(ToolOperationCard, {
      props: { operation: operation({ status: 'interrupted', query: undefined, result: null, artifacts: [] }) },
    })
    expect(wrapper.text()).toContain('Search query unavailable')
    await wrapper.get('button[aria-label="Show results for this search"]').trigger('click')
    expect(wrapper.text()).toContain('The search query is not available in this saved history.')
  })
})
