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
  it('keeps appended follow-ups after history even when the browser clock is behind', () => {
    const history = [
      runMessage('old-user', 'user', 'old-run', '2026-11-12T12:00:00Z'),
      runMessage('old-answer', 'assistant', 'old-run', '2026-11-12T12:01:00Z'),
    ]
    const pending = message('temp-followup', '2026-11-12T11:02:00Z')
    const expected = ['old-user', 'old-answer', 'temp-followup']
    expect(buildConversationTimeline([...history, pending], []).map((entry) => entry.id)).toEqual(expected)

    const admitted = { ...pending, id: 'followup', run_id: 'new-run' }
    expect(buildConversationTimeline([...history, admitted], [
      { ...operation('new-tool', '2026-11-12T10:00:00Z'), run_id: 'new-run' },
    ]).map((entry) => entry.id)).toEqual(['old-user', 'old-answer', 'followup', 'new-tool'])
  })

  it('preserves message order with equal, missing and invalid timestamps', () => {
    const timeline = buildConversationTimeline([
      message('z-first', ''),
      message('z-second', '2026-11-12T12:00:00Z'),
      message('a-third', '2026-11-12T12:00:00Z'),
      message('pending-one', 'not-a-date'),
      message('pending-two', '2026-11-12T11:00:00Z'),
    ], [])
    expect(timeline.map((entry) => entry.id)).toEqual([
      'z-first', 'z-second', 'a-third', 'pending-one', 'pending-two',
    ])
  })

  it('orders messages and tool operations together without manufacturing transcript messages', () => {
    const messages = [message('user-1', '2026-10-20T12:00:00Z'), message('user-2', '2026-10-20T12:00:02Z')]
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

  it('preserves message order for equal timestamps and keeps undated operations at the end', () => {
    const timeline = buildConversationTimeline(
      [message('message-b', '2026-10-20T12:00:00Z'), message('message-a', '2026-10-20T12:00:00Z')],
      [operation('search-a', '2026-10-20T12:00:00Z'), operation('search-undated', '')],
    )

    expect(timeline.map((entry) => entry.id)).toEqual([
      'message-b',
      'message-a',
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

  it('keeps earlier tools before later clarification replies in the same run', () => {
    const timeline = buildConversationTimeline([
      runMessage('request', 'user', 'run-1', '2026-11-12T12:00:00Z'),
      runMessage('clarification', 'user', 'run-1', '2026-11-12T12:00:10Z'),
      runMessage('answer', 'assistant', 'run-1', '2026-11-12T12:00:20Z'),
    ], [
      { ...operation('after-reply', '2026-11-12T12:00:15Z'), run_id: 'run-1' },
      { ...operation('before-reply', '2026-11-12T12:00:05Z'), run_id: 'run-1' },
    ])
    expect(timeline.map((entry) => entry.id)).toEqual([
      'request', 'before-reply', 'clarification', 'after-reply', 'answer',
    ])
  })
})
