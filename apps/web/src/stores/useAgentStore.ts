import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export interface ChatMessage {
  id: string
  role: 'user' | 'agent' | 'system'
  content: string
  timestamp: number
  tool_calls?: ToolCall[]
}

export interface ToolCall {
  id: string
  name: string
  args: Record<string, any>
  result?: any
  status: 'running' | 'success' | 'error'
  error?: string
}

export interface Workflow {
  id: string
  name: string
  version: string
  description: string
  origin: string
  tags: string[]
}

export interface Execution {
  id: string
  workflow_id: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'paused'
  inputs: Record<string, any>
  result?: any
  created_at: number
}

export const useAgentStore = defineStore('agent', () => {
  // State
  const messages = ref<ChatMessage[]>([])
  const workflows = ref<Workflow[]>([])
  const executions = ref<Execution[]>([])
  const currentExecution = ref<Execution | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  // Computed
  const agentMessages = computed(() =>
    messages.value.filter(m => m.role === 'agent' || m.role === 'system')
  )

  const userMessages = computed(() =>
    messages.value.filter(m => m.role === 'user')
  )

  const isRunning = computed(() =>
    currentExecution.value?.status === 'running'
  )

  const isPaused = computed(() =>
    currentExecution.value?.status === 'paused'
  )

  // Actions
  async function sendMessage(content: string) {
    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content,
      timestamp: Date.now()
    }
    messages.value.push(userMsg)

    loading.value = true
    try {
      // Check if this starts a new workflow
      const availableWorkflows = await listWorkflows()
      console.log('Available workflows:', availableWorkflows)
      
      // Try exact match first, then substring match
      let match = availableWorkflows.find((wf: Workflow) =>
        wf.name.toLowerCase() === content.toLowerCase().trim()
      )
      
      if (!match) {
        match = availableWorkflows.find((wf: Workflow) =>
          wf.name.toLowerCase().includes(content.toLowerCase().trim())
        )
      }

      if (match) {
        console.log('Matched workflow:', match.name)
        const execution = await runWorkflow(match.name)
        currentExecution.value = execution
      } else {
        console.log('No workflow match found for:', content)
        // Add agent response placeholder
        const agentMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'agent',
          content: 'I can help with that. Would you like me to start a workflow?',
          timestamp: Date.now()
        }
        messages.value.push(agentMsg)
      }
    } catch (e) {
      console.error('Error in sendMessage:', e)
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  async function listWorkflows() {
    try {
      const response = await fetch('/api/workflows')
      const data = await response.json()
      workflows.value = data
      return data
    } catch (e) {
      error.value = 'Failed to list workflows'
      return []
    }
  }

  async function runWorkflow(name: string, inputs: Record<string, any> = {}) {
    loading.value = true
    try {
      const response = await fetch('/api/workflows/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, inputs })
      })
      const data = await response.json()
      
      const execution: Execution = {
        id: data.execution_id,
        workflow_id: name,
        status: data.status as Execution['status'],
        inputs,
        result: data.output,
        created_at: Date.now()
      }
      
      executions.value.push(execution)
      currentExecution.value = execution

      // Add system message about execution
      const sysMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'system',
        content: `Started workflow: ${name} (${execution.id})`,
        timestamp: Date.now()
      }
      messages.value.push(sysMsg)

      return execution
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function getExecutionStatus(executionId: string) {
    try {
      const response = await fetch(`/api/workflows/status/${executionId}`)
      const data = await response.json()
      
      const execution = executions.value.find(e => e.id === executionId)
      if (execution) {
        execution.status = data.status as Execution['status']
        execution.result = data.result
      }
      
      return data
    } catch (e) {
      error.value = 'Failed to get execution status'
      throw e
    }
  }

  async function resumeWorkflow(executionId: string, response: any = null) {
    loading.value = true
    try {
      const res = await fetch(`/api/workflows/resume/${executionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response })
      })
      const data = await res.json()
      return data
    } catch (e) {
      error.value = 'Failed to resume workflow'
      throw e
    } finally {
      loading.value = false
    }
  }

  function addMessage(msg: ChatMessage) {
    messages.value.push(msg)
  }

  function clearMessages() {
    messages.value = []
  }

  return {
    // State
    messages,
    workflows,
    executions,
    currentExecution,
    loading,
    error,
    // Computed
    agentMessages,
    userMessages,
    isRunning,
    isPaused,
    // Actions
    sendMessage,
    listWorkflows,
    runWorkflow,
    getExecutionStatus,
    resumeWorkflow,
    addMessage,
    clearMessages
  }
})