import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import PythonOperation from '@/components/chat/PythonOperation.vue'
import ToolOperationCard from '@/components/chat/ToolOperationCard.vue'
import { artifactsApi, runsApi } from '@/services/api'

vi.mock('@/services/api', () => ({
  runsApi: { resumeChatPython: vi.fn() },
  artifactsApi: { download: vi.fn() },
}))

// Object URL pool: the tests must see exactly which URLs the component creates
// and revokes, so preview bytes cannot leak without a revoke.
const createdUrls: string[] = []
const revokedUrls: string[] = []
let urlCounter = 0
const urlPool = URL as unknown as Record<string, (input: unknown) => string>
urlPool.createObjectURL = (_blob) => {
  urlCounter += 1
  const url = `blob:chart-${urlCounter}`
  createdUrls.push(url)
  return url
}
urlPool.revokeObjectURL = (url) => {
  revokedUrls.push(url)
}

function artifact(id: string, name: string, mediaType: string): Record<string, unknown> {
  return {
    id,
    sha256: 'b'.repeat(64),
    size_bytes: 1024,
    media_type: mediaType,
    conversation_id: 'conversation-1',
    project_id: 'project-1',
    name,
    source: 'python_execute',
    status: 'ready',
    created_at: '2026-11-05T00:00:00Z',
    mutability: 'immutable',
  }
}

function ref(id: string, mediaType: string): Record<string, unknown> {
  return { id, sha256: 'b'.repeat(64), size_bytes: 1024, media_type: mediaType }
}

function operation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'operation-1',
    run_id: 'run-1',
    conversation_id: 'conversation-1',
    logical_call_id: 'model:2:call:0',
    capability_id: 'python_execute',
    capability_version: '1.0.0',
    manifest_digest: 'a'.repeat(64),
    status: 'succeeded',
    error_code: null,
    error: null,
    query: null,
    result: {
      source: ref('source-artifact', 'text/plain'),
      outputs: {},
      exit_code: 0,
      stdout: '42\n',
      stderr: '',
    },
    artifacts: [artifact('source-artifact', 'python-step.py', 'text/plain')],
    attempts: [],
    created_at: '2026-11-05T00:00:00Z',
    updated_at: '2026-11-05T00:00:00Z',
    finished_at: '2026-11-05T00:00:00Z',
    ...overrides,
  }
}

function chartedOperation(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return operation({
    result: {
      source: ref('source-artifact', 'text/plain'),
      outputs: {
        chart: ref('chart-artifact', 'image/png'),
        photo: ref('photo-artifact', 'image/jpeg'),
        report: ref('report-artifact', 'text/plain'),
      },
      exit_code: 0,
      stdout: 'chart written\n',
      stderr: '',
    },
    artifacts: [
      artifact('source-artifact', 'python-step.py', 'text/plain'),
      artifact('chart-artifact', 'chart.png', 'image/png'),
      artifact('photo-artifact', 'photo.jpg', 'image/jpeg'),
      artifact('report-artifact', 'report.txt', 'text/plain'),
    ],
    ...overrides,
  })
}

