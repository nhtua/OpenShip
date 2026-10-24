import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { toolPolicyApi } from '@/services/api'
import type { ToolPolicyResponse } from '@/types'

export const useToolPolicyStore = defineStore('toolPolicy', () => {
  const snapshot = ref<ToolPolicyResponse | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const forbidden = ref(false)
  const error = ref<string | null>(null)
  let requestGeneration = 0

  const revision = computed(() => snapshot.value?.revision ?? 0)
  const tools = computed(() => snapshot.value?.tools ?? [])
  const effectiveIds = computed(() => snapshot.value?.effective_ids ?? [])
  const deploymentCeilingActive = computed(() => snapshot.value?.deployment_ceiling_active ?? false)
  const deploymentCeilingIds = computed(() => snapshot.value?.deployment_ceiling_ids ?? null)
  const unknownDeploymentIds = computed(() => snapshot.value?.unknown_deployment_ids ?? [])
  const cutoverAvailable = computed(() => snapshot.value?.cutover_available ?? false)
  const legacyCutoverAt = computed(() => snapshot.value?.legacy_cutover_at ?? null)

  async function load(): Promise<void> {
    const generation = ++requestGeneration
    loading.value = true
    error.value = null
    try {
      const response = await toolPolicyApi.get()
      if (generation !== requestGeneration) return
      snapshot.value = response.data
      forbidden.value = false
    } catch (failure) {
      if (generation !== requestGeneration) return
      snapshot.value = null
      const status = responseStatus(failure)
      forbidden.value = status === 403
      error.value = forbidden.value
        ? null
        : 'Built-in tool availability could not be loaded. Try again.'
    } finally {
      if (generation === requestGeneration) loading.value = false
    }
  }

  async function setEnabled(capabilityId: string, enabled: boolean): Promise<boolean> {
    const current = snapshot.value
    if (!current || forbidden.value || saving.value) return false
    const generation = ++requestGeneration
    saving.value = true
    error.value = null
    try {
      const response = await toolPolicyApi.set(capabilityId, {
        enabled,
        expected_revision: current.revision,
      })
      if (generation !== requestGeneration) {
        await load()
        return false
      }
      snapshot.value = response.data
      return true
    } catch (failure) {
      if (generation !== requestGeneration) return false
      if (responseStatus(failure) === 409) {
        await load()
        error.value = 'Tool availability changed elsewhere. The latest settings have been loaded.'
      } else {
        if (responseStatus(failure) === 403) {
          forbidden.value = true
          snapshot.value = null
        }
        error.value = 'The tool setting could not be saved. Reload settings and try again.'
      }
      return false
    } finally {
      saving.value = false
    }
  }

  async function cutoverLegacyAllowlist(): Promise<boolean> {
    const current = snapshot.value
    if (!current || !current.cutover_available || forbidden.value || saving.value) return false
    const generation = ++requestGeneration
    saving.value = true
    error.value = null
    try {
      const response = await toolPolicyApi.cutover({ expected_revision: current.revision })
      if (generation !== requestGeneration) {
        await load()
        return false
      }
      snapshot.value = response.data
      return true
    } catch (failure) {
      if (generation !== requestGeneration) return false
      if (responseStatus(failure) === 409) {
        await load()
        error.value = 'Tool availability changed elsewhere. The latest settings have been loaded.'
      } else {
        error.value = 'The deployment allowlist could not be adopted. Reload settings and try again.'
      }
      return false
    } finally {
      saving.value = false
    }
  }

  function clear(): void {
    requestGeneration += 1
    snapshot.value = null
    loading.value = false
    saving.value = false
    forbidden.value = false
    error.value = null
  }

  return {
    snapshot,
    revision,
    tools,
    effectiveIds,
    deploymentCeilingActive,
    deploymentCeilingIds,
    unknownDeploymentIds,
    cutoverAvailable,
    legacyCutoverAt,
    loading,
    saving,
    forbidden,
    error,
    load,
    setEnabled,
    cutoverLegacyAllowlist,
    clear,
  }
})

function responseStatus(error: unknown): number | undefined {
  return (error as { response?: { status?: number } })?.response?.status
}
