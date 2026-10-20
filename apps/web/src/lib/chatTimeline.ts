import type { ChatMessage, ToolOperationSummary } from '@/types'

export type ConversationEntry =
  | { kind: 'message'; id: string; createdAt: string | null; message: ChatMessage }
  | { kind: 'tool'; id: string; createdAt: string | null; operation: ToolOperationSummary }

/** Combine persisted messages and operations for presentation only. */
export function buildConversationTimeline(
  messages: ChatMessage[],
  operations: ToolOperationSummary[],
): ConversationEntry[] {
  const entries: ConversationEntry[] = [
    ...messages.map((message) => ({
      kind: 'message' as const,
      id: message.id,
      createdAt: message.created_at || null,
      message,
    })),
    ...operations.map((operation) => ({
      kind: 'tool' as const,
      id: operation.id,
      createdAt: operation.created_at || null,
      operation,
    })),
  ]

  return entries.sort((left, right) => {
    const leftTime = left.createdAt ? Date.parse(left.createdAt) : Number.NaN
    const rightTime = right.createdAt ? Date.parse(right.createdAt) : Number.NaN
    const leftKnown = Number.isFinite(leftTime)
    const rightKnown = Number.isFinite(rightTime)
    if (leftKnown && rightKnown && leftTime !== rightTime) return leftTime - rightTime
    if (leftKnown !== rightKnown) return leftKnown ? -1 : 1
    return left.id.localeCompare(right.id)
  })
}
