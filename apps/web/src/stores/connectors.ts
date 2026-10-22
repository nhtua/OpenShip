import { defineStore } from 'pinia'
import { ref } from 'vue'
import { connectorsApi } from '@/services/api'
import type {
  ConnectorKind,
  ConnectorUsage,
  ProjectCapabilitySummary,
  ProjectConnectorDefault,
  ProjectSummary,
} from '@/types'

export const useConnectorsStore = defineStore('connectors', () => {
  const projects = ref<ProjectSummary[]>([])
  const currentProjectId = ref<string | null>(null)
  const connectors = ref<ConnectorUsage[]>([])
  const defaults = ref<Partial<Record<ConnectorKind, ProjectConnectorDefault>>>({})
  const capabilities = ref<ProjectCapabilitySummary[]>([])
  const globalConnectors = ref<ConnectorUsage[]>([])
  const canManageGlobal = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let projectLoadGeneration = 0

  async function initialize(includeGlobalManagement = false): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const response = await connectorsApi.listProjects()
      projects.value = response.data
      if (!projects.value.some((project) => project.id === currentProjectId.value)) {
        currentProjectId.value = projects.value[0]?.id ?? null
      }
      const loads = [loadProjectContext(currentProjectId.value)]
      if (includeGlobalManagement) loads.push(loadGlobalManagement())
      await Promise.all(loads)
    } catch (err: unknown) {
      error.value = safeMessage(err, 'Failed to load project connectors')
      projects.value = []
      currentProjectId.value = null
      connectors.value = []
      capabilities.value = []
    } finally {
      loading.value = false
    }
  }

  async function loadGlobalManagement(): Promise<void> {
    try {
      const response = await connectorsApi.listGlobalConnectors()
      globalConnectors.value = response.data
      canManageGlobal.value = true
    } catch {
      // This endpoint is the authorization source. A frontend username or a
      // visible global Connector in a project list never grants management.
      globalConnectors.value = []
      canManageGlobal.value = false
    }
  }

  async function loadProjectContext(projectId: string | null): Promise<void> {
    const generation = ++projectLoadGeneration
    if (!projectId || !projects.value.some((project) => project.id === projectId)) {
      currentProjectId.value = null
      connectors.value = []
      defaults.value = {}
      capabilities.value = []
      return
    }
    currentProjectId.value = projectId
    try {
      const [connectorResponse, braveDefault, tavilyDefault, capabilityResponse] = await Promise.all([
        connectorsApi.listProjectConnectors(projectId),
        connectorsApi.getProjectDefault(projectId, 'brave'),
        connectorsApi.getProjectDefault(projectId, 'tavily'),
        connectorsApi.getProjectCapabilities(projectId),
      ])
      if (generation !== projectLoadGeneration) return
      connectors.value = connectorResponse.data
      defaults.value = {
        brave: braveDefault.data,
        tavily: tavilyDefault.data,
      }
      capabilities.value = capabilityResponse.data
      error.value = null
    } catch (err: unknown) {
      if (generation !== projectLoadGeneration) return
      error.value = safeMessage(err, 'Failed to load project connector settings')
      connectors.value = []
      defaults.value = {}
      capabilities.value = []
    }
  }

  async function selectProject(projectId: string): Promise<void> {
    if (!projects.value.some((project) => project.id === projectId)) return
    await loadProjectContext(projectId)
  }

  async function setDefault(kind: ConnectorKind, connectorId: string | null): Promise<void> {
    const projectId = currentProjectId.value
    if (!projectId) return
    const current = defaults.value[kind]
    const response = await connectorsApi.setProjectDefault(projectId, kind, {
      expected_revision: current?.revision ?? 0,
      connector_id: connectorId,
    })
    const [connectorResponse, capabilityResponse] = await Promise.all([
      connectorsApi.listProjectConnectors(projectId),
      connectorsApi.getProjectCapabilities(projectId),
    ])
    defaults.value = { ...defaults.value, [kind]: response.data }
    connectors.value = connectorResponse.data
    capabilities.value = capabilityResponse.data
  }

  async function reload(): Promise<void> {
    await Promise.all([loadGlobalManagement(), loadProjectContext(currentProjectId.value)])
  }

  function clear(): void {
    projectLoadGeneration += 1
    projects.value = []
    currentProjectId.value = null
    connectors.value = []
    defaults.value = {}
    capabilities.value = []
    globalConnectors.value = []
    canManageGlobal.value = false
    loading.value = false
    error.value = null
  }

  return {
    projects,
    currentProjectId,
    connectors,
    defaults,
    capabilities,
    globalConnectors,
    canManageGlobal,
    loading,
    error,
    initialize,
    loadGlobalManagement,
    loadProjectContext,
    selectProject,
    setDefault,
    reload,
    clear,
  }
})

function safeMessage(err: unknown, fallback: string): string {
  const error = err as { response?: { data?: { error?: { message?: string }; detail?: string } } }
  return error.response?.data?.error?.message ?? error.response?.data?.detail ?? fallback
}
