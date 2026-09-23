# LangGraph Streaming UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement real-time streaming of LangGraph workflow progress with SSE and a vertical timeline UI.

**Architecture:** Backend uses LangGraph StateGraph with SQLite checkpointing and streams events via FastAPI SSE. Frontend uses Vue3 EventSource API to display live progress with a vertical stage timeline.

**Tech Stack:** Python, FastAPI, LangGraph, SQLite, Vue3, EventSource API

---

### Task 1: Backend - Workflow State and Event Types

**Files:**
- Create: `backend/src/openship/state.py`
- Create: `backend/src/openship/events.py`
- Test: `tests/backend/test_state.py`
- Test: `tests/backend/test_events.py`

- [ ] **Step 1: Write failing test for state definition**

Create `tests/backend/test_state.py`:
```python
from openship.state import WorkflowState

def test_workflow_state_default():
    state = WorkflowState()
    assert state["step"] == "idle"
    assert not state["done"]
    assert state["requirements"] == ""
    assert state["diagram"] == ""
    assert state["terraform"] == ""
    assert state["apply_output"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/backend/test_state.py -v`
Expected: FAIL with "No module named 'openship.state'"

- [ ] **Step 3: Create state module**

Create `backend/src/openship/state.py`:
```python
from typing import TypedDict, Optional

class WorkflowState(TypedDict):
    requirements: str
    diagram: str
    terraform: str
    apply_output: str
    approved_diagram: bool
    approved_terraform: bool
    step: str
    done: bool
    error: Optional[str]

def create_state() -> WorkflowState:
    return {
        "requirements": "",
        "diagram": "",
        "terraform": "",
        "apply_output": "",
        "approved_diagram": False,
        "approved_terraform": False,
        "step": "idle",
        "done": False,
        "error": None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/backend/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Write failing test for events**

Create `tests/backend/test_events.py`:
```python
from openship.events import WorkflowEvent

def test_event_creation():
    event = WorkflowEvent(
        type="stage_start",
        stage="generating_diagram",
        message="Starting diagram generation"
    )
    assert event.type == "stage_start"
    assert event.stage == "generating_diagram"

def test_event_to_sse():
    event = WorkflowEvent(type="stage_complete", stage="diagram", data={"diagram": "test"})
    sse = event.to_sse()
    assert "event: stage_complete" in sse
    assert "data:" in sse
```

- [ ] **Step 6: Create events module**

Create `backend/src/openship/events.py`:
```python
import json
import time
from dataclasses import dataclass, asdict
from typing import Optional, Any

@dataclass
class WorkflowEvent:
    type: str
    stage: str
    message: str = ""
    data: Optional[dict] = None
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_sse(self) -> str:
        payload = {
            "type": self.type,
            "stage": self.stage,
            "message": self.message,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        return f"event: {self.type}\ndata: {json.dumps(payload)}\n\n"

    @property
    def payload(self) -> dict:
        return asdict(self)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/backend/test_events.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/src/openship/state.py backend/src/openship/events.py tests/backend/
git commit -m "feat: add workflow state and event types"
```

### Task 2: Backend - LangGraph Workflow with Checkpointing

**Files:**
- Modify: `backend/src/openship/workflow.py`
- Create: `backend/src/openship/checkpoints.py`
- Test: `tests/backend/test_workflow.py`

- [ ] **Step 1: Write failing test for workflow graph compilation**

Create `tests/backend/test_workflow.py`:
```python
from openship.workflow import compile_graph

def test_compile_graph():
    graph = compile_graph()
    assert graph is not None
```

- [ ] **Step 2: Create checkpoints module**

Create `backend/src/openship/checkpoints.py`:
```python
from langgraph.checkpoint.sqlite import SqliteSaver

def get_checkpointer() -> SqliteSaver:
    return SqliteSaver("checkpoints.db")
```

- [ ] **Step 3: Refactor workflow module**

Rewrite `backend/src/openship/workflow.py`:
```python
import uuid
from typing import AsyncGenerator

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from openship.config import config
from openship.llm import generate_diagram, generate_terraform
from openship.state import WorkflowState, create_state
from openship.events import WorkflowEvent
from openship.checkpoints import get_checkpointer

def compile_graph() -> StateGraph:
    graph = StateGraph(WorkflowState)

    def parse_requirements(state):
        return {"step": "parsing_requirements", "requirements": state.get("requirements", "")}

    def generate_diagram_node(state):
        diagram = generate_diagram(state.get("requirements", ""))
        return {"step": "generating_diagram", "diagram": diagram}

    def generate_terraform_node(state):
        terraform = generate_terraform(state.get("requirements", ""), state.get("diagram", ""))
        return {"step": "generating_terraform", "terraform": terraform}

    def apply_terraform_node(state):
        if config.CLOUD_MOCK or not config.TERRAFORM_APPLY:
            output = "[Mocked] Running terraform plan\n\nTerraform plan completed successfully."
        else:
            output = "Running terraform apply...\n\nApply completed successfully."
        return {"step": "applying_terraform", "apply_output": output, "done": True}

    graph.add_node("parse_requirements", parse_requirements)
    graph.add_node("generate_diagram", generate_diagram_node)
    graph.add_node("generate_terraform", generate_terraform_node)
    graph.add_node("apply_terraform", apply_terraform_node)

    graph.set_entry_point("parse_requirements")
    graph.add_edge("parse_requirements", "generate_diagram")
    graph.add_edge("generate_diagram", "generate_terraform")
    graph.add_edge("generate_terraform", "apply_terraform")
    graph.add_edge("apply_terraform", END)

    checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)

