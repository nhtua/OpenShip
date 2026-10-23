import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import ToolAttachmentMenu from '@/components/chat/ToolAttachmentMenu.vue'
import type { CapabilitySummary } from '@/types'

const capabilities: CapabilitySummary[] = [
  {
    id: 'brave_web_search',
    version: '1.0.0',
    manifest_digest: 'a'.repeat(64),
    input_schema: {},
    output_schema: {},
    effect: 'read_only_external',
    enabled: true,
    available: true,
    unavailable_reason: null,
  },
  {
    id: 'tavily_web_search',
    version: '1.0.0',
    manifest_digest: 'b'.repeat(64),
    input_schema: {},
    output_schema: {},
    effect: 'read_only_external',
    enabled: true,
    available: false,
    unavailable_reason: 'missing_credentials',
  },
]

describe('ToolAttachmentMenu', () => {
  it('offers available tools, explains unavailable tools, and only attaches on selection', async () => {
    const wrapper = mount(ToolAttachmentMenu, {
      props: { capabilities, selectedCapabilityId: null, disabled: false },
    })

    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    expect(wrapper.text()).toContain('Brave Search')
    expect(wrapper.text()).toContain('Tavily Search')
    expect(wrapper.text()).toContain('Add a tool to this message')
    expect(wrapper.emitted('attach')).toBeFalsy()

    const tavily = wrapper.findAll('[data-testid="tool-choice"]').find((item) => item.text().includes('Tavily'))!
    expect(tavily.attributes('disabled')).toBeDefined()
    expect(tavily.text()).toContain('Add a search provider connection')

    await wrapper.findAll('[data-testid="tool-choice"]')[0].trigger('click')
    expect(wrapper.emitted('attach')?.[0]).toEqual([
      { id: 'brave_web_search', version: '1.0.0', manifest_digest: 'a'.repeat(64) },
    ])
  })

  it('closes with Escape and returns focus to the add button', async () => {
    const wrapper = mount(ToolAttachmentMenu, {
      props: { capabilities, selectedCapabilityId: null, disabled: false },
      attachTo: document.body,
    })
    const trigger = wrapper.get('button[aria-label="Add tool"]')
    await trigger.trigger('click')
    await wrapper.get('[data-testid="tool-menu"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[data-testid="tool-menu"]').exists()).toBe(false)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('closes when the user clicks outside the tool menu', async () => {
    const wrapper = mount(ToolAttachmentMenu, {
      props: { capabilities, selectedCapabilityId: null, disabled: false },
      attachTo: document.body,
    })
    const outside = document.createElement('button')
    document.body.append(outside)

    try {
      await wrapper.get('[aria-label="Add tool"]').trigger('click')
      expect(wrapper.find('[data-testid="tool-menu"]').exists()).toBe(true)

      outside.click()
      await nextTick()

      expect(wrapper.find('[data-testid="tool-menu"]').exists()).toBe(false)
    } finally {
      outside.remove()
      wrapper.unmount()
    }
  })

  it('moves between enabled tool choices with arrow keys', async () => {
    const wrapper = mount(ToolAttachmentMenu, {
      attachTo: document.body,
      props: {
        capabilities: capabilities.map((item) => ({
          ...item,
          available: true,
          unavailable_reason: null,
        })),
        selectedCapabilityId: null,
        disabled: false,
      },
    })
    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    const choices = wrapper.findAll('[data-testid="tool-choice"]')
    const automaticToggle = wrapper.get('[data-testid="automatic-tool-mode"]')
    expect(document.activeElement).toBe(automaticToggle.element)
    await automaticToggle.trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(choices[0].element)
    await choices[0].trigger('keydown', { key: 'ArrowUp' })
    expect(document.activeElement).toBe(automaticToggle.element)
    wrapper.unmount()
  })

  it('keeps local workspace tools automatic-only while leaving them available to chat', async () => {
    const workspace: CapabilitySummary = {
      id: 'workspace_list_files',
      version: '1.0.0',
      manifest_digest: 'c'.repeat(64),
      input_schema: {},
      output_schema: {},
      effect: 'read_only_local',
      enabled: true,
      available: true,
      unavailable_reason: null,
    }
    const wrapper = mount(ToolAttachmentMenu, {
      props: {
        capabilities: [...capabilities, workspace],
        selectedCapabilityId: null,
        disabled: false,
      },
    })

    await wrapper.get('button[aria-label="Add tool"]').trigger('click')
    expect(wrapper.findAll('[data-testid="tool-choice"]')).toHaveLength(2)
    expect(wrapper.get('[data-testid="automatic-tool-mode"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).not.toContain('workspace_list_files')
    wrapper.unmount()
  })

  it('offers bound S3 storage as an explicit maintained tool but keeps identity checks out of the model menu', async () => {
    const awsStorage: CapabilitySummary = {
      id: 'aws_s3_bucket_storage', version: '1.0.0', manifest_digest: 'd'.repeat(64),
      input_schema: {}, output_schema: {}, effect: 'read_only_external', enabled: true,
      available: true, unavailable_reason: null,
    }
    const identityCheck: CapabilitySummary = { ...awsStorage, id: 'aws_check_connection' }
    const wrapper = mount(ToolAttachmentMenu, {
      props: {
        capabilities: [...capabilities, awsStorage, identityCheck],
        resourceBindings: [{
          resource_id: 'resource-1', uri: 'aws:s3://example-bucket', display_name: 'Example bucket',
          environment_id: 'environment-1', environment_name: 'production', binding_id: 'binding-1',
          binding_revision: 1, connector_id: 'aws-1', connector_revision: 1, account_id: '123456789012',
          region: 'us-east-1', bucket_name: 'example-bucket', bucket_arn: 'arn:aws:s3:::example-bucket',
          expected_owner: '123456789012', management_method: 'unknown', state: 'active', created_at: '',
        }],
        selectedCapabilityId: null,
        disabled: false,
      },
    })

    await wrapper.get('[aria-label="Add tool"]').trigger('click')
    expect(wrapper.text()).toContain('S3 bucket storage')
    expect(wrapper.text()).not.toContain('aws_check_connection')
    const storage = wrapper.findAll('[data-testid="tool-choice"]').find((item) => item.text().includes('S3'))!
    await storage.trigger('click')
    expect(wrapper.emitted('attach')?.[0]).toEqual([{
      id: 'aws_s3_bucket_storage', version: '1.0.0', manifest_digest: 'd'.repeat(64),
    }])
    wrapper.unmount()
  })
})
