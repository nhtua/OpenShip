import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/services/api'
import type {
  ChatMessage,
  Conversation,
  SSEEvent,
  SSEChunkEvent,
  SSECompleteEvent,
} from '@/types'

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>([])
  const currentConversation = ref<Conversation | null>(null)
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const error = ref<string | null>(null)

  async function getConversations() {
    try {
      const res = await api.get('/workspace/conversations')
      conversations.value = res.data as unknown as Conversation[]
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      error.value =
        axiosErr.response?.data?.detail ?? 'Failed to load conversations'
    }
  }

  async function createConversation(title: string): Promise<Conversation | null> {
    try {
      const res = await api.post('/conversations', { title })
      const conv = res.data as unknown as Conversation
      conversations.value.push(conv)
      return conv
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      error.value =
        axiosErr.response?.data?.detail ?? 'Failed to create conversation'
      return null
    }
  }

  async function loadConversation(conversationId: string) {
    try {
      // Fetch messages BEFORE swapping the displayed conversation so a
      // failed load keeps the previous selection intact (plan Task 5.4).
      const res = await api.get(`/conversations/${conversationId}/messages`)
      const known = conversations.value.find((c) => c.id === conversationId)
      currentConversation.value = {
        id: conversationId,
        title: known?.title || 'Conversation',
        created_at: known?.created_at ?? '',
        updated_at: known?.updated_at ?? '',
      }
      messages.value = res.data as unknown as ChatMessage[]
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      error.value =
        axiosErr.response?.data?.detail ?? 'Failed to load conversation'
    }
  }

  function appendMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function appendStreamingContent(content: string) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && !last.id) {
      last.content += content
    }
  }

  function finalizeStreamingMessage(messageId: string) {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && !last.id) {
      last.id = messageId
    }
  }

  async function sendMessage(
    content: string,
    conversationId: string | null = null,
  ) {
    error.value = null
    isStreaming.value = true

    try {
      // Create user message locally for optimistic UI
      const userMsg: ChatMessage = {
        id: `temp-${Date.now()}`,
        role: 'user',
        content,
        conversation_id: conversationId,
        created_at: new Date().toISOString(),
      }
      appendMessage(userMsg)

      // Create placeholder for assistant response
      const assistantMsg: ChatMessage = {
        id: '',
        role: 'assistant',
        content: '',
        conversation_id: conversationId,
        created_at: new Date().toISOString(),
      }
      appendMessage(assistantMsg)

      // Open SSE connection using fetch
      const token = localStorage.getItem('access_token')
      const convId = conversationId || 'new'
      const url = `/api/chat/${convId}/messages`
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          content,
          conversation_id: conversationId,
        }),
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: Failed to send message`)
      }

      if (!response.body) {
        throw new Error('No response body received')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })

        // Process complete SSE lines
        const lines = buffer.split('\n\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const jsonStr = line.slice(6)
          try {
            const event: SSEEvent = JSON.parse(jsonStr)
            if (event.type === 'chunk') {
              appendStreamingContent((event as SSEChunkEvent).content)
            } else if (event.type === 'complete') {
              finalizeStreamingMessage(
                (event as SSECompleteEvent).message_id,
              )
            } else if (event.type === 'conversation_created') {
              const convEvent = event as { conversation_id: string }
              currentConversation.value = {
                id: convEvent.conversation_id,
                title: 'New Conversation',
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              }
              // Add to conversations list
              conversations.value.unshift(currentConversation.value)
            }
          } catch {
            // Skip malformed SSE events
          }
        }
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      error.value = `Failed to send message: ${message}`
      // Remove the assistant placeholder on error
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant') {
        messages.value.pop()
      }
    } finally {
      isStreaming.value = false
    }
  }

  function clearMessages() {
    messages.value = []
    currentConversation.value = null
  }

  return {
    conversations,
    currentConversation,
    messages,
    isStreaming,
    error,
    getConversations,
    createConversation,
    loadConversation,
    sendMessage,
    appendMessage,
    clearMessages,
  }
})
