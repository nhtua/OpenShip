<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui'
import type { ToolOperationSummary } from '@/types'

const props = defineProps<{ result: NonNullable<ToolOperationSummary['result']> }>()
const checkedAt = computed(() => {
  const date = props.result.checked_at ? new Date(props.result.checked_at) : null
  return date && !Number.isNaN(date.valueOf()) ? date.toLocaleString() : 'Not reported'
})
</script>

<template>
  <section class="grid gap-3 rounded-md border border-border bg-background p-3" data-testid="aws-connection-check" aria-label="AWS connection check result">
    <div class="flex flex-wrap items-center gap-2">
      <strong class="text-sm">AWS role identity check</strong>
      <Badge>{{ result.status === 'ready' ? 'Ready' : 'Not confirmed' }}</Badge>
    </div>
    <dl class="grid gap-2 text-xs sm:grid-cols-2">
      <div><dt class="text-muted-foreground">Returned account</dt><dd>{{ result.account_id || 'Not reported' }}</dd></div>
      <div><dt class="text-muted-foreground">Region</dt><dd>{{ result.region || 'Not reported' }}</dd></div>
      <div class="sm:col-span-2"><dt class="text-muted-foreground">Assumed role</dt><dd class="break-all">{{ result.role_arn || 'Not reported' }}</dd></div>
      <div><dt class="text-muted-foreground">Bucket verification</dt><dd>{{ result.bucket_verified ? 'Passed' : 'Not requested' }}</dd></div>
      <div><dt class="text-muted-foreground">Checked</dt><dd>{{ checkedAt }}</dd></div>
    </dl>
  </section>
</template>
