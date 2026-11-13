import type { ChatMessage, ToolOperationSummary } from '@/types'

export type ConversationEntry =
  | { kind: 'message'; id: string; runId: string | null; createdAt: string | null; message: ChatMessage }
  | { kind: 'tool'; id: string; runId: string | null; createdAt: string | null; operation: ToolOperationSummary }

/** Preserve the store's message order, inserting operations for presentation only. */
export function buildConversationTimeline(
  messages: ChatMessage[],
  operations: ToolOperationSummary[],
): ConversationEntry[] {
  const messageEntries: ConversationEntry[] = messages.map((message) => ({
    kind: 'message',
    id: message.id,
    runId: message.run_id ?? null,
    createdAt: message.created_at || null,
    message,
  }))
  const messageTimes = messages.map((message) => Date.parse(message.created_at))
  const userAnchors = new Map<string, number>()
  const lastUserAnchors = new Map<string, number>()
  const answerAnchors = new Map<string, number>()
  messages.forEach((message, index) => {
    if (!message.run_id) return
    if (message.role === 'user') {
      if (!userAnchors.has(message.run_id)) userAnchors.set(message.run_id, index)
      lastUserAnchors.set(message.run_id, index)
    }
    if (message.role === 'assistant' && !answerAnchors.has(message.run_id)) {
      answerAnchors.set(message.run_id, index)
    }
  })

  // History is already ordered by the server, and local sends are appended by
  // the store. Never re-sort these messages against the browser clock: an
  // optimistic send (even after its ID is acknowledged) can otherwise jump
  // above history until completion replaces its timestamp with the server's.
  const toolsByPosition: ConversationEntry[][] = Array.from(
    { length: messages.length + 1 }, () => [],
  )
  const sortedOperations = [...operations].sort((left, right) => (
    compareKnownTime(Date.parse(left.created_at ?? ''), Date.parse(right.created_at ?? ''))
    || left.id.localeCompare(right.id)
  ))
  for (const operation of sortedOperations) {
    const userAnchor = userAnchors.get(operation.run_id)
    const answerAnchor = answerAnchors.get(operation.run_id)
    // Timestamps choose a gap (including between clarification replies), never
    // reorder messages. Run anchors keep skewed worker clocks inside the turn.
    const time = Date.parse(operation.created_at ?? '')
    const nextMessage = messageTimes.findIndex((messageTime) => messageTime > time)
    let position = nextMessage < 0 ? messages.length : nextMessage
    if (userAnchor !== undefined) {
      const end = answerAnchor ?? lastUserAnchors.get(operation.run_id)! + 1
      position = Math.max(userAnchor + 1, Math.min(position, end))
    } else if (answerAnchor !== undefined) {
      position = Math.min(position, answerAnchor)
    }
    toolsByPosition[position].push({
      kind: 'tool',
      id: operation.id,
      runId: operation.run_id || null,
      createdAt: operation.created_at || null,
      operation,
    })
  }

  const entries: ConversationEntry[] = []
  messageEntries.forEach((entry, index) => entries.push(...toolsByPosition[index], entry))
  entries.push(...toolsByPosition[messages.length])
  return entries
}

function compareKnownTime(left: number, right: number): number {
  const leftKnown = Number.isFinite(left)
  const rightKnown = Number.isFinite(right)
  if (leftKnown && rightKnown && left !== right) return left - right
  if (leftKnown !== rightKnown) return leftKnown ? -1 : 1
  return 0
}
