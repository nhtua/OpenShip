import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ChatStream from '@/components/chat/ChatStream.vue'
import type { ChatMessage, TaskCardState } from '@/types'

const messages: ChatMessage[] = [
  { id: 'before', role: 'user', content: 'Earlier question', conversation_id: 'c1', run_id: 'other', created_at: '2026-11-01T00:00:00Z' },
  { id: 'request', role: 'user', content: 'Make a weather chart', conversation_id: 'c1', run_id: 'run-1', created_at: '2026-11-01T00:01:00Z' },
]

function task(pending = true): TaskCardState {
  return {
    runId: 'run-1', sequence: 2, currentRevision: 1,
    planRevisions: [{ revision: 1, review_digest: 'a'.repeat(64), steps: [{ id: 'chart', intent: 'Chart the temperatures' }] }],
    progress: { schema_version: 1, run_id: 'run-1', status: pending ? 'waiting' : 'completed', revision: 1 },
    dispositions: [],
    pendingDecision: pending ? {
      request_id: 'decision-1', kind: 'plan_confirmation', plan_revision: 1,
      expected_digest: 'a'.repeat(64), prompt: 'Review the chart plan before running it.',
    } : null,
    resolvedDecisions: pending ? [] : [{ request_id: 'decision-1', kind: 'plan_confirmation', plan_revision: 1, state: 'confirmed' }],
    decisionError: null, readOnly: false,
  }
}

describe('task entries in the conversation', () => {
  it('shows streaming activity as a compact thinking row', () => {
    const wrapper = mount(ChatStream, { props: { messages, isStreaming: true } })
    expect(wrapper.get('[data-testid="typing-indicator"]').text()).toContain('Thinking')
    expect(wrapper.get('[data-testid="typing-indicator"]').element.tagName).toBe('DETAILS')
  })
  it('places the plan before tools from the same run', () => {
    const operation = { id: 'tool-1', run_id: 'run-1', conversation_id: 'c1', capability_id: 'download_file', status: 'succeeded', created_at: '2026-11-01T00:02:00Z', result: null, artifacts: [], attempts: [] } as unknown as import('@/types').ToolOperationSummary
    const wrapper = mount(ChatStream, { props: { messages, operations: [operation], tasks: [task()], isStreaming: false } })
    const stream = wrapper.get('[data-testid="stream"]')
    const nodes = stream.element.querySelectorAll('[data-testid="task-timeline-entry"], [data-testid="tool-operation-card"]')
    expect(nodes[0]?.getAttribute('data-testid')).toBe('task-timeline-entry')
  })
  it('places the durable plan and approval immediately after its run, not in a separate pane', async () => {
    const wrapper = mount(ChatStream, { props: { messages, tasks: [task()], isStreaming: false } })
    const stream = wrapper.get('[data-testid="stream"]')
    const text = stream.text()

    expect(text.indexOf('Make a weather chart')).toBeLessThan(text.indexOf('Chart the temperatures'))
    expect(stream.get('[data-testid="task-timeline-entry"]').text()).toContain('Review the chart plan before running it.')
    expect(wrapper.find('[data-testid="task-plan"]').exists()).toBe(false)
    await stream.get('[data-testid="task-decision-confirm"]').trigger('click')
    expect(wrapper.emitted('respond')).toEqual([['run-1', 'confirm', null]])
  })

  it('keeps the plan as a compact receipt after approval without live controls', () => {
    const wrapper = mount(ChatStream, { props: { messages, tasks: [task(false)], isStreaming: false } })
    const entry = wrapper.get('[data-testid="task-timeline-entry"]')
    expect(entry.text()).toContain('Plan revision 1')
    expect(entry.text()).toContain('Confirmed')
    expect(entry.text()).not.toContain('Chart the temperatures')
    expect(entry.find('button').exists()).toBe(false)
  })

  it('says that an Auto plan asked for no confirmation instead of leaving an actionless plan', () => {
    const auto: TaskCardState = {
      ...task(), runId: 'run-1',
      progress: { schema_version: 1, run_id: 'run-1', status: 'running', revision: 1, mode: 'auto' },
      pendingDecision: null,
      resolvedDecisions: [],
    }
    const wrapper = mount(ChatStream, { props: { messages, tasks: [auto], activeTaskRunId: 'run-1', isStreaming: false } })
    const entry = wrapper.get('[data-testid="task-timeline-entry"]')
    expect(entry.text()).toContain('Auto')
    expect(entry.text()).toContain('No confirmation was asked')
    expect(entry.find('button').exists()).toBe(false)
  })

  it('shows an unplanned clarification in the relevant run and not another conversation', () => {
    const clarification: TaskCardState = {
      ...task(), runId: 'run-1', planRevisions: [], currentRevision: 0,
      pendingDecision: { request_id: 'clarify', kind: 'clarification', plan_revision: 0, prompt: 'Which file should I use?' },
    }
    const wrapper = mount(ChatStream, { props: { messages, tasks: [clarification, { ...task(), runId: 'unrelated' }], isStreaming: false } })
    expect(wrapper.findAll('[data-testid="task-timeline-entry"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="task-timeline-entry"]').text()).toContain('Which file should I use?')
  })

  it('does not offer actions on an older run when a different run owns the status card', () => {
    const wrapper = mount(ChatStream, { props: { messages, tasks: [task()], activeTaskRunId: 'another-run', isStreaming: false } })
    expect(wrapper.get('[data-testid="task-decision-confirm"]').attributes('disabled')).toBeDefined()
  })

  it('shows the active durable question when the run has no retained message to anchor it', () => {
    const wrapper = mount(ChatStream, { props: { messages: [], tasks: [task()], activeTaskRunId: 'run-1', isStreaming: false } })
    expect(wrapper.get('[data-testid="task-timeline-entry"]').text()).toContain('Review the chart plan')
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(false)
  })

  it('keeps submission failure beside the question instead of hiding it behind run details', () => {
    const wrapper = mount(ChatStream, { props: { messages, tasks: [{ ...task(), decisionError: 'The choice could not be sent.' }], isStreaming: false } })
    expect(wrapper.get('[data-testid="task-decision-error"]').text()).toBe('The choice could not be sent.')
  })

  it('follows a newly requested interaction only when the reader is at the bottom', async () => {
    const wrapper = mount(ChatStream, { props: { messages, tasks: [], isStreaming: false } })
    const scroller = wrapper.get('[data-testid="stream"]').element as HTMLElement
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 250 })
    await nextTick()
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 500 })
    await wrapper.setProps({ tasks: [task()] })
    await nextTick()
    expect(scroller.scrollTop).toBe(500)
    wrapper.unmount()
  })
})
