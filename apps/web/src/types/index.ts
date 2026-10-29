export interface User {
  id: string
  username: string
  email: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: User
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  email: string
  password: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  conversation_id: string | null
  created_at: string
  /** Durable run that created the message; absent on older conversation history. */
  run_id?: string | null
}

export interface Conversation {
  id: string
  project_id?: string
  project_name: string
  project_archived_at: string | null
  title: string
  created_at: string
  updated_at: string
  archived_at: string | null
}

// Durable run types
export interface Run {
  id: string
  conversation_id: string
  status: 'created' | 'queued' | 'running' | 'waiting_connector' | 'completed' | 'failed' | 'cancelled' | 'timed_out' | 'stale'
  created_at: string
  attempt: number
  kind?: 'chat' | 'tool'
  graph_version?: number
  // A run read states what it was pinned to and what it asked. Null means
  // "not recorded", never "the default".
  model?: RunModelSummary | null
  usage?: UsageSummary | null
  error?: ErrorSummary | null
  provider_attempts?: ProviderAttemptSummary[]
  operations?: ToolOperationSummary[]
  operations_next_cursor?: string | null
  connector_blocker?: ConnectorBlockerSummary | null
}

export interface RunCard {
  run_id: string
  kind: 'chat' | 'tool'
  graph_version: number
  status: string
  started_at: string | null
  ended_at: string | null
  // Provider-reported counts only. Null means nothing was reported — the card
  // says so, it never estimates.
  usage: UsageSummary | null
  error_code: string | null
  // Fixed copy for the failure, never a provider's own body.
  error_message: string | null
  model: RunModelSummary | null
  attempts: ProviderAttemptSummary[]
  operations: ToolOperationSummary[]
  operations_next_cursor: string | null
  connector_blocker?: ConnectorBlockerSummary | null
}

export interface ConnectorBlockerSummary {
  id: string
  request_id: string
  reason: string
  configuration_digest: string
  expires_at: string
  connector_kind: string
  connector_visibility: ConnectorVisibility
  connector_revision: number
  connector_id: string
}

export interface TurnSubmitRequest {
  content: string
  client_request_id: string
  // The profile this turn answers with. Omitting it means "whatever this
  // installation considers default"; naming one is what makes the turn
  // reproducible. A browser never names an endpoint, adapter, or credential.
  provider_profile_id?: string | null
  fallback_profile_ids?: string[] | null
  // Automatic tool selection is an opt-in for this submitted turn. The
  // server resolves and pins the exact enabled capability versions.
  enable_tools?: boolean
  connector_selections?: ConnectorSelections
}

export type ConnectorKind = 'brave' | 'tavily'
export type ManagedConnectorKind = ConnectorKind | 'aws'
export type ConnectorVisibility = 'global' | 'project'
export type ConnectorSelections = Partial<Record<ManagedConnectorKind, string>>

export interface ProjectSummary {
  id: string
  name: string
  archived_at: string | null
}

export type ArchiveListState = 'active' | 'archived'

export interface ConnectorUsage {
  id: string
  kind: ManagedConnectorKind
  visibility: ConnectorVisibility
  name: string
  status: 'configured' | 'disabled' | 'expired' | 'revoked' | 'unavailable'
  revision: number
  is_default: boolean
}

export interface ProjectConnectorDefault {
  service: ConnectorKind
  connector_id: string | null
  revision: number
}

export interface ProjectCapabilitySummary {
  id: string
  version: string
  manifest_digest: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  effect: 'read_only_external' | 'read_only_local'
  permitted: boolean
  required_connector_kinds: ManagedConnectorKind[]
  connector_readiness: Partial<Record<ManagedConnectorKind, 'ready' | 'required' | 'ambiguous' | 'unavailable'>>
  enabled?: boolean
  available?: boolean
  policy_enabled?: boolean
  deployment_restricted?: boolean
  unavailable_reason?:
    | 'disabled'
    | 'disabled_by_operator'
    | 'deployment_restricted'
    | 'missing_credentials'
    | 'runtime_unavailable'
    | 'connector_required'
    | 'connector_ambiguous'
    | 'target_required'
    | 'policy_unavailable'
    | null
}

export interface ToolPolicyEntry {
  id: string
  description: string
  enabled: boolean
  permitted: boolean
  deployment_restricted: boolean
  unavailable_reason: 'disabled_by_operator' | 'deployment_restricted' | null
}

export interface ToolPolicyResponse {
  revision: number
  tools: ToolPolicyEntry[]
  effective_ids: string[]
  deployment_ceiling_active: boolean
  deployment_ceiling_ids: string[] | null
  unknown_deployment_ids: string[]
  cutover_available: boolean
  legacy_cutover_at: string | null
}

