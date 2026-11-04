import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import RunResume from '@/components/chat/RunResume.vue'
import RunCard from '@/components/chat/RunCard.vue'
import { runsApi } from '@/services/api'

vi.mock('@/services/api', () => ({ runsApi: { resumeAnalysis: vi.fn() }, artifactsApi: { download: vi.fn() } }))

describe('RunResume', () => {
  it('offers only A/B, blocks duplicates, and binds the run and request identity', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(runsApi.resumeAnalysis).mockImplementation(() => new Promise((done) => { resolve = done }))
    const wrapper = mount(RunResume, { props: { runId: 'old-run', analysis: { output_ids: ['report-id'] }, error: 'Saved error' } })
    expect(wrapper.get('[data-testid="resume-a"]').text()).toBe('A: Resume from last step')
    expect(wrapper.get('[data-testid="resume-b"]').text()).toBe('B: Start from beginning')
    await wrapper.get('[data-testid="resume-a"]').trigger('click')
    await wrapper.get('[data-testid="resume-a"]').trigger('click')
    expect(runsApi.resumeAnalysis).toHaveBeenCalledTimes(1)
    expect(runsApi.resumeAnalysis).toHaveBeenCalledWith('old-run', { mode: 'last_step', client_request_id: expect.any(String) })
    expect(wrapper.get('[data-testid="resume-b"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="retained-result"]').attributes('href')).toContain('report-id')
    expect(wrapper.get('[data-testid="retained-error"]').attributes('href')).toContain('old-run')
    resolve({ data: { id: 'new-run', conversation_id: 'conversation' } })
    await flushPromises()
    expect(wrapper.emitted('resumed')?.[0]?.[0]).toMatchObject({ id: 'new-run' })
    expect(wrapper.get('[data-testid="resume-a"]').attributes('disabled')).toBeDefined()
  })

  it('retries an ambiguous failure with the identical request and keeps errors visible', async () => {
    vi.mocked(runsApi.resumeAnalysis).mockReset().mockRejectedValue(new Error('Connection lost'))
    const wrapper = mount(RunResume, { props: { runId: 'one', analysis: { output_ids: [] } } })
    await wrapper.get('[data-testid="resume-b"]').trigger('click')
    await flushPromises()
    const first = vi.mocked(runsApi.resumeAnalysis).mock.calls[0]
    await wrapper.get('[data-testid="resume-b"]').trigger('click')
    await flushPromises()
    expect(vi.mocked(runsApi.resumeAnalysis).mock.calls[1]).toEqual(first)
    expect(wrapper.get('[role="alert"]').text()).toContain('retry')
    expect(wrapper.get('[data-testid="resume-a"]').attributes('disabled')).toBeDefined()
    await wrapper.setProps({ runId: 'two' })
    expect(wrapper.get('[data-testid="resume-a"]').attributes('disabled')).toBeUndefined()
  })

  it('is present only on a settled analysis card and does not disable unrelated controls', () => {
    const card = { run_id: 'one', kind: 'tool', status: 'failed', attempts: [], model: null, usage: null, analysis: { output_ids: [] } }
    const wrapper = mount(RunCard, { props: { card: card as never, hasActions: true }, slots: { default: '<button data-testid="chat-control">Chat</button>' } })
    expect(wrapper.findComponent(RunResume).exists()).toBe(true)
    expect(wrapper.get('[data-testid="chat-control"]').attributes('disabled')).toBeUndefined()
    const chat = mount(RunCard, { props: { card: { ...card, kind: 'chat', analysis: null } as never } })
    expect(chat.findComponent(RunResume).exists()).toBe(false)
  })

  it('does not attach a late acknowledgment to a different run', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(runsApi.resumeAnalysis).mockReset().mockImplementation(() => new Promise((done) => { resolve = done }))
    const wrapper = mount(RunResume, { props: { runId: 'first', analysis: { output_ids: [] } } })
    await wrapper.get('[data-testid="resume-a"]').trigger('click')
    await wrapper.setProps({ runId: 'second' })
    resolve({ data: { id: 'resumed-first' } })
    await flushPromises()
    expect(wrapper.emitted('resumed')).toBeUndefined()
    expect(wrapper.get('[data-testid="resume-a"]').attributes('disabled')).toBeUndefined()
  })

  it('shows a definite missing-input refusal and lets the user explicitly choose B', async () => {
    vi.mocked(runsApi.resumeAnalysis).mockReset().mockRejectedValue({ response: { status: 409, data: { error: { code: 'analysis_inputs_unavailable' } } } })
    const wrapper = mount(RunResume, { props: { runId: 'first', analysis: { output_ids: ['saved'] } } })
    await wrapper.get('[data-testid="resume-a"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('missing or pruned')
    expect(wrapper.get('[data-testid="resume-b"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-testid="retained-result"]').exists()).toBe(true)
  })

  it('keeps prior result and error links available while the resumed run is active', () => {
    const card = { run_id: 'new', kind: 'tool', status: 'running', attempts: [], model: null, usage: null, analysis: { output_ids: ['old-report'], retained_error_code: 'execution_interrupted' } }
    const wrapper = mount(RunCard, { props: { card: card as never } })
    expect(wrapper.get('[data-testid="retained-result"]').attributes('href')).toContain('old-report')
    expect(wrapper.get('[data-testid="retained-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="resume-a"]').exists()).toBe(false)
  })
})
