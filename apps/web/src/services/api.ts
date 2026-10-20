import axios from 'axios'
import type {
  ArtifactPage,
  ArtifactSummary,
  AuthResponse,
  CapabilitySummary,
  ConversationWorkspacePage,
  OperationPage,
  Run,
  Snapshot,
  ToolCommandRequest,
  TurnSubmitRequest,
  ModelProfileCatalog,
  WorkspaceFileRead,
} from '@/types'

const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
    }
    return Promise.reject(error)
  },
)

export const authApi = {
  login: (username: string, password: string): Promise<{ data: AuthResponse }> =>
    api.post('/auth/login', { username, password }),

  register: (
    username: string,
    email: string,
    password: string,
  ): Promise<{ data: AuthResponse }> =>
    api.post('/auth/register', { username, email, password }),

  logout: (): Promise<void> => api.post('/auth/logout'),
}

// Durable run commands
export const runsApi = {
  submitTurn: (
    conversationId: string,
    request: TurnSubmitRequest,
  ): Promise<{ data: Run }> =>
    api.post(`/runs/${conversationId}/turns`, request),

  cancelRun: (runId: string): Promise<{ data: Run }> =>
    api.post(`/runs/${runId}/cancel`, {}),

  getSnapshot: (conversationId: string): Promise<{ data: Snapshot }> =>
    api.get(`/conversations/${conversationId}/snapshot`),

  getRun: (runId: string): Promise<{ data: Run }> =>
    api.get(`/runs/${runId}`),

  submitTool: (
    conversationId: string,
    request: ToolCommandRequest,
  ): Promise<{ data: Run }> => api.post(`/runs/${conversationId}/tools`, request),

  getOperations: (
    runId: string,
    params: { limit?: number; cursor?: string } = {},
  ): Promise<{ data: OperationPage }> => api.get(`/runs/${runId}/operations`, { params }),
}

// The installation's own model profiles: what may be selected, and nothing
// secret. A browser names a profile and never an endpoint, adapter, or
// credential.
export const modelProfilesApi = {
  list: (): Promise<{ data: ModelProfileCatalog }> => api.get('/model-profiles'),
}

export const capabilitiesApi = {
  list: (): Promise<{ data: CapabilitySummary[] }> => api.get('/capabilities'),
}

export const artifactsApi = {
  list: (
    conversationId: string,
    params: { limit?: number; cursor?: string } = {},
  ): Promise<{ data: ArtifactPage }> =>
    api.get(`/conversations/${conversationId}/artifacts`, { params }),

  download: (artifactId: string): Promise<{ data: Blob }> =>
    api.get(`/artifacts/${artifactId}/content`, { responseType: 'blob' }),

  delete: (artifactId: string): Promise<{ data: ArtifactSummary }> =>
    api.delete(`/artifacts/${artifactId}`),
}

export const workspaceApi = {
  get: (
    conversationId: string,
    params: { limit?: number; cursor?: string } = {},
  ): Promise<{ data: ConversationWorkspacePage }> =>
    api.get(`/conversations/${conversationId}/workspace`, { params }),

  readFile: (
    conversationId: string,
    artifactId: string,
    params: { offset?: number; max_bytes?: number } = {},
  ): Promise<{ data: WorkspaceFileRead }> =>
    api.get(`/conversations/${conversationId}/workspace/files/${artifactId}`, { params }),
}

export default api