export interface ConnectorRequestStatus {
  status: 'processing' | 'complete'
  result?: Record<string, unknown>
}

export type ConnectorSetupUnavailableReason =
  | 'installation_id_missing'
  | 'encryption_key_unavailable'
  | 'idempotency_key_unavailable'

export interface ConnectorSetupContext {
  can_manage_global: boolean
  credential_store_ready: boolean
  unavailable_reason: ConnectorSetupUnavailableReason | null
}

// What an installation offers. Deliberately contains no credential value,
// no name of a credential binding, and no full endpoint URL — only a
// destination label a person can recognise.
export interface ModelProfileItem {
  profile_id: string
  label: string
  adapter: string
  model: string
  destination_label: string | null
  capabilities: Record<string, unknown>
  available: boolean
  reason: string | null
  explanation: string
  allowed_fallback_profile_ids: string[]
}

export interface ModelProfileCatalog {
  default_profile_id: string
  profiles: ModelProfileItem[]
}

// What a run was pinned to. Null means "not recorded": a run from before
// profiles exists, and nothing is guessed for it.
export interface RunModelSummary {
  profile_id: string
  label: string
  adapter: string
  requested_model: string
  serving_context_tokens?: number | null
  actual_model: string | null
  destination_label: string | null
  serving_profile_id?: string | null
  serving_label?: string | null
  serving_adapter?: string | null
  serving_destination_label?: string | null
  fallback_profile_ids: string[]
  recorded: boolean
}

// Provider-reported counts only. Missing stays missing; nothing is estimated.
export interface UsageSummary {
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  cached_input_tokens: number | null
  reasoning_tokens: number | null
  source: string | null
}

export interface ErrorSummary {
  code: string
  message: string
  retry_after_seconds: number | null
}

// One bounded request a worker made for a run.
export interface ProviderAttemptSummary {
  ordinal: number
  profile_id: string
  adapter: string
  requested_model: string
  actual_model: string | null
  destination_label: string | null
  outcome: string
  error_code: string | null
  usage: UsageSummary | null
  started_at: string | null
  ended_at: string | null
}

