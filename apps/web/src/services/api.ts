import axios from 'axios'
import { generateRequestId } from '@/lib/requestId'
import type {
  ArtifactPage,
  ArtifactSummary,
  AuthResponse,
  CapabilitySummary,
  ConnectorKind,
  ConnectorRequestStatus,
  ConnectorSetupContext,
  ConnectorUsage,
  ConversationWorkspacePage,
  OperationPage,
  Run,
  Snapshot,
  ToolCommandRequest,
  TurnSubmitRequest,
  ModelProfileCatalog,
  ProjectCapabilitySummary,
  ProjectConnectorDefault,
  ProjectSummary,
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
    api.get(`/conversations/${encodeURIComponent(conversationId)}/snapshot`),

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

export const connectorsApi = {
  listProjects: (): Promise<{ data: ProjectSummary[] }> => api.get('/projects'),

  getSetupContext: (): Promise<{ data: ConnectorSetupContext }> =>
    api.get('/connectors/setup-context'),

  listProjectConnectors: (
    projectId: string,
    service?: ConnectorKind,
  ): Promise<{ data: ConnectorUsage[] }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}/connectors`, {
      params: service ? { service } : undefined,
    }),

  getProjectDefault: (
    projectId: string,
    service: ConnectorKind,
  ): Promise<{ data: ProjectConnectorDefault }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}/connector-defaults/${service}`),

  setProjectDefault: (
    projectId: string,
    service: ConnectorKind,
    payload: { expected_revision: number; connector_id: string | null },
  ): Promise<{ data: ProjectConnectorDefault }> =>
    api.put(
      `/projects/${encodeURIComponent(projectId)}/connector-defaults/${service}`,
      payload,
      { headers: { 'Idempotency-Key': generateRequestId() } },
    ),

  getProjectCapabilities: (
    projectId: string,
  ): Promise<{ data: ProjectCapabilitySummary[] }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}/capabilities`),

  createProjectConnector: (
    projectId: string,
    requestId: string,
    payload: { kind: ConnectorKind; name: string; configuration: Record<string, unknown>; api_key: string },
  ) => api.post(`/projects/${encodeURIComponent(projectId)}/connectors`, payload, {
    headers: { 'Idempotency-Key': requestId },
  }),

  updateProjectConnector: (
    projectId: string,
    connectorId: string,
    requestId: string,
    payload: { expected_revision: number; enabled?: boolean; revoke?: boolean; name?: string },
  ) => api.patch(
    `/projects/${encodeURIComponent(projectId)}/connectors/${encodeURIComponent(connectorId)}`,
    payload,
    { headers: { 'Idempotency-Key': requestId } },
  ),

  replaceProjectCredential: (
    projectId: string,
    connectorId: string,
    requestId: string,
    payload: { expected_revision: number; api_key: string },
  ) => api.post(
    `/projects/${encodeURIComponent(projectId)}/connectors/${encodeURIComponent(connectorId)}/credentials`,
    payload,
    { headers: { 'Idempotency-Key': requestId } },
  ),

  projectRequestStatus: (
    projectId: string,
    requestId: string,
  ): Promise<{ data: ConnectorRequestStatus }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}/connector-requests/${encodeURIComponent(requestId)}`),

  listGlobalConnectors: (): Promise<{ data: ConnectorUsage[] }> => api.get('/global-connectors'),

  createGlobalConnector: (
    requestId: string,
    payload: { kind: ConnectorKind; name: string; configuration: Record<string, unknown>; api_key: string },
  ) => api.post('/global-connectors', payload, { headers: { 'Idempotency-Key': requestId } }),

  updateGlobalConnector: (
    connectorId: string,
    requestId: string,
    payload: { expected_revision: number; enabled?: boolean; revoke?: boolean; name?: string },
  ) => api.patch(`/global-connectors/${encodeURIComponent(connectorId)}`, payload, {
    headers: { 'Idempotency-Key': requestId },
  }),

  replaceGlobalCredential: (
    connectorId: string,
    requestId: string,
    payload: { expected_revision: number; api_key: string },
  ) => api.post(
    `/global-connectors/${encodeURIComponent(connectorId)}/credentials`,
    payload,
    { headers: { 'Idempotency-Key': requestId } },
  ),

  globalRequestStatus: (requestId: string): Promise<{ data: ConnectorRequestStatus }> =>
    api.get(`/global-connectors/requests/${encodeURIComponent(requestId)}`),
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
