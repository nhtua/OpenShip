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

export interface SSEChunkEvent {
  type: 'chunk'
  content: string
}

export interface SSECompleteEvent {
  type: 'complete'
  message_id: string
}

export type SSEEvent = SSEChunkEvent | SSECompleteEvent


