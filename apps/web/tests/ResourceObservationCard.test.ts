import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ResourceObservationCard from '@/components/connectors/ResourceObservationCard.vue'

const observation = {
  id: 'observation-1', resource_id: 'resource-1', binding_id: 'binding-1', binding_revision: 2,
  run_id: 'run-1', operation_id: 'operation-1', source: 'aws_s3_bucket_storage',
  retrieved_at: '2026-10-22T18:00:00Z', measurement_at: null, outcome: 'partial' as const,
  coverage: {
    queried_types: ['StandardStorage', 'GlacierStorage'], reported_types: ['StandardStorage'],
    missing_types: ['GlacierStorage'], partial_types: ['StandardStorage'], query_count: 22,
    stale: true, reported_storage_subtotal_bytes: 0,
  },
  evidence: { id: 'artifact-1', sha256: 'a'.repeat(64), size_bytes: 10, media_type: 'application/json' },
  evidence_status: 'ready' as const,
}

describe('ResourceObservationCard', () => {
  it('shows source, measured and retrieved times, freshness, coverage, subtotal and retained evidence', async () => {
    const wrapper = mount(ResourceObservationCard, { props: { observation } })
    const text = wrapper.text()

    expect(text).toContain('AWS CloudWatch daily storage metrics')
    expect(text).toContain('Stale measurement')
    expect(text).toContain('Not reported')
    expect(text).toContain('0 bytes')
    expect(text).toContain('1 of 2 storage types · 1 missing · 1 partial')
    expect(text).toContain('GlacierStorage')
    expect(text).toContain('Evidence: ready')
    await wrapper.get('[aria-label="Download retained S3 storage evidence"]').trigger('click')
    expect(wrapper.emitted('download')).toEqual([['artifact-1', 's3-bucket-storage.json']])
  })

  it('shows retained bucket metadata without inventing a measurement time', async () => {
    const wrapper = mount(ResourceObservationCard, {
      props: {
        observation: {
          ...observation,
          source: 'aws_s3_bucket_metadata',
          measurement_at: null,
          outcome: 'measured',
          coverage: { versioning_status: 'suspended', mfa_delete_status: 'not_reported' },
        },
      },
    })

    expect(wrapper.text()).toContain('AWS S3 bucket versioning metadata')
    expect(wrapper.text()).toContain('Versioning: suspended')
    expect(wrapper.text()).toContain('MFA Delete: not reported')
    expect(wrapper.text()).toContain('Not reported')
    await wrapper.get('[aria-label="Download retained S3 metadata evidence"]').trigger('click')
    expect(wrapper.emitted('download')).toEqual([['artifact-1', 's3-bucket-metadata.json']])
  })
})
