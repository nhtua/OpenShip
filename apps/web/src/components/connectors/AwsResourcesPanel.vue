<script setup lang="ts">
import { computed, ref } from 'vue'
import { Button, Label } from '@/components/ui'
import ResourceObservationCard from './ResourceObservationCard.vue'
import { useResourcesStore } from '@/stores/resources'
import { artifactsApi } from '@/services/api'
import type { ConnectorUsage, EnvironmentSummary, ResourceBindingSummary } from '@/types'

const props = defineProps<{
  projectId: string | null
  connectors: ConnectorUsage[]
  environments: EnvironmentSummary[]
  bindings: ResourceBindingSummary[]
  loading: boolean
}>()
const emit = defineEmits<{
  check: [connectorId: string, bindingId: string]
  error: [message: string]
}>()
const resources = useResourcesStore()
const environmentName = ref('production')
const environmentDisplayName = ref('Production')
const selectedEnvironmentId = ref('')
const selectedConnectorId = ref('')
const bucketName = ref('')
const displayName = ref('')
const savingEnvironment = ref(false)
const savingBinding = ref(false)
const formError = ref<string | null>(null)
const observationsOpen = ref<Set<string>>(new Set())
const activeConnectors = computed(() => props.connectors.filter(
  (connector) => connector.kind === 'aws' && connector.status === 'configured',
))

async function addEnvironment(): Promise<void> {
  if (!props.projectId || savingEnvironment.value) return
  savingEnvironment.value = true
  formError.value = null
  try {
    const environment = await resources.createEnvironment(props.projectId, {
      name: environmentName.value.trim().toLowerCase(),
      display_name: environmentDisplayName.value.trim(),
    })
    selectedEnvironmentId.value = environment.id
  } catch {
    formError.value = 'The environment could not be created. Check its name and try again.'
  } finally {
    savingEnvironment.value = false
  }
}

async function addBinding(): Promise<void> {
  if (!props.projectId || savingBinding.value) return
  const environmentId = selectedEnvironmentId.value || props.environments[0]?.id
  const connector = activeConnectors.value.find((item) => item.id === selectedConnectorId.value) ?? activeConnectors.value[0]
  if (!environmentId || !connector || !bucketName.value.trim() || !displayName.value.trim()) return
  savingBinding.value = true
  formError.value = null
  try {
    await resources.createBinding(props.projectId, {
      environment_id: environmentId,
      connector_id: connector.id,
      connector_revision: connector.revision,
      bucket_name: bucketName.value.trim(),
      display_name: displayName.value.trim(),
    })
    bucketName.value = ''
    displayName.value = ''
  } catch {
    formError.value = 'The bucket could not be bound. Check the bucket name, project, environment and Connector revision.'
  } finally {
    savingBinding.value = false
  }
}

async function toggleObservations(binding: ResourceBindingSummary): Promise<void> {
  const next = new Set(observationsOpen.value)
  if (next.has(binding.resource_id)) {
    next.delete(binding.resource_id)
  } else {
    next.add(binding.resource_id)
    if (props.projectId) await resources.loadObservations(props.projectId, binding.resource_id)
  }
  observationsOpen.value = next
}

async function downloadEvidence(artifactId: string, filename: string): Promise<void> {
  try {
    const response = await artifactsApi.download(artifactId)
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    anchor.click()
    URL.revokeObjectURL(url)
  } catch {
    emit('error', 'Retained observation evidence could not be downloaded. Reload and try again.')
  }
}
</script>

