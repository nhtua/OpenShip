<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useAgentStore } from '../stores/useAgentStore'
import ChatStream from '../components/ChatStream.vue'
import ChatInput from '../components/ChatInput.vue'
import ApprovalCard from '../components/ApprovalCard.vue'

const route = useRoute()
const store = useAgentStore()
const pollingInterval = ref<number | null>(null)

onMounted(async () => {
  const executionId = route.params.executionId as string
  
  if (executionId) {
    // Load existing execution
    try {
      await store.getExecutionStatus(executionId)
      const execution = store.executions.find(
        (e: any) => e.id === executionId
      )
      if (execution) {
        store.currentExecution = execution
      }
      
      // Start polling for status updates
      startPolling()
    } catch (e) {
      console.error('Failed to load execution:', e)
    }
  }
})

onUnmounted(() => {
  stopPolling()
})

function startPolling() {
  if (pollingInterval.value) return
  
  pollingInterval.value = window.setInterval(async () => {
    const current = store.currentExecution
    if (current && ['running', 'pending'].includes(current.status)) {
      try {
        await store.getExecutionStatus(current.id)
      } catch (e) {
        console.error('Polling error:', e)
      }
    }
  }, 2000)
}

function stopPolling() {
  if (pollingInterval.value) {
    clearInterval(pollingInterval.value)
    pollingInterval.value = null
  }
}

function handleSendMessage(content: string) {
  store.sendMessage(content)
}

async function handleBuild(description: string) {
  await store.buildWorkflow(description)
}

function handleResume() {
  const current = store.currentExecution
  if (current) {
    store.resumeWorkflow(current.id)
  }
}

function handleApprove() {
  handleResume()
}

function handleReject() {
  const current = store.currentExecution
  if (current) {
    store.resumeWorkflow(current.id, { approved: false })
  }
}
</script>

<template>
  <div class="agent-workspace">
    <!-- Header -->
    <div class="workspace-header">
      <div class="execution-info">
        <h2 class="execution-title">
          {{ store.currentExecution ? store.currentExecution.workflow_id : 'Agent Workspace' }}
        </h2>
        <span v-if="store.currentExecution" class="execution-status" :class="`status-${store.currentExecution.status}`">
          {{ store.currentExecution.status }}
        </span>
      </div>
      <ApprovalCard
        v-if="store.isPaused"
        title="Awaiting Approval"
        description="The workflow is paused and waiting for your approval to continue."
        @approve="handleApprove"
        @reject="handleReject"
      />
    </div>

    <!-- Chat Stream -->
    <ChatStream :messages="store.messages" />

    <!-- Chat Input -->
    <div class="workspace-input">
      <div class="input-row">
        <ChatInput
          :loading="store.loading"
          :disabled="store.isRunning"
          placeholder="Interact with the agent..."
          @send="handleSendMessage"
        />
        <button
          class="btn btn-secondary"
          @click="handleBuild('Build a workflow')"
        >
          Build Workflow
        </button>
      </div>
    </div>
  </div>
</template>

<style>
.agent-workspace {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px);
  gap: 0.5rem;
}

.workspace-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
}

.execution-info {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.execution-title {
  font-size: 1rem;
  font-weight: 600;
}

.execution-status {
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
  border-radius: 999px;
  font-weight: 500;
  text-transform: uppercase;
}

.status-running {
  background: rgba(37, 99, 235, 0.1);
  color: var(--primary);
}

.status-completed {
  background: rgba(16, 185, 129, 0.1);
  color: var(--success);
}

.status-failed {
  background: rgba(239, 68, 68, 0.1);
  color: var(--error);
}

.status-paused {
  background: rgba(245, 158, 11, 0.1);
  color: var(--warning);
}

.status-pending {
  background: rgba(107, 114, 128, 0.1);
  color: var(--text-muted);
}

.btn {
  padding: 0.375rem 0.75rem;
  border: none;
  border-radius: 0.25rem;
  font-size: 0.875rem;
  cursor: pointer;
}

.btn-primary {
  background: var(--primary);
  color: white;
}

.btn-primary:hover {
  background: var(--primary-hover);
}

.workspace-input {
  border-top: 1px solid var(--border);
  padding: 1rem 0 0.5rem;
}

.input-row {
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
}

.input-row .chat-input {
  flex: 1;
}

.btn-secondary {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
}

.btn-secondary:hover {
  background: var(--surface);
}
</style>