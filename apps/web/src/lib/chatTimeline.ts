import type { ChatMessage, ToolOperationSummary } from '@/types'

export type ConversationEntry =
  | { kind: 'message'; id: string; runId: string | null; createdAt: string | null; message: ChatMessage }
  | { kind: 'tool'; id: string; runId: string | null; createdAt: string | null; operation: ToolOperationSummary }

/** Combine persisted messages and operations for presentation only. */
export function buildConversationTimeline(
  messages: ChatMessage[],
  operations: ToolOperationSummary[],
): ConversationEntry[] {
  const entries: ConversationEntry[] = [
    ...messages.map((message) => ({
      kind: 'message' as const,
      id: message.id,
      runId: message.run_id ?? null,
      createdAt: message.created_at || null,
      message,
    })),
    ...operations.map((operation) => ({
      kind: 'tool' as const,
      id: operation.id,
      runId: operation.run_id || null,
      createdAt: operation.created_at || null,
      operation,
    })),
  ]

  const sortTimes = new Map<ConversationEntry, number>()
  for (const entry of entries) {
    const time = entry.createdAt ? Date.parse(entry.createdAt) : Number.NaN
    sortTimes.set(entry, Number.isFinite(time) ? time : Number.NaN)
  }

  const byRun = new Map<string, ConversationEntry[]>()
  for (const entry of entries) {
    if (!entry.runId) continue
    const group = byRun.get(entry.runId) ?? []
    group.push(entry)
    byRun.set(entry.runId, group)
  }

  for (const group of byRun.values()) {
    const users = group.filter((entry) => entry.kind === 'message' && entry.message.role === 'user')
    const assistants = group.filter((entry) => entry.kind === 'message' && entry.message.role === 'assistant')
    const tools = group
      .filter((entry) => entry.kind === 'tool')
      .sort((left, right) => compareKnownTime(sortTimes.get(left)!, sortTimes.get(right)!) || left.id.localeCompare(right.id))
    if (!users.length || !tools.length) continue

    const firstUserTime = Math.min(...users.map((entry) => sortTimes.get(entry)!))
    const lastAssistantTime = assistants.length
      ? Math.max(...assistants.map((entry) => sortTimes.get(entry)!))
      : Number.NaN
    if (!Number.isFinite(firstUserTime)) continue

    // Operation timestamps can be written on a separate worker clock. When
    // the run has both ends, spread tools between its user prompt and answer;
    // for a live run, let same-run tie-breaking put them just after the prompt.
    if (Number.isFinite(lastAssistantTime) && lastAssistantTime > firstUserTime) {
      tools.forEach((entry, index) => {
        sortTimes.set(entry, firstUserTime + ((lastAssistantTime - firstUserTime) * (index + 1)) / (tools.length + 1))
      })
    } else {
      tools.forEach((entry) => sortTimes.set(entry, firstUserTime))
    }
  }

  return entries.sort((left, right) => {
    const leftTime = sortTimes.get(left)!
    const rightTime = sortTimes.get(right)!
    const leftKnown = Number.isFinite(leftTime)
    const rightKnown = Number.isFinite(rightTime)
    if (leftKnown && rightKnown && leftTime !== rightTime) return leftTime - rightTime
    if (leftKnown !== rightKnown) return leftKnown ? -1 : 1

    if (left.runId && left.runId === right.runId) {
      const roleOrder = (entry: ConversationEntry) => {
        if (entry.kind === 'tool') return 1
        return entry.message.role === 'user' ? 0 : 2
      }
      const roleDifference = roleOrder(left) - roleOrder(right)
      if (roleDifference) return roleDifference
    }
    return left.id.localeCompare(right.id)
  })
}

function compareKnownTime(left: number, right: number): number {
  const leftKnown = Number.isFinite(left)
  const rightKnown = Number.isFinite(right)
  if (leftKnown && rightKnown && left !== right) return left - right
  if (leftKnown !== rightKnown) return leftKnown ? -1 : 1
  return 0
}
