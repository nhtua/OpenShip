import { defineStore } from 'pinia'
import { ref } from 'vue'
import { connectorsApi } from '@/services/api'
import type {
  ConnectorKind,
  ConnectorUsage,
  ProjectCapabilitySummary,
  ProjectConnectorDefault,
  ConnectorSetupUnavailableReason,
} from '@/types'

export const useConnectorsStore = defineStore('connectors', () => {
  const connectors = ref<ConnectorUsage[]>([])
  const defaults = ref<Partial<Record<ConnectorKind, ProjectConnectorDefault>>>({})
  const capabilities = ref<ProjectCapabilitySummary[]>([])
  const globalConnectors = ref<ConnectorUsage[]>([])
  const canManageGlobal = ref(false)
  const credentialStoreReady = ref(false)
  const setupUnavailableReason = ref<ConnectorSetupUnavailableReason | null>(null)
  const loading = ref(false)
  const globalLoading = ref(false)
  const error = ref<string | null>(null)
  const globalError = ref<string | null>(null)
  let projectLoadGeneration = 0
  let loadedProjectId: string | null = null

  async function initialize(
    projectId: string | null,
    includeGlobalManagement = false,
  ): Promise<void> {
    const loads = [loadProjectContext(projectId)]
    if (includeGlobalManagement) loads.push(loadGlobalManagement())
    await Promise.all(loads)
  }

  async function loadGlobalManagement(): Promise<void> {
    globalLoading.value = true
    globalError.value = null
    try {
      let setup: Awaited<ReturnType<typeof connectorsApi.getSetupContext>>
      try {
        setup = await connectorsApi.getSetupContext()
      } catch {
        globalConnectors.value = []
        canManageGlobal.value = false
        credentialStoreReady.value = false
        setupUnavailableReason.value = null
        globalError.value = 'Failed to load global connector setup.'
        return
      }

      credentialStoreReady.value = setup.data.credential_store_ready
      setupUnavailableReason.value = setup.data.unavailable_reason
      canManageGlobal.value = setup.data.can_manage_global
      if (!setup.data.can_manage_global) {
        globalConnectors.value = []
        return
      }

      try {
        const response = await connectorsApi.listGlobalConnectors()
        globalConnectors.value = response.data
        canManageGlobal.value = true
      } catch {
        // Keep the safe readiness result, but require the management endpoint's
        // existing authorization check to succeed before showing its data.
        globalConnectors.value = []
        canManageGlobal.value = false
        globalError.value = 'Failed to load the global connector catalog.'
      }
    } finally {
      globalLoading.value = false
    }
  }

  async function loadProjectContext(projectId: string | null): Promise<void> {
    const generation = ++projectLoadGeneration
    loadedProjectId = projectId
    if (!projectId) {
      connectors.value = []
      defaults.value = {}
      capabilities.value = []
      error.value = null
      loading.value = false
      return
    }

    loading.value = true
    error.value = null
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
    } catch (err: unknown) {
      if (generation !== projectLoadGeneration) return
      error.value = safeMessage(err, 'Failed to load project connector settings')
      connectors.value = []
      defaults.value = {}
      capabilities.value = []
    } finally {
      if (generation === projectLoadGeneration) loading.value = false
    }
  }

  async function setDefault(
    projectId: string,
    kind: ConnectorKind,
    connectorId: string | null,
  ): Promise<void> {
    if (loadedProjectId !== projectId) return
    const generation = projectLoadGeneration
    const current = defaults.value[kind]
    await connectorsApi.setProjectDefault(projectId, kind, {
      expected_revision: current?.revision ?? 0,
      connector_id: connectorId,
    })
    if (generation !== projectLoadGeneration || loadedProjectId !== projectId) return
    const [connectorResponse, braveDefault, tavilyDefault, capabilityResponse] = await Promise.all([
      connectorsApi.listProjectConnectors(projectId),
      connectorsApi.getProjectDefault(projectId, 'brave'),
      connectorsApi.getProjectDefault(projectId, 'tavily'),
      connectorsApi.getProjectCapabilities(projectId),
    ])
    if (generation !== projectLoadGeneration || loadedProjectId !== projectId) return
    defaults.value = { brave: braveDefault.data, tavily: tavilyDefault.data }
    connectors.value = connectorResponse.data
    capabilities.value = capabilityResponse.data
  }

  async function reload(projectId: string | null): Promise<void> {
    await Promise.all([loadGlobalManagement(), loadProjectContext(projectId)])
  }

  function clear(): void {
    projectLoadGeneration += 1
    loadedProjectId = null
    connectors.value = []
    defaults.value = {}
    capabilities.value = []
    globalConnectors.value = []
    canManageGlobal.value = false
    credentialStoreReady.value = false
    setupUnavailableReason.value = null
    loading.value = false
    globalLoading.value = false
    error.value = null
    globalError.value = null
  }

  return {
    connectors,
    defaults,
    capabilities,
    globalConnectors,
    canManageGlobal,
    credentialStoreReady,
    setupUnavailableReason,
    loading,
    globalLoading,
    error,
    globalError,
    initialize,
    loadGlobalManagement,
    loadProjectContext,
    setDefault,
    reload,
    clear,
  }
})

function safeMessage(err: unknown, fallback: string): string {
  const error = err as { response?: { data?: { error?: { message?: string }; detail?: string } } }
  return error.response?.data?.error?.message ?? error.response?.data?.detail ?? fallback
}
