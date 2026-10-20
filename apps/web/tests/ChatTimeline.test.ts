import { describe, expect, it } from 'vitest'
import type { ChatMessage, ToolOperationSummary } from '@/types'
import { buildConversationTimeline } from '@/lib/chatTimeline'

function message(id: string, created_at: string): ChatMessage {
  return { id, role: 'user', content: id, conversation_id: 'c1', created_at }
}

function runMessage(
  id: string,
  role: ChatMessage['role'],
  run_id: string,
  created_at: string,
): ChatMessage {
  return { id, role, content: id, conversation_id: 'c1', run_id, created_at }
}

function operation(id: string, created_at: string): ToolOperationSummary {
  return {
    id,
    run_id: `run-${id}`,
    conversation_id: 'c1',
    logical_call_id: id,
    capability_id: 'brave_web_search',
    capability_version: '1.0.0',
    manifest_digest: 'a'.repeat(64),
    status: 'succeeded',
    error_code: null,
    error: null,
    result: { query: id, items: [] },
    artifacts: [],
    attempts: [],
    created_at,
    updated_at: created_at,
    finished_at: created_at,
  }
}

describe('buildConversationTimeline', () => {
  it('orders messages and tool operations together without manufacturing transcript messages', () => {
    const messages = [message('user-2', '2026-10-20T12:00:02Z'), message('user-1', '2026-10-20T12:00:00Z')]
    const operations = [operation('search-2', '2026-10-20T12:00:03Z'), operation('search-1', '2026-10-20T12:00:01Z')]

    const timeline = buildConversationTimeline(messages, operations)

    expect(timeline.map((entry) => `${entry.kind}:${entry.id}`)).toEqual([
      'message:user-1',
      'tool:search-1',
      'message:user-2',
      'tool:search-2',
    ])
    expect(messages).toHaveLength(2)
    expect(timeline.filter((entry) => entry.kind === 'message')).toHaveLength(2)
  })

  it('uses stable IDs for equal timestamps and keeps undated operations at the end', () => {
    const timeline = buildConversationTimeline(
      [message('message-b', '2026-10-20T12:00:00Z'), message('message-a', '2026-10-20T12:00:00Z')],
      [operation('search-a', '2026-10-20T12:00:00Z'), operation('search-undated', '')],
    )

    expect(timeline.map((entry) => entry.id)).toEqual([
      'message-a',
      'message-b',
      'search-a',
      'search-undated',
    ])
  })

  it('keeps each run’s tools between its user message and answer when operation times are unreliable', () => {
    const timeline = buildConversationTimeline(
      [
        runMessage('user-first', 'user', 'run-first', '2026-10-20T12:00:00Z'),
        runMessage('assistant-first', 'assistant', 'run-first', '2026-10-20T12:00:10Z'),
        runMessage('user-second', 'user', 'run-second', '2026-10-20T12:01:00Z'),
        runMessage('assistant-second', 'assistant', 'run-second', '2026-10-20T12:01:10Z'),
      ],
      [
        { ...operation('search-second', '2026-10-20T11:00:00Z'), run_id: 'run-second' },
        { ...operation('search-first', '2026-10-20T11:00:01Z'), run_id: 'run-first' },
      ],
    )

    expect(timeline.map((entry) => `${entry.kind}:${entry.id}`)).toEqual([
      'message:user-first',
      'tool:search-first',
      'message:assistant-first',
      'message:user-second',
      'tool:search-second',
      'message:assistant-second',
    ])
  })

  it('places tool operations after a run’s user message while its answer is still pending', () => {
    const timeline = buildConversationTimeline(
      [runMessage('user-live', 'user', 'run-live', '2026-10-20T12:00:00Z')],
      [{ ...operation('search-live', ''), run_id: 'run-live' }],
    )

    expect(timeline.map((entry) => `${entry.kind}:${entry.id}`)).toEqual([
      'message:user-live',
      'tool:search-live',
    ])
  })
})
