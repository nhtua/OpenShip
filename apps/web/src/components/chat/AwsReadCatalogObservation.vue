<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui'
import type { ToolOperationSummary } from '@/types'

const props = defineProps<{
  result: NonNullable<ToolOperationSummary['result']>
}>()

const operations = computed(() => props.result.operations ?? [])
const readOperationCount = computed(() => operations.value.filter((item) => item.supported).length)

function unsupportedReason(reason?: string | null): string {
  switch (reason) {
    case 'operation_requires_write_action': return 'Requires an AWS write action.'
    case 'missing_authorized_action': return 'No authorized read action is defined for this operation.'
    case 'cross_service_authorization': return 'Uses an authorization pattern outside the supported read catalog.'
    case 'unsupported_binary_shape': return 'Uses a binary data shape this tool cannot safely handle.'
    case 'unsupported_schema_expansion': return 'Its schema exceeds the limits of this read tool.'
    case 'application_secret_operation': return 'Reads application secrets, which this tool excludes.'
    case 'credential_vending_operation': return 'Issues credentials, which this tool excludes.'
    default: return 'Excluded from the read-only catalog.'
  }
}
</script>

<template>
  <section class="grid min-w-0 gap-3 border-t border-border px-3 py-3" data-testid="aws-read-catalog">
    <div class="flex flex-wrap items-center gap-2 text-xs">
      <strong class="font-medium">{{ operations.length }} {{ operations.length === 1 ? 'operation' : 'operations' }} listed</strong>
      <Badge variant="outline">{{ readOperationCount }} read operations in this page</Badge>
      <span v-if="result.sdk_version" class="text-muted-foreground">Botocore {{ result.sdk_version }}</span>
    </div>

    <p class="text-xs text-muted-foreground">
      This local catalog shows which AWS SDK reads OpenShip can make. The AWS role's permissions are checked when a read runs.
    </p>

    <ol v-if="operations.length" class="grid min-w-0 gap-2">
      <li
        v-for="item in operations"
        :key="`${item.service}.${item.operation}`"
        class="grid min-w-0 gap-2 rounded-md border border-border bg-background p-2"
      >
        <div class="flex min-w-0 flex-wrap items-center gap-2">
          <code class="min-w-0 break-all text-xs">{{ item.service }}.{{ item.operation }}</code>
          <Badge :variant="item.supported ? 'default' : 'outline'" class="text-[10px]">
            {{ item.supported ? 'Read available' : 'Excluded' }}
          </Badge>
        </div>
        <p v-if="!item.supported" class="text-xs text-muted-foreground">
          {{ unsupportedReason(item.unsupported_reason) }}
        </p>
        <details v-if="item.parameter_schema" class="min-w-0 text-xs">
          <summary class="cursor-pointer text-muted-foreground">Input parameter schema</summary>
          <pre class="mt-2 max-h-72 overflow-auto rounded-md border border-border bg-muted/40 p-2 leading-5"><code>{{ JSON.stringify(item.parameter_schema, null, 2) }}</code></pre>
        </details>
      </li>
    </ol>
    <p v-else class="text-sm text-muted-foreground">No operations matched this catalog query.</p>

    <p v-if="result.next_cursor" class="text-xs text-muted-foreground" data-testid="aws-read-catalog-continuation">
      More operations are available on the next catalog page.
    </p>
  </section>
</template>