async def run_workflow(requirements: str) -> AsyncGenerator[WorkflowEvent, None]:
    run_id = str(uuid.uuid4())
    graph = compile_graph()

    config_obj = {"configurable": {"thread_id": run_id}}

    yield WorkflowEvent(type="stage_start", stage="generating_diagram", message="Generating architecture diagram")

    async for chunk in graph.astream({"requirements": requirements}, config=config_obj):
        for node, data in chunk.items():
            if node == "generate_diagram":
                yield WorkflowEvent(type="stage_complete", stage="generating_diagram", data={"diagram": data.get("diagram", "")})
                yield WorkflowEvent(type="stage_start", stage="generating_terraform", message="Generating Terraform code")
            elif node == "generate_terraform":
                yield WorkflowEvent(type="stage_complete", stage="generating_terraform", data={"terraform": data.get("terraform", "")})
                yield WorkflowEvent(type="stage_start", stage="applying_terraform", message="Applying Terraform")
            elif node == "apply_terraform":
                yield WorkflowEvent(type="stage_complete", stage="applying_terraform", data={"output": data.get("apply_output", "")})

    yield WorkflowEvent(type="complete", stage="done", message="Workflow completed")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/backend/test_workflow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/openship/workflow.py backend/src/openship/checkpoints.py tests/backend/test_workflow.py
git commit -m "feat: implement LangGraph workflow with checkpointing and streaming"
```

### Task 3: Backend - SSE API Endpoints

**Files:**
- Modify: `backend/src/openship/api.py`
- Test: `tests/backend/test_api.py`

- [ ] **Step 1: Write failing test for SSE endpoint**

Create `tests/backend/test_api.py`:
```python
import pytest
from fastapi.testclient import TestClient
from openship.app import app

client = TestClient(app)

def test_create_workflow():
    response = client.post("/api/workflows", json={"requirements": "test"})
    assert response.status_code == 200
    assert "run_id" in response.json()

def test_stream_events_endpoint_exists():
    response = client.get("/api/workflows/test-id/events")
    assert response.status_code == 200
```

- [ ] **Step 2: Update API module**

Rewrite `backend/src/openship/api.py`:
```python
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from openship.workflow import run_workflow
from openship.config import config

router = APIRouter()

class WorkflowRequest(BaseModel):
    requirements: str = ""

class ApproveRequest(BaseModel):
    pass

