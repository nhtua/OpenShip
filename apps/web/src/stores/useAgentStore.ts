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
      // Use LLM to route the message
      const decision = await routeMessage(content)
      console.log('Routing decision:', decision)
      
      if (decision.action === 'run_workflow') {
        const execution = await runWorkflow(decision.workflow_name, decision.parameters || {})
        currentExecution.value = execution
        
        // Add system message about execution
        const sysMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'system',
          content: `Running workflow: ${decision.workflow_name}`,
          timestamp: Date.now()
        }
        messages.value.push(sysMsg)
      } else if (decision.action === 'build_workflow') {
        const result = await buildWorkflow(content, '')
        console.log('Build result:', result)
      } else {
        // Chat response
        const agentMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: 'agent',
          content: decision.reasoning || 'I can help with that. Would you like me to start a workflow?',
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

  async function routeMessage(message: string) {
    try {
      const response = await fetch('/api/agent/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          history: messages.value
            .filter((m: ChatMessage) => m.role !== 'system')
            .map((m: ChatMessage) => `${m.role}: ${m.content}`)
        })
      })
      return await response.json()
    } catch (e) {
      console.error('Error routing message:', e)
      // Fall back to text matching
      const availableWorkflows = await listWorkflows()
      const match = availableWorkflows.find((wf: Workflow) =>
        wf.name.toLowerCase().includes(message.toLowerCase().trim())
      )
      if (match) {
        return { action: 'run_workflow', workflow_name: match.name, parameters: {} }
      }
      return { action: 'chat', reasoning: 'I can help with that.' }
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

      // Add workflow output
      if (data.status === 'completed' || data.status === 'failed') {
        // Format step outputs
        let outputText = ''
        if (data.step_outputs) {
          const steps = Object.keys(data.step_outputs).sort()
          for (const step of steps) {
            const stepOutput = data.step_outputs[step]
            if (stepOutput.output) {
              outputText += `Step ${step}: ${stepOutput.output.trim()}\n`
            } else if (stepOutput.error) {
              outputText += `Step ${step} (error): ${stepOutput.error.trim()}\n`
            }
          }
        }
        
        if (outputText) {
          const resultMsg: ChatMessage = {
            id: crypto.randomUUID(),
            role: 'agent',
            content: `Workflow completed:\n\n\`\`\`\n${outputText.trim()}\n\`\`\``,
            timestamp: Date.now()
          }
          messages.value.push(resultMsg)
        }
      }

      return execution
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function buildWorkflow(description: string, feedback: string = '') {
    loading.value = true
    try {
      const response = await fetch('/api/workflows/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description, feedback })
      })
      const data = await response.json()
      
      // Add the plan as an agent message
      const agentMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'agent',
        content: `Here's the plan I generated:\n\n${data.plan}`,
        timestamp: Date.now()
      }
      messages.value.push(agentMsg)
      
      return data
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  async function compileWorkflow(description: string) {
    loading.value = true
    try {
      const response = await fetch('/api/workflows/compile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description })
      })
      const data = await response.json()
      return data
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
    routeMessage,
    listWorkflows,
    runWorkflow,
    getExecutionStatus,
    resumeWorkflow,
    buildWorkflow,
    compileWorkflow,
    addMessage,
    clearMessages
  }
})