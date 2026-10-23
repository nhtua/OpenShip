import axios from 'axios'
import { generateRequestId } from '@/lib/requestId'
import type {
  ArtifactPage,
  ArtifactSummary,
  ArchiveListState,
  AuthResponse,
  CapabilitySummary,
  ConnectorKind,
  ConnectorRequestStatus,
  ConnectorSetupContext,
  ConnectorUsage,
  EnvironmentSummary,
  ConversationWorkspacePage,
  Conversation,
  OperationPage,
  Run,
  Snapshot,
  ToolCommandRequest,
  ToolPolicyResponse,
  TurnSubmitRequest,
  ModelProfileCatalog,
  ProjectCapabilitySummary,
  ProjectConnectorDefault,
  ProjectSummary,
  ProjectAwsConnectorCreate,
  ResourceObservationPage,
  ResourcePage,
  ResourceBindingSummary,
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

export const projectsApi = {
  list: (state: ArchiveListState = 'active'): Promise<{ data: ProjectSummary[] }> =>
    api.get('/projects', { params: { state } }),

  create: (payload: { name: string }): Promise<{ data: ProjectSummary }> =>
    api.post('/projects', payload),

  get: (projectId: string): Promise<{ data: ProjectSummary }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}`),

  rename: (projectId: string, payload: { name: string }): Promise<{ data: ProjectSummary }> =>
    api.patch(`/projects/${encodeURIComponent(projectId)}`, payload),

  archive: (projectId: string): Promise<{ data: ProjectSummary }> =>
    api.post(`/projects/${encodeURIComponent(projectId)}/archive`),

  restore: (projectId: string): Promise<{ data: ProjectSummary }> =>
    api.post(`/projects/${encodeURIComponent(projectId)}/restore`),
}

export const conversationsApi = {
  list: (state: ArchiveListState = 'active'): Promise<{ data: Conversation[] }> =>
    api.get('/workspace/conversations', { params: { state } }),

  get: (conversationId: string): Promise<{ data: Conversation }> =>
    api.get(`/conversations/${encodeURIComponent(conversationId)}`),

  archive: (conversationId: string): Promise<{ data: Conversation }> =>
    api.post(`/conversations/${encodeURIComponent(conversationId)}/archive`),

  restore: (conversationId: string): Promise<{ data: Conversation }> =>
    api.post(`/conversations/${encodeURIComponent(conversationId)}/restore`),
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

  resumeConnector: (
    runId: string,
    payload: { blocker_id: string; request_id: string; expected_configuration_digest: string },
  ): Promise<{ data: Run }> =>
    api.post(`/runs/${encodeURIComponent(runId)}/resume-connector`, payload),

  dismissConnector: (
    runId: string,
    payload: { blocker_id: string },
  ): Promise<{ data: Run }> =>
    api.post(`/runs/${encodeURIComponent(runId)}/dismiss-connector`, payload),

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

export const toolPolicyApi = {
  get: (): Promise<{ data: ToolPolicyResponse }> => api.get('/global-tool-policy'),

  set: (
    capabilityId: string,
    payload: { enabled: boolean; expected_revision: number },
  ): Promise<{ data: ToolPolicyResponse }> =>
    api.put(`/global-tool-policy/${encodeURIComponent(capabilityId)}`, payload),

  cutover: (
    payload: { expected_revision: number },
  ): Promise<{ data: ToolPolicyResponse }> =>
    api.post('/global-tool-policy/cutover', payload),
}

export const connectorsApi = {
  getSetupContext: (): Promise<{ data: ConnectorSetupContext }> =>
    api.get('/connectors/setup-context'),

  listProjectConnectors: (
    projectId: string,
    service?: ConnectorKind,
  ): Promise<{ data: ConnectorUsage[] }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}/connectors`, {
      params: service ? { service } : undefined,
    }),

  getProjectConnector: (projectId: string, connectorId: string) =>
    api.get(`/projects/${encodeURIComponent(projectId)}/connectors/${encodeURIComponent(connectorId)}`),

  createProjectAwsConnector: (
    projectId: string,
    requestId: string,
    payload: ProjectAwsConnectorCreate,
  ) => api.post(`/projects/${encodeURIComponent(projectId)}/aws-connectors`, payload, {
    headers: { 'Idempotency-Key': requestId },
  }),

  replaceProjectAwsCredential: (
    projectId: string,
    connectorId: string,
    requestId: string,
    payload: { expected_revision: number; auth: ProjectAwsConnectorCreate['auth'] },
  ) => api.post(
    `/projects/${encodeURIComponent(projectId)}/connectors/${encodeURIComponent(connectorId)}/aws-credentials`,
    payload,
    { headers: { 'Idempotency-Key': requestId } },
  ),

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

  checkProjectConnector: (
    projectId: string,
    connectorId: string,
  ): Promise<{ data: { status: 'valid' | 'invalid' | 'unavailable'; message: string; revision: number } }> =>
    api.post(`/projects/${encodeURIComponent(projectId)}/connectors/${encodeURIComponent(connectorId)}/check`, {}),

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

  checkGlobalConnector: (
    connectorId: string,
  ): Promise<{ data: { status: 'valid' | 'invalid' | 'unavailable'; message: string; revision: number } }> =>
    api.post(`/global-connectors/${encodeURIComponent(connectorId)}/check`, {}),

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

export const resourcesApi = {
  listEnvironments: (projectId: string): Promise<{ data: EnvironmentSummary[] }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}/environments`),

  createEnvironment: (
    projectId: string,
    payload: { name: string; display_name: string },
  ): Promise<{ data: EnvironmentSummary }> =>
    api.post(`/projects/${encodeURIComponent(projectId)}/environments`, payload),

  listBindings: (projectId: string): Promise<{ data: ResourcePage }> =>
    api.get(`/projects/${encodeURIComponent(projectId)}/resources`),

  createBinding: (
    projectId: string,
    payload: {
      environment_id: string
      connector_id: string
      connector_revision: number
      bucket_name: string
      display_name: string
    },
  ): Promise<{ data: ResourceBindingSummary }> =>
    api.post(`/projects/${encodeURIComponent(projectId)}/resources`, payload),

  listObservations: (
    projectId: string,
    resourceId: string,
  ): Promise<{ data: ResourceObservationPage }> =>
    api.get(
      `/projects/${encodeURIComponent(projectId)}/resources/${encodeURIComponent(resourceId)}/observations`,
    ),
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
