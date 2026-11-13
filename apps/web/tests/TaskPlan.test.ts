import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import TaskPlan from '@/components/chat/TaskPlan.vue'
import type { TaskCardState, TaskSummary } from '@/types'

// The card is a view of durable records. A repair and a partial output stay
// visible exactly as the records say, and a skipped step is never rendered as
// completed work. Nothing private - an operation identity, a call binding or a
// continuation - may reach the page.
function summary(fields: TaskSummary) {
  return { ...fields }
}

const REPAIRED: TaskCardState = {
  runId: 'run-1',
  sequence: 9,
  currentRevision: 2,
  planRevisions: [
    { revision: 1, review_digest: 'a'.repeat(64), steps: [{ id: 'fetch', intent: 'Fetch the observations' }] },
    {
      revision: 2,
      review_digest: 'b'.repeat(64),
      steps: [
        { id: 'fetch', intent: 'Fetch the observations' },
        { id: 'chart', intent: 'Chart them', depends_on: ['fetch'] },
      ],
    },
  ],
  progress: summary({
    schema_version: 1,
    run_id: 'run-1',
    mode: 'copilot',
    revision: 2,
    status: 'verification_pending',
    steps: [
      {
        id: 'fetch',
        status: 'verified',
        attempt_count: 2,
        evidence_refs: ['artifact:11111111-1111-1111-1111-111111111111'],
        outputs: [],
      },
      { id: 'chart', status: 'skipped', attempt_count: 0, evidence_refs: [], outputs: [] },
    ],
    skipped_steps: ['chart'],
    verified_outputs: [
      { id: '11111111-1111-1111-1111-111111111111', name: 'observations.csv', media_type: 'text/csv', status: 'ready' },
    ],
    last_disposition: 'material_revision',
    pending_decision: null,
  }),
  dispositions: [
    { stepId: 'fetch', disposition: 'known_failure' },
    { stepId: 'fetch', disposition: 'none' },
    { stepId: null, disposition: 'material_revision' },
  ],
  pendingDecision: null,
  resolvedDecisions: [],
  decisionError: null,
  readOnly: false,
}

describe('TaskPlan', () => {
  it('repairedAndPartialOutputsRemainVisible', () => {
    const wrapper = mount(TaskPlan, { props: { card: REPAIRED } })

    // Both revisions stay on the page: a repair replaced the plan under
    // review, it did not erase the one the reader may still want to see.
    expect(wrapper.get('[data-testid="task-plan-revisions"]').text()).toContain('1')
    expect(wrapper.findAll('[data-testid="task-plan-revision"]')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="task-plan-step"]')).toHaveLength(2)

    const repaired = wrapper.get('[data-testid="task-plan-step-fetch"]')
    expect(repaired.text()).toContain('Verified')
    expect(repaired.text()).toContain('attempt 2')
    // The repair's history stays beside the verdict.
    expect(wrapper.get('[data-testid="task-plan-disposition"]').text()).toContain('Repaired after a changed plan')
    // The output that exists is linked; the one that does not stays absent
    // rather than implied.
    expect(wrapper.find('[data-testid="task-plan-output"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="task-plan-output"]').text()).toContain('observations.csv')
    wrapper.unmount()
  })

  it('skippedStepIsNotRenderedCompleted', () => {
    const wrapper = mount(TaskPlan, { props: { card: REPAIRED } })

    const skipped = wrapper.get('[data-testid="task-plan-step-chart"]')
    expect(skipped.text()).toContain('Skipped')
    expect(skipped.text().includes('Verified')).toBe(false)
    expect(skipped.text().includes('Completed')).toBe(false)
    expect(wrapper.find('[data-testid="task-plan-step-chart-complete"]').exists()).toBe(false)
    // The run as a whole is not complete either: work remains unresolved.
    expect(wrapper.get('[data-testid="task-plan-status"]').text()).toContain('Verification pending')
    expect(wrapper.find('[data-testid="task-plan-completed-banner"]').exists()).toBe(false)

    // Nothing private travels with the card, so nothing can render.
    const rendered = wrapper.text()
    expect(rendered.includes('continuation')).toBe(false)
    expect(rendered.includes('runtime_binding')).toBe(false)
    expect(rendered.includes('logical_call_id')).toBe(false)
    expect(rendered.includes('operation_id')).toBe(false)
    wrapper.unmount()
  })

  it('rendersEveryPlanRevisionItWasGivenInOrder', () => {
    const wrapper = mount(TaskPlan, { props: { card: REPAIRED } })
    const renderedPlanRevisions = wrapper.findAll('[data-testid="task-plan-revision"]').map((node) => Number(node.text()))
    expect(renderedPlanRevisions).toEqual([1, 2])
    wrapper.unmount()
  })
})