export interface DurableEvent {
  id: string
  conversation_id: string
  run_id: string | null
  sequence: number
  type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface Snapshot {
  conversation_id: string
  project_id?: string
  sequence: number
  events: DurableEvent[]
  runs?: SnapshotRun[]
  artifacts?: ArtifactSummary[]
  artifacts_next_cursor?: string | null
}

export interface SnapshotRun {
  id: string
  status: Run['status']
  attempt: number
  started_at: string | null
  ended_at: string | null
  error_code: string | null
  kind?: 'chat' | 'tool'
  graph_version?: number
  model?: RunModelSummary | null
  usage?: UsageSummary | null
  error?: ErrorSummary | null
  provider_attempts?: ProviderAttemptSummary[]
  operations?: ToolOperationSummary[]
  operations_next_cursor?: string | null
  connector_blocker?: ConnectorBlockerSummary | null
}

export interface CapabilityRef {
  id: string
  version: string
  manifest_digest: string
}

export interface CapabilitySummary extends CapabilityRef {
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  effect: 'read_only_external' | 'read_only_local'
  enabled: boolean
  available: boolean
  unavailable_reason:
    | 'disabled'
    | 'disabled_by_operator'
    | 'deployment_restricted'
    | 'policy_unavailable'
    | 'missing_credentials'
    | 'runtime_unavailable'
    | null
  deployment_restricted?: boolean
  permitted?: boolean
  required_connector_kinds?: ManagedConnectorKind[]
  connector_readiness?: Partial<Record<ManagedConnectorKind, 'ready' | 'required' | 'ambiguous' | 'unavailable'>>
}

export interface ToolCommandRequest {
  client_request_id: string
  capability: CapabilityRef
  arguments: Record<string, unknown>
  connector_selections?: ConnectorSelections
}

export interface AwsBootstrapAuthInput {
  access_key_id: string
  secret_access_key: string
  session_token?: string | null
  expires_at?: string | null
}

export interface ProjectAwsConnectorCreate {
  name: string
  configuration: {
    schema_version: 1
    kind: 'aws'
    region: string
    expected_account_id: string
    basic_role_arn: string
  }
  auth: AwsBootstrapAuthInput
}

export interface EnvironmentSummary {
  id: string
  project_id: string
  name: string
  display_name: string
  created_at: string
}

export interface ResourceBindingSummary {
  resource_id: string
  uri: string
  display_name: string
  environment_id: string
  environment_name: string
  binding_id: string
  binding_revision: number
  connector_id: string
  connector_revision: number
  account_id: string
  region: string
  bucket_name: string
  bucket_arn: string
  expected_owner: string
  management_method: 'external' | 'unknown'
  state: 'active' | 'retired'
  created_at: string
}

export interface ResourceObservationSummary {
  id: string
  resource_id: string
  binding_id: string
  binding_revision: number
  run_id: string
  operation_id: string
  source: string
  retrieved_at: string
  measurement_at: string | null
  outcome: 'measured' | 'no_data' | 'partial' | 'failed'
  coverage: Record<string, unknown>
  evidence: { id: string; sha256: string; size_bytes: number; media_type: string } | null
  evidence_status: 'staging' | 'ready' | 'deleting' | 'deleted' | 'unavailable' | null
}

export interface ResourcePage {
  items: ResourceBindingSummary[]
  next_cursor: string | null
}

export interface ResourceObservationPage {
  items: ResourceObservationSummary[]
  next_cursor: string | null
}

export interface ArtifactSummary {
  id: string
  sha256: string
  size_bytes: number
  media_type: string
  conversation_id: string
  project_id: string
  name: string
  source: string
  status: 'staging' | 'ready' | 'deleting' | 'deleted' | 'unavailable'
  created_at: string
  mutability: 'immutable' | 'mutable'
}

export interface LogicalFileSummary {
  id: string
  name: string
  current_version_id: string
  current_artifact: {
    id: string
    sha256: string
    size_bytes: number
    media_type: string
    status: ArtifactSummary['status']
  }
  created_at: string
}

export interface LogicalFileVersion {
  id: string
  file_id: string
  artifact_id: string
  base_version_id: string | null
  created_at: string
}

export interface SearchResultItem {
  title: string
  url: string
  snippet: string
  rank: number
}

export interface ToolFailureSummary {
  code: string
  message: string
}

export interface ToolAttemptSummary {
  id: string
  operation_id: string
  ordinal: number
  fence: number
  dispatch_started_at: string | null
  finished_at: string | null
  outcome: string
  error_code: string | null
}

export interface ToolOperationSummary {
  id: string
  run_id: string
  conversation_id: string
  logical_call_id: string
  capability_id: string
  capability_version: string
  manifest_digest: string
  status: 'admitted' | 'dispatching' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
  error_code: string | null
  error: ToolFailureSummary | null
  // Added with the owned operation summary; absent on legacy snapshots.
  query?: string | null
  result: {
    operation_id?: string
    connector_id?: string
    connector_revision?: number
    catalog_digest?: string
    service?: string
    operation?: string
    sdk_version?: string
    operations?: Array<{
      service: string
      operation: string
      api_name: string
      supported: boolean
      unsupported_reason?: string | null
      parameter_schema?: Record<string, unknown> | null
    }>
    preview?: Record<string, unknown>
    preview_truncated?: boolean
    next_cursor?: string | null
    observed_at?: string
    query?: string
    items?: SearchResultItem[]
    truncated?: boolean
    resource_id?: string
    resource_binding_id?: string
    binding_revision?: number
    account_id?: string
    region?: string
    bucket_name?: string
    versioning_status?: 'enabled' | 'suspended' | 'not_enabled'
    mfa_delete_status?: 'enabled' | 'disabled' | 'not_reported'
    request_count?: number
    retrieved_at?: string
    outcome?: 'measured' | 'no_data' | 'partial'
    stale?: boolean
    measurement_at?: string | null
    reported_storage_subtotal_bytes?: number | null
    coverage?: Record<string, unknown>
    artifact?: { id: string; sha256: string; size_bytes: number; media_type: string }
    status?: 'ready'
    role_arn?: string
    bucket_verified?: boolean
    checked_at?: string
  } | null
  artifacts: ArtifactSummary[]
  attempts: ToolAttemptSummary[]
  created_at: string | null
  updated_at: string | null
  finished_at: string | null
}

export interface OperationPage {
  items: ToolOperationSummary[]
  next_cursor: string | null
}

export interface ArtifactPage {
  items: ArtifactSummary[]
  next_cursor: string | null
}

export interface ConversationWorkspaceSummary {
  id: string
  conversation_id: string
  project_id: string
  schema_version: 1
  relative_root: string
  storage_id: string
  status: 'available' | 'unavailable' | 'deleted'
  created_at: string
  quota: {
    committed_bytes: number
    committed_count: number
    reserved_bytes: number
    reserved_count: number
    max_bytes: number
    max_count: number
  }
}

export interface ConversationWorkspacePage {
  workspace: ConversationWorkspaceSummary
  files: ArtifactPage
}

export interface WorkspaceFileRead {
  workspace_id: string
  artifact_id: string
  version_id: string
  sha256: string
  media_type: string
  size_bytes: number
  offset: number
  content: string
  next_offset: number | null
}
