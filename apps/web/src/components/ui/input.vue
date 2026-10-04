<script setup lang="ts">
import { cn } from '@/lib/utils'
import { computed, type HTMLAttributes } from 'vue'

interface Props {
  class?: HTMLAttributes['class']
  id?: string
  name?: string
  modelValue?: string
  placeholder?: string
  type?: string
  disabled?: boolean
  required?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  type: 'text',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function onInput(event: Event) {
  const target = event.target as HTMLInputElement
  emit('update:modelValue', target.value)
}
</script>

<template>
  <input
    :id="id"
    :name="name"
    :class="
      cn(
        'flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
        props.class,
      )
    "
    :type="type"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    :required="required"
    @input="onInput"
  />
</template>
