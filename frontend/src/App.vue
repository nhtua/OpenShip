<template>
  <div class="app">
    <header class="header">
      <h1>OpenShip</h1>
      <p>Agentic DevOps Workflow</p>
    </header>

    <div class="workflow-layout">
      <div class="timeline-sidebar">
        <WorkflowTimeline
          :stages="stages"
          :active-stage="activeStage"
          :completed-stages="completedStages"
        />
      </div>

      <div class="content-area">
        <div v-if="activeStage === 'requirements'" class="stage-panel">
          <h2>Requirements</h2>
          <textarea v-model="requirements" class="requirements-editor" rows="10"
                    placeholder="Describe your infrastructure requirements..."></textarea>
          <button class="btn btn-primary" @click="startWorkflow">Generate</button>
        </div>

        <div v-else-if="activeStage === 'generating_diagram'" class="stage-panel">
          <h2>Generating Diagram</h2>
          <p v-if="!diagramSource">Waiting for diagram...</p>
          <div v-else class="diagram-container">
            <div ref="mermaidPreview" class="mermaid-preview"></div>
          </div>
          <button v-if="diagramSource" class="btn btn-primary" @click="approveDiagram">Approve Diagram</button>
        </div>

        <div v-else-if="activeStage === 'generating_terraform'" class="stage-panel">
          <h2>Generating Terraform</h2>
          <p v-if="!terraformSource">Waiting for Terraform code...</p>
          <div v-else class="terraform-container">
            <pre>{{ terraformSource }}</pre>
          </div>
          <button v-if="terraformSource" class="btn btn-primary" @click="approveTerraform">Approve Terraform</button>
        </div>

        <div v-else-if="activeStage === 'applying_terraform'" class="stage-panel">
          <h2>Applying Terraform</h2>
          <p v-if="!applyOutput">Applying...</p>
          <div v-else class="output-container">
            <pre>{{ applyOutput }}</pre>
          </div>
        </div>

        <div v-else-if="activeStage === 'done'" class="stage-panel">
          <h2>Complete</h2>
          <p>Workflow completed successfully!</p>
          <button class="btn btn-primary" @click="reset">Start New</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import WorkflowTimeline from './components/WorkflowTimeline.vue'
import mermaid from 'mermaid'

const stages = [
  { id: 'requirements', label: 'Requirements' },
  { id: 'generating_diagram', label: 'Diagram' },
  { id: 'generating_terraform', label: 'Terraform' },
  { id: 'applying_terraform', label: 'Apply' },
  { id: 'done', label: 'Complete' }
]

const activeStage = ref('requirements')
const completedStages = ref([])
const requirements = ref('')
const diagramSource = ref('')
const terraformSource = ref('')
const applyOutput = ref('')

onMounted(() => {
  mermaid.initialize({ theme: 'dark' })
})

watch(diagramSource, async () => {
  if (diagramSource.value) {
    await nextTick()
    const preview = document.querySelector('.mermaid-preview')
    if (preview) {
      preview.innerHTML = ''
      try {
        const { svg } = await mermaid.render('diagram', diagramSource.value)
        preview.innerHTML = svg
      } catch (e) {
        preview.innerHTML = '<div class="error">' + e.message + '</div>'
      }
    }
  }
})

async function startWorkflow() {
  try {
    const response = await fetch('/api/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirements: requirements.value })
    })

    if (!response.ok) throw new Error('Failed to start workflow')

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent = null
    let currentData = []

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value)
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7)
        } else if (line.startsWith('data: ')) {
          currentData.push(line.slice(6))
        } else if (line === '' && currentEvent) {
          const data = JSON.parse(currentData.join(''))
          handleSSEEvent(currentEvent, data)
          currentEvent = null
          currentData = []
        }
      }
    }
  } catch (error) {
    console.error('Workflow error:', error)
  }
}

function handleSSEEvent(eventType, data) {
  console.log('SSE Event:', eventType, data)

  if (eventType === 'stage_start') {
    activeStage.value = data.stage
  } else if (eventType === 'stage_complete') {
    completedStages.value.push(data.stage)
    if (data.stage === 'generating_diagram') {
      diagramSource.value = data.data?.diagram || ''
    } else if (data.stage === 'generating_terraform') {
      terraformSource.value = data.data?.terraform || ''
    } else if (data.stage === 'applying_terraform') {
      applyOutput.value = data.data?.output || ''
    }
  } else if (eventType === 'complete') {
    activeStage.value = 'done'
  }
}

function approveDiagram() {
  // Continue to next stage
}

function approveTerraform() {
  // Continue to next stage
}

function reset() {
  activeStage.value = 'requirements'
  completedStages.value = []
  diagramSource.value = ''
  terraformSource.value = ''
  applyOutput.value = ''
}
</script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #1a1a1a; color: #e0e0e0; }
.app { min-height: 100vh; padding: 20px; }
.header { text-align: center; margin-bottom: 30px; }
.header h1 { font-size: 2rem; color: #4af62c; }
.header p { color: #888; margin-top: 5px; }
.workflow-layout { display: flex; max-width: 1200px; margin: 0 auto; gap: 24px; }
.timeline-sidebar { width: 200px; flex-shrink: 0; }
.content-area { flex: 1; }
.stage-panel { background: #252525; border-radius: 12px; padding: 24px; }
.stage-panel h2 { margin-bottom: 16px; color: #fff; }
.btn { padding: 10px 24px; border: none; border-radius: 6px; font-size: 1rem; cursor: pointer; }
.btn-primary { background: #4af62c; color: #1a1a1a; font-weight: bold; }
.requirements-editor { width: 100%; padding: 12px; background: #1e1e1e; border: 1px solid #333; border-radius: 8px; color: #e0e0e0; font-family: monospace; resize: vertical; }
pre { background: #1e1e1e; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px; line-height: 1.4; }
.mermaid-preview { min-height: 300px; display: flex; align-items: center; justify-content: center; background: #1e1e1e; border-radius: 8px; padding: 16px; }
</style>