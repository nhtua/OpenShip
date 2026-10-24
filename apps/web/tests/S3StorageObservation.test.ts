import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import S3StorageObservation from '@/components/chat/S3StorageObservation.vue'

describe('S3StorageObservation', () => {
  it('shows independent retrieval, measurement, freshness, coverage, subtotal and retained evidence', async () => {
    const wrapper = mount(S3StorageObservation, {
      props: {
        result: {
          bucket_name: 'example-bucket', account_id: '123456789012', region: 'us-east-1',
          retrieved_at: '2026-10-22T18:00:00Z', measurement_at: null,
          outcome: 'partial', stale: true, reported_storage_subtotal_bytes: 0,
          coverage: {
            queried_types: ['StandardStorage', 'GlacierStorage'],
            reported_types: ['StandardStorage'],
            missing_types: ['GlacierStorage'], partial_types: ['StandardStorage'],
            query_count: 22, truncated: false,
          },
        },
        artifacts: [{
          id: 'artifact-1', sha256: 'a'.repeat(64), size_bytes: 100, media_type: 'application/json',
          conversation_id: 'conversation-1', project_id: 'project-1', name: 's3-storage.json',
          source: 'aws_s3_bucket_storage', status: 'ready', created_at: '', mutability: 'immutable',
        }],
      },
    })

    const text = wrapper.text()
    expect(text).toContain('AWS CloudWatch daily storage metrics')
    expect(text).toContain('Stale measurement')
    expect(text).toContain('Not reported')
    expect(text).toContain('0 bytes')
    expect(text).toContain('1 of 2 types reported · 22 queries')
    expect(text).toContain('GlacierStorage')
    expect(text).toContain('StandardStorage')
    expect(text).toContain('Retained evidence')
    await wrapper.get('button[aria-label="Download s3-storage.json"]').trigger('click')
    expect(wrapper.emitted('download')).toEqual([['artifact-1', 's3-storage.json']])
  })

  it('states when immutable evidence is unavailable and keeps missing measurements unknown', () => {
    const wrapper = mount(S3StorageObservation, {
      props: {
        result: { outcome: 'no_data', stale: false, measurement_at: null, reported_storage_subtotal_bytes: null },
        artifacts: [],
      },
    })

    expect(wrapper.text()).toContain('No measurements returned')
    expect(wrapper.text()).toContain('Unknown')
    expect(wrapper.text()).toContain('Evidence is not currently available')
  })
})
