import { defineStore } from 'pinia'
import { ref } from 'vue'
import { resourcesApi } from '@/services/api'
import type {
  EnvironmentSummary,
  ResourceBindingSummary,
  ResourceObservationSummary,
} from '@/types'

export const useResourcesStore = defineStore('resources', () => {
  const environments = ref<EnvironmentSummary[]>([])
  const bindings = ref<ResourceBindingSummary[]>([])
  const observationsByResource = ref<Map<string, ResourceObservationSummary[]>>(new Map())
  const loading = ref(false)
  const error = ref<string | null>(null)
  let projectGeneration = 0
  const observationGenerations = new Map<string, number>()

  async function loadProject(projectId: string | null): Promise<void> {
    const generation = ++projectGeneration
    if (!projectId) {
      environments.value = []
      bindings.value = []
      observationsByResource.value = new Map()
      loading.value = false
      error.value = null
      return
    }
    loading.value = true
    error.value = null
    try {
      const [environmentResponse, bindingResponse] = await Promise.all([
        resourcesApi.listEnvironments(projectId),
        resourcesApi.listBindings(projectId),
      ])
      if (generation !== projectGeneration) return
      environments.value = environmentResponse.data
      bindings.value = bindingResponse.data.items
    } catch {
      if (generation !== projectGeneration) return
      environments.value = []
      bindings.value = []
      error.value = 'Project resources could not be loaded. Reload and try again.'
    } finally {
      if (generation === projectGeneration) loading.value = false
    }
  }

  async function createEnvironment(
    projectId: string,
    payload: { name: string; display_name: string },
  ): Promise<EnvironmentSummary> {
    const result = await resourcesApi.createEnvironment(projectId, payload)
    await loadProject(projectId)
    return result.data
  }

  async function createBinding(
    projectId: string,
    payload: {
      environment_id: string
      connector_id: string
      connector_revision: number
      bucket_name: string
      display_name: string
    },
  ): Promise<ResourceBindingSummary> {
    const result = await resourcesApi.createBinding(projectId, payload)
    await loadProject(projectId)
    return result.data
  }

  async function loadObservations(projectId: string, resourceId: string): Promise<void> {
    const generation = (observationGenerations.get(resourceId) ?? 0) + 1
    observationGenerations.set(resourceId, generation)
    try {
      const response = await resourcesApi.listObservations(projectId, resourceId)
      if (observationGenerations.get(resourceId) !== generation) return
      observationsByResource.value = new Map(observationsByResource.value).set(
        resourceId,
        response.data.items,
      )
    } catch {
      if (observationGenerations.get(resourceId) === generation) {
        observationsByResource.value = new Map(observationsByResource.value).set(resourceId, [])
      }
    }
  }

  function clear(): void {
    projectGeneration += 1
    observationGenerations.clear()
    environments.value = []
    bindings.value = []
    observationsByResource.value = new Map()
    loading.value = false
    error.value = null
  }

  return {
    environments,
    bindings,
    observationsByResource,
    loading,
    error,
    loadProject,
    createEnvironment,
    createBinding,
    loadObservations,
    clear,
  }
})
