import { defineStore } from 'pinia'
import { ref } from 'vue'
import api, { runsApi } from '@/services/api'
import type {
  ChatMessage,
  Conversation,
  DurableEvent,
  RunCard,
  Snapshot,
} from '@/types'

// Stable client request ID generation
function generateRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // Fallback for environments without crypto.randomUUID
  return 'req-' + Date.now() + '-' + Math.random().toString(36).slice(2)
}

export const useChatStore = defineStore('chat', () => {
  const conversations = ref<Conversation[]>([])
  const currentConversation = ref<Conversation | null>(null)
  const messages = ref<ChatMessage[]>([])
  const isStreaming = ref(false)
  const error = ref<string | null>(null)

  // Durable run state
  // Run cards: one per conversation, keyed by conversation ID
  const runCards = ref<Map<string, RunCard>>(new Map())
  // Pending requests: request_id -> { conversation_id, content }
  const pendingRequests = ref<Map<string, { conversation_id: string; content: string }>>(new Map())
  // Applied event IDs (for deduplication)
  const appliedEventIds = ref<Set<string>>(new Set())
  // Cursor per conversation (last committed sequence)
  const cursors = ref<Map<string, number>>(new Map())

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
      conversations.value.unshift(conv)
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
      // Load snapshot first to rebuild durable state
      try {
        const snapRes = await runsApi.getSnapshot(conversationId)
        const snapshot = snapRes.data as unknown as Snapshot
        // Process snapshot events to rebuild state
        for (const event of snapshot.events) {
          processDurableEvent(event)
        }
        // Set the cursor to the snapshot's sequence (or the highest event sequence)
        const maxSeq = snapshot.events.reduce(
          (max, e) => Math.max(max, e.sequence),
          -1,
        )
        cursors.value.set(conversationId, Math.max(snapshot.sequence, maxSeq))
      } catch (snapErr) {
        // Snapshot may not be available yet; fall through to messages
        console.warn('Snapshot unavailable, falling back to messages', snapErr)
      }

      // Fetch messages (authoritative for message content)
      const res = await api.get(`/conversations/${conversationId}/messages`)
      const known = conversations.value.find((c) => c.id === conversationId)
      currentConversation.value = {
        id: conversationId,
        title: known?.title || 'Conversation',
        created_at: known?.created_at ?? '',
        updated_at: known?.updated_at ?? '',
      }
      messages.value = res.data as unknown as ChatMessage[]

      // If there's an active run for this conversation, subscribe to events
      const card = runCards.value.get(conversationId)
      if (card && ['queued', 'running', 'paused'].includes(card.status)) {
        const cursor = cursors.value.get(conversationId) ?? -1
        // Subscribe in background (don't block loadConversation)
        subscribeToEvents(conversationId, cursor, (event) => {
          processDurableEvent(event)
          // Use live cursor value, not captured
          const currentCursor = cursors.value.get(conversationId) ?? -1
          if (event.sequence > currentCursor) {
            cursors.value.set(conversationId, event.sequence)
          }
          // Reload messages when run completes
          if (
            event.type === 'run.succeeded' ||
            event.type === 'run.failed' ||
            event.type === 'run_cancelled'
          ) {
            api
              .get(`/conversations/${conversationId}/messages`)
              .then((res) => {
                messages.value = res.data as unknown as ChatMessage[]
              })
              .catch((e) => console.error('Failed to reload messages', e))
          }
        }).catch((e) => console.warn('Event subscription failed', e))
      }
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } }
      error.value =
        axiosErr.response?.data?.detail ?? 'Failed to load conversation'
    }
  }

  function processDurableEvent(event: DurableEvent): void {
    // Deduplicate by event ID
    if (appliedEventIds.value.has(event.id)) {
      return
    }
    appliedEventIds.value.add(event.id)

    const convId = event.conversation_id
    // If no current conversation set, still process for run cards
    // but only add messages if the event is for the current conversation
    const isCurrent = currentConversation.value?.id === convId

    switch (event.type) {
      case 'turn_submitted': {
        // User message was submitted
        const payload = event.payload
        let content = (payload.content as string) || ''
        const clientId = payload.client_request_id as string | undefined

        // If content not in event, look up pending request by client_request_id
        if (!content && clientId) {
          const pending = pendingRequests.value.get(clientId)
          if (pending) {
            content = pending.content
          }
        }

        // Try to find the optimistic temp message and update it
        if (clientId) {
          const tempMsg = messages.value.find(
            (m) => m.role === 'user' && m.id === `temp-${clientId}`,
          )
          if (tempMsg) {
            // Replace temp ID with server-derived ID
            tempMsg.id = `user-${event.id}`
            // Use server's created_at if available
            if (event.created_at) {
              tempMsg.created_at = event.created_at
            }
          } else {
            // No temp message (reconnect scenario) — create user message
            const existing = messages.value.find(
              (m) => m.role === 'user' && m.content === content,
            )
            if (!existing && content) {
              const userMsg: ChatMessage = {
                id: `user-${event.id}`,
                role: 'user',
                content,
                conversation_id: convId,
                created_at: event.created_at,
              }
              if (isCurrent) {
                messages.value.push(userMsg)
              }
            }
          }
        } else if (isCurrent && content) {
          // No client_request_id — check for duplicates
          const existing = messages.value.find(
            (m) => m.role === 'user' && m.content === content,
          )
          if (!existing) {
            const userMsg: ChatMessage = {
              id: `user-${event.id}`,
              role: 'user',
              content,
              conversation_id: convId,
              created_at: event.created_at,
            }
            messages.value.push(userMsg)
          }
        }

        // Create or update run card (for all conversations, not just current)
        if (event.run_id) {
          const card = runCards.value.get(convId)
          if (card) {
            card.run_id = event.run_id
            runCards.value.set(convId, card)
          } else {
            const runCard: RunCard = {
              run_id: event.run_id,
              status: 'queued',
              started_at: null,
              ended_at: null,
              usage: null,
              error_code: null,
            }
            runCards.value.set(convId, runCard)
          }
        }
        break
      }

      case 'run.queued': {
        updateRunStatus(convId, event.run_id || '', 'queued', event.created_at)
        break
      }

      case 'run.started': {
        updateRunStatus(convId, event.run_id || '', 'running', event.created_at)
        break
      }

      case 'run.paused': {
        updateRunStatus(convId, event.run_id || '', 'paused', event.created_at)
        break
      }

      case 'run.succeeded': {
        updateRunStatus(convId, event.run_id || '', 'succeeded', event.created_at)
        // The assistant message should be loaded from the messages endpoint
        // after the run completes. For now, just update the run card.
        break
      }

      case 'run.failed': {
        updateRunStatus(convId, event.run_id || '', 'failed', event.created_at)
        break
      }

      case 'run_cancelled': {
        updateRunStatus(convId, event.run_id || '', 'cancelled', event.created_at)
        break
      }

      case 'title_updated': {
        const payload = event.payload
        const title = (payload.title as string) || ''
        const payloadConvId = payload.conversation_id as string

        // Update conversation title in conversations list
        const convIndex = conversations.value.findIndex(
          (c) => c.id === payloadConvId,
        )
        if (convIndex >= 0) {
          conversations.value[convIndex].title = title
        }

        // Update current conversation title
        if (currentConversation.value?.id === payloadConvId) {
          currentConversation.value.title = title
        }
        break
      }
    }
  }

  function updateRunStatus(
    convId: string,
    runId: string,
    status: string,
    timestamp?: string,
  ): void {
    const card = runCards.value.get(convId)
    if (card) {
      card.run_id = runId
      card.status = status
      const ts = timestamp ?? new Date().toISOString()
      if (['succeeded', 'failed', 'cancelled'].includes(status)) {
        card.ended_at = ts
      } else if (status === 'running') {
        card.started_at = ts
      }
      runCards.value.set(convId, card)
    }
  }

  async function subscribeToEvents(
    conversationId: string,
    afterSequence: number,
    onEvent: (event: DurableEvent) => void,
  ): Promise<void> {
    const token = localStorage.getItem('access_token')
    const url = `/api/conversations/${conversationId}/events/stream?after_sequence=${afterSequence}`
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
      },
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: Failed to subscribe to events`)
    }

    if (!response.body) {
      throw new Error('No response body received')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let lastSeenSequence = afterSequence
    let sawTerminalEvent = false

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        // Stream closed without terminal event — refetch snapshot to recover
        if (!sawTerminalEvent) {
          console.warn(
            `SSE stream for ${conversationId} closed without terminal event at sequence ${lastSeenSequence}. Refetching snapshot.`,
          )
          try {
            const snapRes = await runsApi.getSnapshot(conversationId)
            const snapshot = snapRes.data as unknown as Snapshot
            // Process new events from snapshot that we missed
            for (const event of snapshot.events) {
              if (event.sequence > lastSeenSequence) {
                onEvent(event)
                lastSeenSequence = event.sequence
                if (
                  event.type === 'run.succeeded' ||
                  event.type === 'run.failed' ||
                  event.type === 'run_cancelled'
                ) {
                  sawTerminalEvent = true
                }
              }
            }
            cursors.value.set(conversationId, snapshot.sequence)
          } catch (err) {
            console.warn('Failed to refetch snapshot after stream close', err)
          }
        }
        break
      }

      buffer += decoder.decode(value, { stream: true })

      // Process complete SSE frames
      const frames = buffer.split('\n\n')
      buffer = frames.pop() ?? ''

      for (const frame of frames) {
        if (!frame.trim()) continue

        // Parse SSE fields
        let eventId = ''
        let eventType = ''
        let dataStr = ''

        for (const line of frame.split('\n')) {
          if (line.startsWith('id: ')) {
            eventId = line.slice(4)
          } else if (line.startsWith('event: ')) {
            eventType = line.slice(7)
          } else if (line.startsWith('data: ')) {
            dataStr = line.slice(6)
          }
        }

        if (!dataStr) continue

        try {
          const data = JSON.parse(dataStr)
          const sequence = data.sequence || 0

          // Emit the parsed event
          onEvent({
            id: eventId || data.id || '',
            conversation_id: conversationId,
            run_id: data.run_id || null,
            sequence,
            type: eventType || data.type || '',
            payload: data.payload || data,
            created_at: new Date().toISOString(),
          })

          if (sequence > lastSeenSequence) {
            lastSeenSequence = sequence
          }
          if (
            eventType === 'run.succeeded' ||
            eventType === 'run.failed' ||
            eventType === 'run_cancelled'
          ) {
            sawTerminalEvent = true
          }
        } catch {
          // Skip malformed events
        }
      }
      // Break out of the outer loop after processing terminal event
      if (sawTerminalEvent) {
        reader.releaseLock()
        break
      }
    }
  }

  async function sendMessage(
    content: string,
    conversationId: string | null = null,
  ) {
    error.value = null
    isStreaming.value = true

    try {
      // Generate stable client request UUID
      const requestId = generateRequestId()

      // Store pending request (local draft)
      pendingRequests.value.set(requestId, {
        conversation_id: conversationId || '',
        content,
      })

      // Create optimistic user message for immediate UI feedback
      const userMsg: ChatMessage = {
        id: `temp-${requestId}`,
        role: 'user',
        content,
        conversation_id: conversationId,
        created_at: new Date().toISOString(),
      }
      messages.value.push(userMsg)

      // Create run card for this conversation
      if (conversationId) {
        const runCard: RunCard = {
          run_id: '',
          status: 'queued',
          started_at: null,
          ended_at: null,
          usage: null,
          error_code: null,
        }
        runCards.value.set(conversationId, runCard)
      }

      // Submit durable command
      // If no conversation ID, create a new conversation first
      let effectiveConvId = conversationId
      if (!effectiveConvId) {
        const newConv = await createConversation('New Conversation')
        if (!newConv) {
          error.value = 'Failed to create conversation.'
          isStreaming.value = false
          return
        }
        effectiveConvId = newConv.id
        // Update current conversation
        currentConversation.value = newConv
      }

      let runId = ''
      try {
        const runRes = await runsApi.submitTurn(effectiveConvId, {
          content,
          client_request_id: requestId,
        })
        runId = runRes.data.id
        console.log('STORE: durable run submitted, runId:', runId)
        // Update run card with actual run ID
        const card = runCards.value.get(effectiveConvId)
        if (card) {
          card.run_id = runId
          runCards.value.set(effectiveConvId, card)
        }
      } catch (err) {
        console.error('STORE: Failed to submit durable run', err)
        error.value = 'Failed to submit message. Please try again.'
        isStreaming.value = false
        return
      }

      // Subscribe to events and process stream
      const convId = effectiveConvId
      const cursor = cursors.value.get(convId) ?? -1

      if (runId) {
        // Use durable event stream
        try {
          await subscribeToEvents(convId, cursor, (event) => {
            processDurableEvent(event)
            // Update cursor (use live value, not captured)
            const currentCursor = cursors.value.get(convId) ?? -1
            if (event.sequence > currentCursor) {
              cursors.value.set(convId, event.sequence)
            }

            // Check for run completion
            if (
              event.type === 'run.succeeded' ||
              event.type === 'run.failed' ||
              event.type === 'run_cancelled'
            ) {
              // Reload messages to get the assistant response
              if (convId) {
                api
                  .get(`/conversations/${convId}/messages`)
                  .then((res) => {
                    messages.value = res.data as unknown as ChatMessage[]
                  })
                  .catch((e) => console.error('Failed to reload messages', e))
              }
            }
          })
        } catch (err) {
          console.error('Event stream error', err)
        }
      }

      // Clear pending request
      pendingRequests.value.delete(requestId)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      error.value = `Failed to send message: ${message}`
    } finally {
      isStreaming.value = false
    }
  }

  async function cancelRun(runId: string): Promise<void> {
    try {
      await runsApi.cancelRun(runId)
      // Wait for durable state update by polling the run status
      let attempts = 0
      const maxAttempts = 10
      while (attempts < maxAttempts) {
        try {
          const res = await runsApi.getRun(runId)
          const status = res.data.status
          if (['cancelled', 'failed', 'completed'].includes(status)) {
            // Update the run card to reflect the durable state
            const card = [...runCards.value.entries()].find(
              ([, c]) => c.run_id === runId,
            )
            if (card) {
              card[1].status = status
              card[1].ended_at = new Date().toISOString()
              runCards.value.set(card[0], card[1])
            }
            break
          }
        } catch (err) {
          console.warn('Failed to fetch run status after cancel', err)
        }
        attempts++
        // Wait 500ms between polls
        await new Promise((resolve) => setTimeout(resolve, 500))
      }
    } catch (err) {
      console.error('Failed to cancel run', err)
    }
  }

  function appendMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function clearMessages() {
    messages.value = []
    currentConversation.value = null
    runCards.value.clear()
    cursors.value.clear()
  }

  return {
    conversations,
    currentConversation,
    messages,
    isStreaming,
    error,
    runCards,
    cursors,
    getConversations,
    createConversation,
    loadConversation,
    sendMessage,
    cancelRun,
    subscribeToEvents,
    processDurableEvent,
    appendMessage,
    clearMessages,
  }
})
