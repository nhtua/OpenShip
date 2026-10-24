import { defineStore } from 'pinia'
import { ref } from 'vue'
import { projectsApi } from '@/services/api'
import type { ProjectSummary } from '@/types'

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<ProjectSummary[]>([])
  const archivedProjects = ref<ProjectSummary[]>([])
  const currentProjectId = ref<string | null>(null)
  const loading = ref(false)
  const archivedLoading = ref(false)
  const error = ref<string | null>(null)
  let activeLoadGeneration = 0
  let archivedLoadGeneration = 0
  let identityGeneration = 0
  const projectOperationGenerations = new Map<string, number>()

  function invalidateLists(): void {
    activeLoadGeneration += 1
    archivedLoadGeneration += 1
    loading.value = false
    archivedLoading.value = false
  }

  async function initialize(): Promise<void> {
    const generation = ++activeLoadGeneration
    const identity = identityGeneration
    loading.value = true
    error.value = null
    try {
      const response = await projectsApi.list('active')
      if (generation !== activeLoadGeneration || identity !== identityGeneration) return
      projects.value = response.data
      if (!projects.value.some((project) => project.id === currentProjectId.value)) {
        currentProjectId.value = projects.value[0]?.id ?? null
      }
    } catch (err: unknown) {
      if (generation !== activeLoadGeneration || identity !== identityGeneration) return
      error.value = safeMessage(err, 'Failed to load projects')
      projects.value = []
      currentProjectId.value = null
    } finally {
      if (generation === activeLoadGeneration && identity === identityGeneration) loading.value = false
    }
  }

  async function loadArchivedProjects(): Promise<void> {
    const generation = ++archivedLoadGeneration
    const identity = identityGeneration
    archivedLoading.value = true
    error.value = null
    try {
      const response = await projectsApi.list('archived')
      if (generation !== archivedLoadGeneration || identity !== identityGeneration) return
      archivedProjects.value = response.data
    } catch (err: unknown) {
      if (generation !== archivedLoadGeneration || identity !== identityGeneration) return
      error.value = safeMessage(err, 'Failed to load archived projects')
      archivedProjects.value = []
    } finally {
      if (generation === archivedLoadGeneration && identity === identityGeneration) archivedLoading.value = false
    }
  }

  async function createProject(name: string): Promise<ProjectSummary> {
    const normalized = validateName(name)
    invalidateLists()
    const identity = identityGeneration
    error.value = null
    try {
      const response = await projectsApi.create({ name: normalized })
      const created = response.data
      if (identity === identityGeneration) {
        if (!projects.value.some((project) => project.id === created.id)) {
          projects.value = [...projects.value, created]
        }
        currentProjectId.value = created.id
      }
      return created
    } catch (err: unknown) {
      if (identity === identityGeneration) error.value = safeMessage(err, 'Failed to create project')
      throw err
    }
  }

  async function renameProject(projectId: string, name: string): Promise<ProjectSummary> {
    const normalized = validateName(name)
    if (!projects.value.some((project) => project.id === projectId)) {
      throw new Error('Project not found')
    }
    invalidateLists()
    const identity = identityGeneration
    error.value = null
    try {
      const response = await projectsApi.rename(projectId, { name: normalized })
      if (identity === identityGeneration) {
        projects.value = projects.value.map((project) =>
          project.id === projectId ? response.data : project,
        )
      }
      return response.data
    } catch (err: unknown) {
      if (identity === identityGeneration) error.value = safeMessage(err, 'Failed to rename project')
      throw err
    }
  }

  async function archiveProject(projectId: string): Promise<void> {
    if (!projects.value.some((project) => project.id === projectId)) {
      throw new Error('Project not found')
    }
    invalidateLists()
    const identity = identityGeneration
    const operation = (projectOperationGenerations.get(projectId) ?? 0) + 1
    projectOperationGenerations.set(projectId, operation)
    error.value = null
    try {
      const response = await projectsApi.archive(projectId)
      if (identity !== identityGeneration || projectOperationGenerations.get(projectId) !== operation) return
      projects.value = projects.value.filter((project) => project.id !== projectId)
      archivedProjects.value = [
        response.data,
        ...archivedProjects.value.filter((project) => project.id !== projectId),
      ]
      if (currentProjectId.value === projectId) {
        currentProjectId.value = projects.value[0]?.id ?? null
      }
    } catch (err: unknown) {
      if (identity === identityGeneration && projectOperationGenerations.get(projectId) === operation) {
        error.value = safeMessage(err, 'Failed to archive project')
        if (statusCode(err) === 409) {
          const message = error.value
          await Promise.all([initialize(), loadArchivedProjects()])
          if (identity === identityGeneration) error.value = message
        }
      }
      throw err
    }
  }

  async function restoreProject(projectId: string): Promise<ProjectSummary> {
    if (!archivedProjects.value.some((project) => project.id === projectId)) {
      throw new Error('Archived project not found')
    }
    invalidateLists()
    const identity = identityGeneration
    const operation = (projectOperationGenerations.get(projectId) ?? 0) + 1
    projectOperationGenerations.set(projectId, operation)
    error.value = null
    try {
      const response = await projectsApi.restore(projectId)
      if (identity === identityGeneration && projectOperationGenerations.get(projectId) === operation) {
        archivedProjects.value = archivedProjects.value.filter((project) => project.id !== projectId)
        projects.value = [...projects.value.filter((project) => project.id !== projectId), response.data]
      }
      return response.data
    } catch (err: unknown) {
      if (identity === identityGeneration && projectOperationGenerations.get(projectId) === operation) {
        error.value = safeMessage(err, 'Failed to restore project')
        if (statusCode(err) === 409) {
          const message = error.value
          await Promise.all([initialize(), loadArchivedProjects()])
          if (identity === identityGeneration) error.value = message
        }
      }
      throw err
    }
  }

  function selectProject(projectId: string): boolean {
    if (!projects.value.some((project) => project.id === projectId)) return false
    currentProjectId.value = projectId
    error.value = null
    return true
  }

  function clear(): void {
    identityGeneration += 1
    projectOperationGenerations.clear()
    invalidateLists()
    projects.value = []
    archivedProjects.value = []
    currentProjectId.value = null
    error.value = null
  }

  return {
    projects,
    archivedProjects,
    currentProjectId,
    loading,
    archivedLoading,
    error,
    initialize,
    loadArchivedProjects,
    createProject,
    renameProject,
    archiveProject,
    restoreProject,
    selectProject,
    clear,
  }
})

function validateName(value: string): string {
  const name = value.trim()
  if (!name || name.length > 255) {
    throw new Error('Project name must be 1 to 255 characters after trimming')
  }
  return name
}

function safeMessage(err: unknown, fallback: string): string {
  const response = err as {
    response?: {
      data?: {
        error?: { message?: string }
        detail?: string | { error?: { message?: string } }
      }
    }
  }
  const data = response.response?.data
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  return data?.error?.message ?? (detail ? detail.error?.message : undefined) ?? fallback
}

function statusCode(err: unknown): number | undefined {
  return (err as { response?: { status?: number } }).response?.status
}
