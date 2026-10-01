<script setup lang="ts">
import type { ToolCall } from '../stores/useAgentStore'

const props = defineProps<{
  toolCall: ToolCall
}>()
</script>

<template>
  <div class="tool-call-card">
    <div class="tool-call-header">
      <div class="tool-call-icon" :class="`status-${toolCall.status}`">
        <svg v-if="toolCall.status === 'success'" width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
          <path d="M6 9L3 6l1-1 2 2 3-3 1 1"/>
        </svg>
        <svg v-else-if="toolCall.status === 'error'" width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
          <path d="M3 3l8 8M11 3l-8 8"/>
        </svg>
        <svg v-else width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
          <circle cx="7" cy="7" r="2"/>
        </svg>
      </div>
      <h4 class="tool-call-name">{{ toolCall.name }}</h4>
      <span class="tool-call-status" :class="`status-${toolCall.status}`">
        {{ toolCall.status }}
      </span>
    </div>
    
    <div class="tool-call-body">
      <div class="tool-section">
        <span class="section-label">Arguments</span>
        <pre class="code-block">{{ JSON.stringify(toolCall.args, null, 2) }}</pre>
      </div>
      
      <div v-if="toolCall.result !== undefined" class="tool-section">
        <span class="section-label">Result</span>
        <pre class="code-block">{{ JSON.stringify(toolCall.result, null, 2) }}</pre>
      </div>
      
      <div v-if="toolCall.error" class="tool-section">
        <span class="section-label">Error</span>
        <pre class="code-block error-text">{{ toolCall.error }}</pre>
      </div>
    </div>
  </div>
</template>

<style>
.tool-call-card {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  overflow: hidden;
  margin-top: 0.5rem;
}

.tool-call-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
}

.tool-call-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 50%;
  font-size: 0.7rem;
}

.status-running .tool-call-icon {
  background: rgba(37, 99, 235, 0.1);
  color: var(--primary);
}

.status-success .tool-call-icon {
  background: rgba(16, 185, 129, 0.1);
  color: var(--success);
}

.status-error .tool-call-icon {
  background: rgba(239, 68, 68, 0.1);
  color: var(--error);
}

.tool-call-name {
  font-size: 0.85rem;
  font-weight: 500;
  flex: 1;
}

.tool-call-status {
  font-size: 0.7rem;
  padding: 0.125rem 0.375rem;
  border-radius: 999px;
  font-weight: 500;
  text-transform: uppercase;
}

.tool-call-body {
  padding: 0.5rem;
}

.tool-section {
  margin-bottom: 0.5rem;
}

.tool-section:last-child {
  margin-bottom: 0;
}

.section-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: 0.25rem;
  text-transform: uppercase;
}

.code-block {
  margin: 0;
  padding: 0.5rem;
  background: var(--bg);
  border-radius: 0.375rem;
  font-size: 0.75rem;
  overflow-x: auto;
  white-space: pre-wrap;
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
}

.error-text {
  color: var(--error);
}
</style>