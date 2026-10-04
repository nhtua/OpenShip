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

export interface SendMessageRequest {
  content: string
  conversation_id?: string | null
}

// Legacy SSE events (still emitted by the adapter)
export interface SSEChunkEvent {
  type: 'chunk'
  content: string
}

export interface SSECompleteEvent {
  type: 'complete'
  message_id: string
}

export interface SSEConversationCreatedEvent {
  type: 'conversation_created'
  conversation_id: string
}

export interface SSETitleUpdatedEvent {
  type: 'title_updated'
  conversation_id: string
  title: string
}

export interface SSEMessageUpdatedEvent {
  type: 'message_updated'
  message_id: string
  content: string
}

export type SSEEvent =
  | SSEChunkEvent
  | SSECompleteEvent
  | SSEConversationCreatedEvent
  | SSETitleUpdatedEvent
  | SSEMessageUpdatedEvent

// Durable run types
export interface Run {
  id: string
  conversation_id: string
  status: 'created' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'timed_out' | 'stale'
  created_at: string
  attempt: number
}

export interface RunCard {
  run_id: string
  status: string
  started_at: string | null
  ended_at: string | null
  usage: string | null
  error_code: string | null
}

export interface TurnSubmitRequest {
  content: string
  client_request_id: string
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
}
