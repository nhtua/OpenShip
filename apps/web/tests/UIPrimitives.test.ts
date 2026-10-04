import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { mount } from '@vue/test-utils'
import { describe, it, expect, vi } from 'vitest'
import Button from '@/components/ui/button.vue'
import Input from '@/components/ui/input.vue'
import Textarea from '@/components/ui/textarea.vue'
import Icon from '@/components/common/Icon.vue'
import { cn } from '@/lib/utils'
import { PRIME_ICON_NAMES } from '@/components/common/icons'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

describe('UI primitives', () => {
  it('cnMergesConflictingUtilities', () => {
    expect(cn('p-2', 'p-4')).toBe('p-4')
  })

  it('buttonForwardsNativeAttributes', () => {
    const wrapper = mount(Button, {
      props: {
        type: 'submit',
        disabled: true,
        'aria-label': 'Submit form',
      },
    })
    const btn = wrapper.find('button')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('type')).toBe('submit')
    expect(btn.attributes('aria-label')).toBe('Submit form')
    // The native disabled attribute is what suppresses click activation.
    expect(btn.element.disabled).toBe(true)
  })

  it('buttonRendersVariantAndSizeClasses', () => {
    const wrapper = mount(Button, {
      props: { variant: 'destructive', size: 'icon' },
    })
    const cls = wrapper.find('button').classes().join(' ')
    expect(cls).toContain('bg-destructive')
    expect(cls).toContain('h-9')
    expect(cls).toContain('w-9')
  })

  it('inputAndTextareaSupportVModel', async () => {
    const onInput = vi.fn()
    const input = mount(Input, {
      props: {
        modelValue: 'a',
        id: 'field-id',
        name: 'field',
        'onUpdate:modelValue': onInput,
      },
    })
    expect(input.find('input').attributes('id')).toBe('field-id')
    expect(input.find('input').attributes('name')).toBe('field')
    await input.find('input').setValue('b')
    expect(onInput).toHaveBeenCalledWith('b')

    const onArea = vi.fn()
    const area = mount(Textarea, {
      props: {
        modelValue: '',
        id: 'msg-id',
        name: 'message',
        'onUpdate:modelValue': onArea,
      },
    })
    expect(area.find('textarea').attributes('id')).toBe('msg-id')
    expect(area.find('textarea').attributes('name')).toBe('message')
    await area.find('textarea').setValue('hello')
    expect(onArea).toHaveBeenCalledWith('hello')
  })

  it('iconsUseOnlyInstalledGlyphs', () => {
    const pack = readFileSync(
      path.resolve(__dirname, '../node_modules/primeicons/primeicons.css'),
      'utf-8',
    )
    for (const name of PRIME_ICON_NAMES) {
      expect(pack, `pi-${name} glyph`).toContain(`.pi-${name}:before`)
    }
  })

  it('iconRendersGlyphAndIsDecorative', () => {
    const wrapper = mount(Icon, { props: { name: 'send' } })
    const el = wrapper.find('i')
    expect(el.classes()).toContain('pi')
    expect(el.classes()).toContain('pi-send')
    expect(el.attributes('aria-hidden')).toBe('true')
  })
})
