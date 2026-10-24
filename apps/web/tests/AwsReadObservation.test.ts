import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AwsReadObservation from '@/components/chat/AwsReadObservation.vue'
import type { ArtifactSummary, ToolOperationSummary } from '@/types'

const result: NonNullable<ToolOperationSummary['result']> = {
  operation_id: 'operation-aws-1',
  connector_id: 'connector-aws-1',
  connector_revision: 3,
  account_id: '123456789012',
  service: 's3',
  operation: 'list_buckets',
  region: 'us-east-1',
  retrieved_at: '2026-10-24T12:00:00Z',
  outcome: 'partial',
  preview: { Buckets: [{ Name: '<script>alert(1)</script>' }] },
  preview_truncated: true,
  next_cursor: 'opaque-cursor',
}

const evidence: ArtifactSummary[] = [{
  id: 'artifact-aws-1',
  sha256: 'a'.repeat(64),
  size_bytes: 320,
  media_type: 'application/json',
  conversation_id: 'conversation-1',
  project_id: 'project-1',
  name: 'aws-read.json',
  source: 'aws_read',
  status: 'ready',
  created_at: '2026-10-24T12:00:00Z',
  mutability: 'immutable',
}]

describe('AwsReadObservation', () => {
  it('shows safe AWS provenance, partial and continuation state, and the exact retained evidence', async () => {
    const wrapper = mount(AwsReadObservation, { props: { result, artifacts: evidence } })

    expect(wrapper.text()).toContain('s3.list_buckets')
    expect(wrapper.text()).toContain('123456789012')
    expect(wrapper.text()).toContain('Partial results')
    expect(wrapper.text()).toContain('More results are available to continue reading')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('<script>alert(1)</script>')

    await wrapper.get('button[aria-label="Download aws-read.json"]').trigger('click')
    expect(wrapper.emitted('download')?.[0]).toEqual(['artifact-aws-1', 'aws-read.json'])
  })
})
