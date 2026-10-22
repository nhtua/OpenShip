import { defineStore } from 'pinia'
import { ref } from 'vue'
import { projectsApi } from '@/services/api'
import type { ProjectSummary } from '@/types'

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<ProjectSummary[]>([])
  const currentProjectId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let loadGeneration = 0

  async function initialize(): Promise<void> {
    const generation = ++loadGeneration
    loading.value = true
    error.value = null
    try {
      const response = await projectsApi.list()
      if (generation !== loadGeneration) return
      projects.value = response.data
      if (!projects.value.some((project) => project.id === currentProjectId.value)) {
        currentProjectId.value = projects.value[0]?.id ?? null
      }
    } catch (err: unknown) {
      if (generation !== loadGeneration) return
      error.value = safeMessage(err, 'Failed to load projects')
      projects.value = []
      currentProjectId.value = null
    } finally {
      if (generation === loadGeneration) loading.value = false
    }
  }

  async function createProject(name: string): Promise<ProjectSummary> {
    const normalized = validateName(name)
    loadGeneration += 1
    loading.value = false
    error.value = null
    try {
      const response = await projectsApi.create({ name: normalized })
      const created = response.data
      if (!projects.value.some((project) => project.id === created.id)) {
        projects.value = [...projects.value, created]
      }
      currentProjectId.value = created.id
      return created
    } catch (err: unknown) {
      error.value = safeMessage(err, 'Failed to create project')
      throw err
    }
  }

  async function renameProject(projectId: string, name: string): Promise<ProjectSummary> {
    const normalized = validateName(name)
    if (!projects.value.some((project) => project.id === projectId)) {
      throw new Error('Project not found')
    }
    loadGeneration += 1
    loading.value = false
    error.value = null
    try {
      const response = await projectsApi.rename(projectId, { name: normalized })
      projects.value = projects.value.map((project) =>
        project.id === projectId ? response.data : project,
      )
      return response.data
    } catch (err: unknown) {
      error.value = safeMessage(err, 'Failed to rename project')
      throw err
    }
  }

  async function deleteProject(projectId: string): Promise<void> {
    if (!projects.value.some((project) => project.id === projectId)) {
      throw new Error('Project not found')
    }
    loadGeneration += 1
    loading.value = false
    error.value = null
    try {
      await projectsApi.delete(projectId)
      projects.value = projects.value.filter((project) => project.id !== projectId)
      if (currentProjectId.value === projectId) {
        currentProjectId.value = projects.value[0]?.id ?? null
      }
    } catch (err: unknown) {
      error.value = safeMessage(err, 'Failed to delete project')
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
    loadGeneration += 1
    projects.value = []
    currentProjectId.value = null
    loading.value = false
    error.value = null
  }

  return {
    projects,
    currentProjectId,
    loading,
    error,
    initialize,
    createProject,
    renameProject,
    deleteProject,
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