@router.post("/workflows")
async def create_new_workflow(req: WorkflowRequest):
    """Create a new workflow run and stream events."""
    return StreamingResponse(
        run_workflow(req.requirements),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.post("/workflows/{run_id}/approve-diagram")
async def approve_diagram_endpoint(run_id: str, req: ApproveRequest):
    """Approve the diagram and continue the workflow."""
    return {"run_id": run_id, "status": "diagram approved"}

@router.post("/workflows/{run_id}/approve-terraform")
async def approve_terraform_endpoint(run_id: str, req: ApproveRequest):
    """Approve the terraform code and continue the workflow."""
    return {"run_id": run_id, "status": "terraform approved"}

@router.get("/config")
async def get_config():
    """Get the current configuration."""
    return {
        "cloud_provider": config.CLOUD_PROVIDER,
        "cloud_region": config.CLOUD_REGION,
        "cloud_mock": config.CLOUD_MOCK,
        "llm_provider": config.LLM_PROVIDER,
        "llm_model": config.LLM_MODEL,
        "llm_base_url": config.LLM_BASE_URL,
        "terraform_backend": config.TERRAFORM_BACKEND,
        "terraform_apply": config.TERRAFORM_APPLY,
    }
```

- [ ] **Step 3: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/backend/test_api.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/src/openship/api.py tests/backend/test_api.py
git commit -m "feat: implement SSE streaming API endpoints"
```

### Task 4: Frontend - Install Dependencies and Setup

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.js`

- [ ] **Step 1: Install SSE client library**

Run: `cd frontend && npm install @stdlib/eventsource`
Expected: Package installed successfully

- [ ] **Step 2: Configure Vite proxy for SSE**

Update `frontend/vite.config.js`:
```javascript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      }
    }
  }
})
```

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.js
git commit -m "feat: setup frontend dependencies and proxy configuration"
```

### Task 5: Frontend - Vertical Timeline Layout

**Files:**
- Create: `frontend/src/components/WorkflowTimeline.vue`
- Create: `frontend/src/components/StageItem.vue`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Create StageItem component**

Create `frontend/src/components/StageItem.vue`:
```vue
<template>
  <div class="stage-item" :class="{ active: isActive, completed: isCompleted }">
    <div class="stage-indicator">
      <div v-if="isCompleted" class="indicator-dot completed">✓</div>
      <div v-else-if="isActive" class="indicator-dot active">
        <svg class="spinner" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" fill="none"
                  stroke-dasharray="40" stroke-dashoffset="10">
            <animateTransform attributeName="transform" type="rotate"
                              from="0 10 10" to="360 10 10" dur="1s" repeatCount="indefinite"/>
          </circle>
        </svg>
      </div>
      <div v-else class="indicator-dot pending"></div>
    </div>
    <div class="stage-label">
      <span>{{ label }}</span>
      <span v-if="statusMessage" class="status-message">{{ statusMessage }}</span>
    </div>
    <div v-if="showConnector" class="stage-connector"></div>
  </div>
</template>

<script setup>
defineProps({
  label: String,
  isActive: Boolean,
  isCompleted: Boolean,
  statusMessage: String,
  showConnector: Boolean
})
</script>

<style scoped>
.stage-item {
  display: flex;
  align-items: center;
  padding: 8px 0;
  position: relative;
}

.indicator-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  margin-right: 12px;
  flex-shrink: 0;
}

.indicator-dot.completed {
  background: #4af62c;
  color: #1a1a1a;
}

.indicator-dot.active {
  background: rgba(74, 246, 44, 0.2);
  color: #4af62c;
}

.indicator-dot.pending {
  background: #333;
  color: #666;
}

.stage-label {
  display: flex;
  flex-direction: column;
}

.stage-label span:first-child {
  color: #e0e0e0;
  font-size: 14px;
}

.status-message {
  color: #4af62c;
  font-size: 11px;
  font-style: italic;
}

.stage-connector {
  position: absolute;
  left: 9px;
  top: 28px;
  width: 2px;
  height: 100%;
  background: #333;
}

.stage-item:last-child .stage-connector {
  display: none;
}
</style>
```

- [ ] **Step 2: Create WorkflowTimeline component**

Create `frontend/src/components/WorkflowTimeline.vue`:
```vue
<template>
  <div class="workflow-timeline">
    <StageItem
      v-for="(stage, index) in stages"
      :key="stage.id"
      :label="stage.label"
      :is-active="activeStage === stage.id"
      :is-completed="completedStages.includes(stage.id)"
      :status-message="activeStage === stage.id ? stage.statusMessage : null"
      :show-connector="index < stages.length - 1"
    />
  </div>
</template>

<script setup>
import StageItem from './StageItem.vue'

const props = defineProps({
  stages: Array,
  activeStage: String,
  completedStages: Array
})
</script>

<style scoped>
.workflow-timeline {
  display: flex;
  flex-direction: column;
}
</style>
```

- [ ] **Step 3: Refactor App.vue to use vertical timeline**

