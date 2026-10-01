<script setup lang="ts">
const props = defineProps<{
  title: string
  description?: string
  toolCall?: {
    name: string
    args: Record<string, any>
  }
}>()

const emit = defineEmits<{
  approve: []
  reject: []
}>()
</script>

<template>
  <div class="approval-card">
    <div class="approval-header">
      <div class="approval-icon">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
          <path d="M10 2L3 7v11h14V7l-7-5zm0 2l4 3H6l4-3z"/>
        </svg>
      </div>
      <div class="approval-info">
        <h4 class="approval-title">{{ title }}</h4>
        <p v-if="description" class="approval-description">{{ description }}</p>
      </div>
    </div>
    
    <div v-if="toolCall" class="approval-tool">
      <h5>Tool: {{ toolCall.name }}</h5>
      <pre class="approval-args">{{ JSON.stringify(toolCall.args, null, 2) }}</pre>
    </div>
    
    <div class="approval-actions">
      <button class="btn btn-approve" @click="emit('approve')">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
          <path d="M6 9L3 6l1-1 2 2 3-3 1 1"/>
        </svg>
        Approve
      </button>
      <button class="btn btn-reject" @click="emit('reject')">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor">
          <path d="M3 3l8 8M11 3l-8 8"/>
        </svg>
        Reject
      </button>
    </div>
  </div>
</template>

<style>
.approval-card {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  overflow: hidden;
  margin-top: 0.5rem;
  background: var(--surface);
}

.approval-header {
  display: flex;
  gap: 0.75rem;
  padding: 0.75rem;
  border-bottom: 1px solid var(--border);
}

.approval-icon {
  color: var(--warning);
  flex-shrink: 0;
}

.approval-info {
  flex: 1;
}

.approval-title {
  font-size: 0.95rem;
  font-weight: 500;
  margin-bottom: 0.25rem;
}

.approval-description {
  font-size: 0.85rem;
  color: var(--text-muted);
  margin: 0;
}

.approval-tool {
  padding: 0.75rem;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

.approval-tool h5 {
  font-size: 0.85rem;
  font-weight: 500;
  margin-bottom: 0.375rem;
}

.approval-args {
  margin: 0;
  padding: 0.5rem;
  background: var(--surface);
  border-radius: 0.375rem;
  font-size: 0.75rem;
  overflow-x: auto;
  white-space: pre-wrap;
}

.approval-actions {
  display: flex;
  gap: 0.5rem;
  padding: 0.75rem;
}

.btn {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.75rem;
  border: none;
  border-radius: 0.375rem;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.2s;
}

.btn-approve {
  background: rgba(16, 185, 129, 0.1);
  color: var(--success);
}

.btn-approve:hover {
  background: rgba(16, 185, 129, 0.2);
}

.btn-reject {
  background: rgba(239, 68, 68, 0.1);
  color: var(--error);
}

.btn-reject:hover {
  background: rgba(239, 68, 68, 0.2);
}
</style>