<script setup lang="ts">
import { cn } from '@/lib/utils'
import type { HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
  id?: string
  name?: string
  modelValue?: string
  placeholder?: string
  disabled?: boolean
  required?: boolean
  rows?: number
}

const props = withDefaults(defineProps<Props>(), {
  rows: 3,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onInput(event: Event) {
  const target = event.target as HTMLTextAreaElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <textarea
    :id="id"
    :name="name"
    :class="
      cn(
        'flex min-h-[60px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
        props.class,
      )
    "
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    :required="required"
    :rows="rows"
    @input="onInput"
  />
</template>