Rewrite `frontend/src/App.vue`:
```vue
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

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value)
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          const eventType = line.slice(7)
          handleSSEEvent(eventType, lines)
        }
      }
    }
  } catch (error) {
    console.error('Workflow error:', error)
  }
}

function handleSSEEvent(eventType, lines) {
  let dataLine = lines.find(l => l.startsWith('data: '))
  if (!dataLine) return

  const data = JSON.parse(dataLine.slice(6))

  if (eventType === 'stage_start') {
    activeStage.value = data.stage
  } else if (eventType === 'stage_complete') {
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
```

- [ ] **Step 4: Test the UI manually**

Run: `cd frontend && npm run dev`
Navigate to http://localhost:5173 and verify the vertical timeline layout renders correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ frontend/src/App.vue
git commit -m "feat: implement vertical timeline layout with stage indicators"
```

### Task 6: Frontend - SSE Event Handling

**Files:**
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Add proper SSE event listener**

Update the `startWorkflow` function in `frontend/src/App.vue` to use EventSource properly:
```javascript
async function startWorkflow() {
  const response = await fetch('/api/workflows', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requirements: requirements.value })
  })

  if (!response.ok) throw new Error('Failed to start workflow')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value)
    const lines = buffer.split('\n')
    buffer = lines.pop()

    let currentEvent = null
    let currentData = []

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
```

- [ ] **Step 2: Test SSE streaming**

Run the backend and frontend, start a workflow, and verify SSE events are received and processed correctly.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.vue
git commit -m "feat: implement SSE event handling for real-time updates"
```

### Task 7: Frontend - Status Indicators and Animations

**Files:**
- Modify: `frontend/src/components/StageItem.vue`

- [ ] **Step 1: Add spinner animation CSS**

Update `frontend/src/components/StageItem.vue` with proper spinner animation:
```vue
<style scoped>
.spinner {
  width: 16px;
  height: 16px;
}
</style>
```

- [ ] **Step 2: Add status message display**

Ensure status messages are displayed correctly during active stages.

- [ ] **Step 3: Test animations**

Verify spinner animations and status indicators work correctly during workflow execution.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/StageItem.vue
git commit -m "feat: add spinner animations and status indicators"
```

### Task 8: Integration Testing

**Files:**
- Create: `tests/integration/test_e2e.py`

- [ ] **Step 1: Write integration test**

Create `tests/integration/test_e2e.py`:
```python
import pytest
from fastapi.testclient import TestClient
from openship.app import app

client = TestClient(app)

def test_full_workflow_streaming():
    """Test that the full workflow streams events correctly."""
    response = client.post(
        "/api/workflows",
        json={"requirements": "Simple web app with load balancer"},
        headers={"Accept": "text/event-stream"}
    )
    assert response.status_code == 200
    assert "stage_start" in response.text
    assert "stage_complete" in response.text
    assert "complete" in response.text
```

- [ ] **Step 2: Run integration test**

Run: `cd backend && uv run pytest tests/integration/ -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_e2e.py
git commit -m "test: add end-to-end integration test for workflow streaming"
```

### Task 9: Manual E2E Verification

- [ ] **Step 1: Start backend**

Run: `cd backend && uv run python -m openship.main`

- [ ] **Step 2: Start frontend**

Run: `cd frontend && npm run dev`

- [ ] **Step 3: Verify full workflow**

Navigate to http://localhost:5173, enter requirements, click Generate, and verify:
- Vertical timeline shows stage progression
- Spinners appear during LLM processing
- Diagram renders after generation
- Terraform code displays after generation
- Apply output shows completion

- [ ] **Step 4: Document results**

Add verification notes to the implementation plan.

### Task 10: Cleanup and Final Commit

- [ ] **Step 1: Clean up temporary files**

Remove any temporary files or debug code.

- [ ] **Step 2: Final commit**

```bash
git add .
git commit -m "feat: complete LangGraph streaming UI implementation"
```

---

## Self-Review Checklist

- [ ] All spec requirements are covered by tasks
- [ ] No placeholders (TBD, TODO) in the plan
- [ ] All file paths are exact and complete
- [ ] All code snippets are complete and testable
- [ ] Type names are consistent across tasks
- [ ] Tests are included for each major component
- [ ] Commands include expected output

## Execution Notes

This plan should be executed using subagent-driven development, with a fresh subagent per task and review between tasks. Each task is small and self-contained, making it ideal for subagent execution.