<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ChatMessage } from '../stores/useAgentStore'

const props = defineProps<{
  messages: ChatMessage[]
}>()

const scrollEl = ref<HTMLElement | null>(null)

watch(() => props.messages.length, () => {
  setTimeout(() => {
    if (scrollEl.value) {
      scrollEl.value.scrollTop = scrollEl.value.scrollHeight
    }
  }, 50)
})

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit'
  })
}
</script>

<template>
  <div class="chat-stream" ref="scrollEl">
    <div v-if="messages.length === 0" class="empty-chat">
      <p>Start a conversation with the agent...</p>
    </div>
    
    <template v-for="msg in messages" :key="msg.id">
      <!-- User Message -->
      <div v-if="msg.role === 'user'" class="message message-user">
        <div class="message-avatar user-avatar">U</div>
        <div class="message-content">
          <div class="message-text">{{ msg.content }}</div>
          <div class="message-meta">{{ formatTime(msg.timestamp) }}</div>
        </div>
      </div>
      
      <!-- Agent Message -->
      <div v-else-if="msg.role === 'agent'" class="message message-agent">
        <div class="message-avatar agent-avatar">A</div>
        <div class="message-content">
          <div class="message-text">{{ msg.content }}</div>
          
          <!-- Tool calls in message -->
          <template v-if="msg.tool_calls && msg.tool_calls.length > 0">
            <div class="tool-calls">
              <div
                v-for="tool in msg.tool_calls"
                :key="tool.id"
                class="tool-call"
              >
                <div class="tool-call-header">
                  <span class="tool-call-name">{{ tool.name }}</span>
                  <span class="tool-call-status" :class="`status-${tool.status}`">
                    {{ tool.status }}
                  </span>
                </div>
                <pre class="tool-call-args">{{ JSON.stringify(tool.args, null, 2) }}</pre>
                <div v-if="tool.result !== undefined" class="tool-call-result">
                  <pre>{{ JSON.stringify(tool.result, null, 2) }}</pre>
                </div>
              </div>
            </div>
          </template>
          
          <div class="message-meta">{{ formatTime(msg.timestamp) }}</div>
        </div>
      </div>
      
      <!-- System Message -->
      <div v-else class="message message-system">
        <div class="message-content system-content">
          <span class="system-icon">⚙</span>
          <span class="message-text">{{ msg.content }}</span>
          <span class="message-meta">{{ formatTime(msg.timestamp) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style>
.chat-stream {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.message {
  display: flex;
  gap: 0.75rem;
  max-width: 100%;
}

.message-user {
  flex-direction: row-reverse;
}

.message-avatar {
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-weight: 600;
  flex-shrink: 0;
}

.user-avatar {
  background: var(--primary);
  color: white;
}

.agent-avatar {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
}

.message-content {
  max-width: 70%;
  padding: 0.75rem;
  border-radius: 0.5rem;
  background: var(--surface);
  border: 1px solid var(--border);
}

.message-user .message-content {
  background: rgba(37, 99, 235, 0.05);
}

.message-text {
  font-size: 0.95rem;
  line-height: 1.5;
  white-space: pre-wrap;
}

.message-meta {
  font-size: 0.7rem;
  color: var(--text-muted);
  margin-top: 0.375rem;
}

.message-system {
  justify-content: center;
}

.system-content {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.75rem;
  background: var(--bg);
  border-radius: 999px;
  font-size: 0.8rem;
  color: var(--text-muted);
  max-width: none;
  border: none;
}

.system-icon {
  font-size: 0.75rem;
}

.tool-calls {
  margin-top: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.tool-call {
  border: 1px solid var(--border);
  border-radius: 0.375rem;
  overflow: hidden;
}

.tool-call-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.375rem 0.5rem;
  background: var(--bg);
  font-size: 0.8rem;
}

.tool-call-name {
  font-weight: 500;
}

.tool-call-status {
  font-size: 0.7rem;
  padding: 0.125rem 0.375rem;
  border-radius: 999px;
}

.status-running {
  background: rgba(37, 99, 235, 0.1);
  color: var(--primary);
}

.status-success {
  background: rgba(16, 185, 129, 0.1);
  color: var(--success);
}

.status-error {
  background: rgba(239, 68, 68, 0.1);
  color: var(--error);
}

.tool-call-args,
.tool-call-result {
  margin: 0;
  padding: 0.5rem;
  font-size: 0.75rem;
  overflow-x: auto;
  white-space: pre-wrap;
  background: var(--bg);
  border-top: 1px solid var(--border);
}

.empty-chat {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}
</style>