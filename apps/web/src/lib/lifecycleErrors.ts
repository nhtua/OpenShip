export type LifecycleConflictCode = 'conversation_archived' | 'project_archived'

export function lifecycleConflictCode(error: unknown): LifecycleConflictCode | null {
  const response = error as {
    response?: {
      status?: number
      data?: {
        detail?: string | { error?: { code?: string } }
        error?: { code?: string }
      }
    }
  }
  if (response.response?.status !== 409) return null
  const body = response.response.data
  const detail = body?.detail
  const code = (typeof detail === 'object' ? detail.error?.code : undefined) ?? body?.error?.code
  return code === 'conversation_archived' || code === 'project_archived' ? code : null
}