<template>
  <section class="grid gap-4 rounded-xl border border-border bg-card p-4" aria-labelledby="aws-resources-heading" data-testid="aws-resources-panel">
    <header class="grid gap-1">
      <h2 id="aws-resources-heading" class="text-lg font-semibold">S3 bucket bindings</h2>
      <p class="max-w-3xl text-sm text-muted-foreground">
        Bind a bucket to an environment before selecting it in a conversation. Creating a binding records the intended target; use Check bucket to verify its AWS identity.
      </p>
    </header>

    <p v-if="formError" role="alert" class="text-sm text-destructive">{{ formError }}</p>
    <p v-if="loading" role="status" class="text-sm text-muted-foreground">Loading project resources…</p>

    <form class="grid gap-3 rounded-lg border border-border p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" @submit.prevent="addEnvironment">
      <div class="grid gap-1.5">
        <Label for="resource-environment-name">Environment name</Label>
        <input id="resource-environment-name" v-model="environmentName" required maxlength="64" pattern="[a-z0-9][a-z0-9_-]{0,62}" class="h-9 rounded-md border border-input bg-background px-3 text-sm" />
      </div>
      <div class="grid gap-1.5">
        <Label for="resource-environment-label">Display name</Label>
        <input id="resource-environment-label" v-model="environmentDisplayName" required maxlength="255" class="h-9 rounded-md border border-input bg-background px-3 text-sm" />
      </div>
      <Button type="submit" size="sm" variant="outline" :disabled="!projectId || savingEnvironment">
        {{ savingEnvironment ? 'Saving…' : 'Add environment' }}
      </Button>
    </form>

    <form v-if="activeConnectors.length && environments.length" class="grid gap-3 rounded-lg border border-border p-3 sm:grid-cols-2" @submit.prevent="addBinding">
      <div class="grid gap-1.5">
        <Label for="binding-environment">Environment</Label>
        <select id="binding-environment" v-model="selectedEnvironmentId" class="h-9 rounded-md border border-input bg-background px-3 text-sm">
          <option value="">{{ environments[0]?.display_name }} (default)</option>
          <option v-for="environment in environments" :key="environment.id" :value="environment.id">{{ environment.display_name }}</option>
        </select>
      </div>
      <div class="grid gap-1.5">
        <Label for="binding-connector">AWS Connector</Label>
        <select id="binding-connector" v-model="selectedConnectorId" class="h-9 rounded-md border border-input bg-background px-3 text-sm">
          <option value="">{{ activeConnectors[0]?.name }}</option>
          <option v-for="connector in activeConnectors" :key="connector.id" :value="connector.id">{{ connector.name }} · revision {{ connector.revision }}</option>
        </select>
      </div>
      <div class="grid gap-1.5">
        <Label for="binding-bucket-name">Bucket name</Label>
        <input id="binding-bucket-name" v-model="bucketName" required minlength="3" maxlength="63" autocomplete="off" class="h-9 rounded-md border border-input bg-background px-3 text-sm" />
      </div>
      <div class="grid gap-1.5">
        <Label for="binding-display-name">Display name</Label>
        <input id="binding-display-name" v-model="displayName" required maxlength="255" placeholder="Terraform state" class="h-9 rounded-md border border-input bg-background px-3 text-sm" />
      </div>
      <div class="sm:col-span-2">
        <Button type="submit" size="sm" :disabled="savingBinding">
          {{ savingBinding ? 'Saving…' : 'Bind S3 bucket' }}
        </Button>
      </div>
    </form>
    <p v-else-if="!activeConnectors.length" class="rounded-md border border-border p-3 text-sm text-muted-foreground">
      Configure a project AWS Connector before binding S3 buckets.
    </p>
    <p v-else-if="!environments.length" class="rounded-md border border-border p-3 text-sm text-muted-foreground">
      Add an environment before binding an S3 bucket.
    </p>

    <p v-if="!bindings.length && !loading" class="text-sm text-muted-foreground">No S3 buckets are bound to this project yet.</p>
    <ul v-else class="grid gap-3">
      <li v-for="binding in bindings" :key="binding.binding_id" class="grid gap-3 rounded-lg border border-border p-3" :data-binding-id="binding.binding_id">
        <div class="flex min-w-0 flex-wrap items-start justify-between gap-3">
          <div class="grid min-w-0 gap-1">
            <strong class="truncate">{{ binding.display_name }}</strong>
            <span class="break-all text-xs text-muted-foreground">{{ binding.bucket_name }} · {{ binding.environment_name }} · {{ binding.account_id }} · {{ binding.region }}</span>
            <span class="text-xs text-muted-foreground">Connector revision {{ binding.connector_revision }} · Binding revision {{ binding.binding_revision }} · {{ binding.management_method === 'unknown' ? 'Management status unknown' : 'Externally managed' }}</span>
          </div>
          <div class="flex shrink-0 flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" :disabled="binding.state !== 'active'" @click="emit('check', binding.connector_id, binding.binding_id)">Check bucket</Button>
            <Button type="button" size="sm" variant="ghost" :aria-expanded="observationsOpen.has(binding.resource_id)" @click="toggleObservations(binding)">
              {{ observationsOpen.has(binding.resource_id) ? 'Hide observations' : 'View observations' }}
            </Button>
          </div>
        </div>
        <div v-if="observationsOpen.has(binding.resource_id)" class="grid gap-2 border-t border-border pt-3" data-testid="resource-observations">
          <p v-if="!resources.observationsByResource.get(binding.resource_id)?.length" class="text-xs text-muted-foreground">No retained observations are available for this bucket.</p>
          <ResourceObservationCard
            v-for="observation in resources.observationsByResource.get(binding.resource_id) ?? []"
            :key="observation.id"
            :observation="observation"
            @download="downloadEvidence"
          />
        </div>
      </li>
    </ul>
  </section>
</template>
