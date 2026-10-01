<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentStore } from '../stores/useAgentStore'
import ChatInput from '../components/ChatInput.vue'

const store = useAgentStore()
const router = useRouter()
const inputFocused = ref(false)

onMounted(async () => {
  try {
    await store.listWorkflows()
  } catch (e) {
    console.error('Failed to load workflows:', e)
  }
})

async function handleSendMessage(content: string) {
  await store.sendMessage(content)
  
  // If a workflow was started, navigate to the agent workspace
  if (store.currentExecution) {
    router.push({
      name: 'agent-workspace',
      params: { executionId: store.currentExecution.id }
    })
  }
}

async function handleWorkflowClick(workflow: any) {
  await store.runWorkflow(workflow.name)
  
  // Navigate to the agent workspace
  if (store.currentExecution) {
    router.push({
      name: 'agent-workspace',
      params: { executionId: store.currentExecution.id }
    })
  }
}
</script>

<template>
  <div class="home-view">
    <!-- Chat Input Section -->
    <div class="chat-section">
      <div class="chat-container">
        <ChatInput
          :loading="store.loading"
          placeholder="Describe what you want to build or automate..."
          @send="handleSendMessage"
        />
      </div>
    </div>

    <!-- Workflow Templates Section -->
    <div class="workflows-section">
      <h2 class="section-title">Available Workflows</h2>
      
      <div v-if="store.workflows.length === 0" class="empty-state">
        <p>No workflows available</p>
      </div>

      <div v-else class="workflows-grid">
        <button
          v-for="workflow in store.workflows"
          :key="workflow.id"
          class="workflow-card"
          @click="handleWorkflowClick(workflow)"
        >
          <div class="workflow-icon">
            <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
              <path d="M10 2L3 7v11h14V7l-7-5zm0 2l4 3H6l4-3z"/>
            </svg>
          </div>
          <div class="workflow-info">
            <h3 class="workflow-name">{{ workflow.name }}</h3>
            <p class="workflow-description">{{ workflow.description }}</p>
          </div>
          <div class="workflow-meta">
            <span class="workflow-version">v{{ workflow.version }}</span>
            <span class="workflow-origin" :class="`origin-${workflow.origin}`">
              {{ workflow.origin }}
            </span>
          </div>
        </button>
      </div>
    </div>
  </div>
</template>

<style>
.home-view {
  max-width: 800px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2rem;
}

.chat-section {
  text-align: center;
  padding: 2rem 0;
}

.chat-container {
  max-width: 600px;
  margin: 0 auto;
}

.chat-container .chat-input {
  max-width: none;
}

.workflows-section {
  padding: 1rem 0;
}

.section-title {
  font-size: 1rem;
  font-weight: 500;
  color: var(--text-muted);
  margin-bottom: 1rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.workflows-grid {
  display: grid;
  gap: 0.75rem;
}

.workflow-card {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  padding: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--surface);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
  text-align: left;
}

.workflow-card:hover {
  border-color: var(--primary);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
}

.workflow-icon {
  color: var(--primary);
  flex-shrink: 0;
  margin-top: 0.25rem;
}

.workflow-info {
  flex: 1;
  min-width: 0;
}

.workflow-name {
  font-size: 0.95rem;
  font-weight: 500;
  color: var(--text);
  margin-bottom: 0.125rem;
}

.workflow-description {
  font-size: 0.85rem;
  color: var(--text-muted);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.workflow-meta {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  text-align: right;
}

.workflow-version {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.workflow-origin {
  font-size: 0.65rem;
  padding: 0.125rem 0.375rem;
  border-radius: 999px;
  font-weight: 500;
}

.origin-builtin {
  background: rgba(37, 99, 235, 0.1);
  color: var(--primary);
}

.origin-custom {
  background: rgba(16, 185, 129, 0.1);
  color: var(--success);
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: var(--text-muted);
}
</style>