describe('PythonOperation', () => {
  it('shows progress, safe failure copy, and stdout for a fileless run', async () => {
    const running = mount(PythonOperation, { props: { operation: operation({ status: 'running' }) } })
    expect(running.get('[data-testid="python-operation-status"]').text()).toBe('Running')
    expect(running.find('[data-testid="python-stdout"]').exists()).toBe(false)

    const failed = mount(PythonOperation, {
      props: {
        operation: operation({
          status: 'failed',
          error_code: 'sandbox_timeout',
          error: { code: 'sandbox_timeout', message: 'The sandbox command timed out.' },
        }),
      },
    })
    expect(failed.get('[data-testid="python-operation-status"]').text()).toBe('Failed')
    expect(failed.get('[role="alert"]').text()).toContain('timed out')

    const plain = operation({
      result: { source: ref('source-artifact', 'text/plain'), outputs: {}, exit_code: 0, stdout: '42\nhttps://model-mentioned.example/not-linked.png\n', stderr: '' },
    })
    const settled = mount(PythonOperation, { props: { operation: plain } })
    await flushPromises()
    expect(settled.get('[data-testid="python-operation-status"]').text()).toBe('Completed')
    expect(settled.get('[data-testid="python-stdout"]').text()).toContain('42')
    const images = settled.findAll('img')
    expect(images).toHaveLength(0)
    const anchoredExternal = settled.findAll('a').filter((node) => String(node.attributes('href') ?? '').startsWith('http'))
    expect(anchoredExternal).toHaveLength(0)
    // The mentioned URL must stay text, never a resource the browser fetches.
    expect(settled.html().includes('src="http')).toBe(false)
  })

  it('previews only owned PNG and JPEG outputs of the operation', async () => {
    vi.mocked(runsApi.resumeChatPython).mockReset()
    vi.mocked(artifactsApi.download).mockReset().mockReturnValue({ data: new Blob([Uint8Array.from([139, 80, 78, 71])]) })
    const wrapper = mount(PythonOperation, { props: { operation: chartedOperation() } })
    await flushPromises()
    const previews = wrapper.findAll('[data-testid="chart-preview"]')
    expect(previews).toHaveLength(2)
    expect(previews.map((node) => node.attributes('alt'))).toEqual(['chart.png preview', 'photo.jpg preview'])
    for (const node of previews) {
      expect(String(node.attributes('src'))).toMatch(/^blob:chart-/)
    }
    expect(wrapper.find('img[alt="report.txt preview"]').exists()).toBe(false)
    expect(wrapper.find('img[alt="python-step.py preview"]').exists()).toBe(false)
    const downloadButtons = wrapper.findAll('button', (node) => String(node.attributes('aria-label') ?? '').startsWith('Download '))
    expect(downloadButtons.map((node) => node.attributes('aria-label'))).toEqual(['Download chart.png', 'Download photo.jpg', 'Download report.txt'])
  })

  it('revokes replaced and unmounted preview URLs', async () => {
    createdUrls.length = 0
    revokedUrls.length = 0
    vi.mocked(artifactsApi.download).mockReset().mockReturnValue({ data: new Blob([Uint8Array.from([1])]) })
    const first = operation({
      result: { ...{}, outputs: { chart: ref('chart-artifact', 'image/png') }, exit_code: 0, stdout: '', stderr: '', source: ref('source-artifact', 'text/plain') },
      artifacts: [artifact('source-artifact', 'python-step.py', 'text/plain'), artifact('chart-artifact', 'chart.png', 'image/png')],
    })
    const wrapper = mount(PythonOperation, { props: { operation: first } })
    await flushPromises()
    expect(wrapper.findAll('[data-testid="chart-preview"]')).toHaveLength(1)
    const replaced = operation({
      id: 'operation-2',
      run_id: 'run-2',
      result: { outputs: { chart: ref('other-chart', 'image/jpeg') }, exit_code: 0, stdout: '', stderr: '', source: ref('source-artifact', 'text/plain') },
      artifacts: [artifact('source-artifact', 'python-step.py', 'text/plain'), artifact('other-chart', 'other-chart.jpg', 'image/jpeg')],
    })
    await wrapper.setProps({ operation: replaced })
    await flushPromises()
    expect(wrapper.findAll('[data-testid="chart-preview"]')).toHaveLength(1)
    expect(revokedUrls).toEqual([createdUrls[0]])
    expect(createdUrls).toHaveLength(2)
    wrapper.unmount()
    expect(revokedUrls).toEqual([createdUrls[0], createdUrls[1]])
  })

  it('drops a preview response that arrived after the conversation changed', async () => {
    createdUrls.length = 0
    revokedUrls.length = 0
    let resolve!: (value: unknown) => void
    vi.mocked(artifactsApi.download).mockReset().mockImplementation(() => new Promise((done) => { resolve = done }))
    const singleChart = operation({
      result: {
        source: ref('source-artifact', 'text/plain'),
        outputs: { chart: ref('chart-artifact', 'image/png') },
        exit_code: 0,
        stdout: '',
        stderr: '',
      },
      artifacts: [artifact('source-artifact', 'python-step.py', 'text/plain'), artifact('chart-artifact', 'chart.png', 'image/png')],
    })
    const wrapper = mount(PythonOperation, { props: { operation: singleChart } })
    const moved = operation({ id: 'operation-2', conversation_id: 'conversation-2' })
    await wrapper.setProps({ operation: moved })
    resolve({ data: new Blob([Uint8Array.from([2])]) })
    await flushPromises()
    expect(wrapper.findAll('[data-testid="chart-preview"]')).toHaveLength(0)
    expect(createdUrls).toHaveLength(0)
    expect(vi.mocked(artifactsApi.download).mock.calls.map((call) => String(call[0]))).toEqual(['chart-artifact'])
  })

  it('offers the retained chart download without re-executing', async () => {
    vi.mocked(artifactsApi.download).mockReset().mockReturnValue({ data: new Blob([Uint8Array.from([9])]) })
    const wrapper = mount(PythonOperation, { props: { operation: chartedOperation() } })
    await flushPromises()
    expect(artifactsApi.download).toHaveBeenCalledTimes(2)
    await wrapper.get('button[aria-label="Download chart.png"]').trigger('click')
    expect(wrapper.emitted('download')?.[0]).toEqual(['chart-artifact', 'chart.png'])
    expect(artifactsApi.download).toHaveBeenCalledTimes(2)
  })

  it('submits one continuation and disables duplicate resume submissions', async () => {
    createdUrls.length = 0
    revokedUrls.length = 0
    let resolve!: (value: unknown) => void
    vi.mocked(runsApi.resumeChatPython).mockReset().mockImplementation(() => new Promise((done) => { resolve = done; return undefined }))
    vi.mocked(artifactsApi.download).mockReset().mockReturnValue({ data: new Blob([Uint8Array.from([3])]) })
    const wrapper = mount(PythonOperation, { props: { operation: chartedOperation(), canResume: true } })
    await wrapper.get('[data-testid="resume-last-step"]').trigger('click')
    await wrapper.get('[data-testid="resume-last-step"]').trigger('click')
    expect(runsApi.resumeChatPython).toHaveBeenCalledTimes(1)
    expect(runsApi.resumeChatPython).toHaveBeenCalledWith('run-1', { mode: 'last_step', client_request_id: expect.any(String) })
    expect(wrapper.get('[data-testid="resume-beginning"]').attributes('disabled')).toBeDefined()
    resolve({ data: { id: 'new-run', conversation_id: 'conversation-1' } })
    await flushPromises()
    expect(wrapper.emitted('resumed')?.[0]?.[0]).toMatchObject({ id: 'new-run' })
    await wrapper.get('[data-testid="resume-last-step"]').trigger('click')
    expect(runsApi.resumeChatPython).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-testid="resume-last-step"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="resume-beginning"]').attributes('disabled')).toBeDefined()
  })

  it('renders python operations as python cards inside the tool card', async () => {
    const wrapper = mount(ToolOperationCard, { props: { operation: chartedOperation() } })
    expect(wrapper.findComponent(PythonOperation).exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="operation-provenance"]')).toHaveLength(0)
    const search = {
      ...operation(),
      capability_id: 'brave_web_search',
      result: { query: 'terraform', items: [] as [] },
    }
    const searchCard = mount(ToolOperationCard, { props: { operation: search } })
    expect(searchCard.findComponent(PythonOperation).exists()).toBe(false)
  })
})
