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
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

// Durable run types
export interface Run {
  id: string
  conversation_id: string
  status: 'created' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'timed_out' | 'stale'
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
  unavailable_reason: 'disabled' | 'missing_credentials' | 'runtime_unavailable' | null
}

export interface ToolCommandRequest {
  client_request_id: string
  capability: CapabilityRef
  arguments: { query: string; max_results: number }
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
    observed_at?: string
    query?: string
    items?: SearchResultItem[]
    truncated?: boolean
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
