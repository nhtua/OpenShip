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
          :processing-stage="processingStage"
        />
      </div>

      <div class="content-area">
        <div v-if="activeStage === 'requirements'" class="stage-panel">
          <h2>Requirements</h2>
          <MdEditor v-model="requirements" placeholder="Describe your infrastructure requirements..." />
          <div style="margin-top: 16px;">
            <button class="btn btn-primary" @click="startWorkflow">Generate</button>
          </div>
        </div>

        <div v-else-if="activeStage === 'generating_diagram'" class="stage-panel">
          <h2>Diagram</h2>
          <p v-if="!diagramSource">Waiting for diagram...</p>
          <div v-else class="diagram-container">
            <div v-if="showDiagramSource" class="diagram-source">
              <h3>Source</h3>
              <pre>{{ diagramSource }}</pre>
            </div>
            <div class="diagram-preview">
              <h3>Preview</h3>
              <div ref="mermaidPreview" class="mermaid-preview"></div>
            </div>
          </div>
          <div v-if="diagramSource" style="margin-top: 16px; display: flex; gap: 8px;">
            <button class="btn" @click="showDiagramSource = !showDiagramSource">
              {{ showDiagramSource ? 'Hide Source' : 'Show Source' }}
            </button>
            <button class="btn btn-primary" @click="approveDiagram">Approve Diagram</button>
          </div>
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
import { MdEditor } from 'md-editor-v3'
import 'md-editor-v3/lib/style.css'
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
const processingStage = ref(null)
let runId = null

const requirements = ref(`# Web Application Infrastructure

## Requirements
- Web server load-balanced across 2 instances
- PostgreSQL database
- Redis cache
- All resources in us-east-1
- Tags: env=production, app=webapp

## Networking
- Private subnet for database
- Public subnet for web servers
- Security groups for each tier
`)
const diagramSource = ref('')
const showDiagramSource = ref(true)
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
  processingStage.value = 'generating_diagram'
  try {
    const response = await fetch('/api/workflows', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirements: requirements.value })
    })

    if (!response.ok) throw new Error('Failed to start workflow')

    const data = await response.json()
    runId = data.run_id
    diagramSource.value = data.diagram || ''
    processingStage.value = null
    completedStages.value.push('requirements')
    activeStage.value = 'generating_diagram'
  } catch (error) {
    console.error('Workflow error:', error)
    processingStage.value = null
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
    completedStages.value.push('applying_terraform')
    activeStage.value = 'done'
  }
}

function approveDiagram() {
  processingStage.value = 'generating_terraform'
  completedStages.value.push('generating_diagram')
  activeStage.value = 'generating_terraform'
  fetch('/api/workflows/' + runId + '/approve-diagram', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  }).then(res => res.json()).then(data => {
    processingStage.value = null
    if (data.terraform) {
      terraformSource.value = data.terraform
    }
  }).catch(err => {
    processingStage.value = null
    console.error('Approve diagram error:', err)
  })
}

function approveTerraform() {
  processingStage.value = 'applying_terraform'
  completedStages.value.push('generating_terraform')
  activeStage.value = 'applying_terraform'
  fetch('/api/workflows/' + runId + '/approve-terraform', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({})
  }).then(res => res.json()).then(data => {
    processingStage.value = null
    if (data.apply_output) {
      applyOutput.value = data.apply_output
    }
  }).catch(err => {
    processingStage.value = null
    console.error('Approve terraform error:', err)
  })
}

function reset() {
  activeStage.value = 'requirements'
  completedStages.value = []
  diagramSource.value = ''
  terraformSource.value = ''
  applyOutput.value = ''
  runId = null
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
.btn { padding: 10px 24px; border: none; border-radius: 6px; font-size: 1rem; cursor: pointer; background: #333; color: #e0e0e0; }
.btn-primary { background: #4af62c; color: #1a1a1a; font-weight: bold; }
.btn:hover { opacity: 0.8; }
pre { background: #1e1e1e; padding: 16px; border-radius: 8px; overflow-x: auto; font-size: 13px; line-height: 1.4; }
.diagram-container { display: flex; gap: 16px; min-height: 400px; }
.diagram-source { flex: 0 0 400px; display: flex; flex-direction: column; }
.diagram-source h3, .diagram-preview h3 { margin-bottom: 8px; color: #fff; }
.diagram-source pre { flex: 1; }
.diagram-preview { flex: 1; display: flex; flex-direction: column; }
.mermaid-preview { min-height: 300px; flex: 1; display: flex; align-items: center; justify-content: center; background: #1e1e1e; border-radius: 8px; padding: 16px; }
.output-container pre { white-space: pre-wrap; }
</style>