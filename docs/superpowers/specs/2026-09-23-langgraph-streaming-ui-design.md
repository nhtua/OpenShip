# OpenShip: LangGraph Streaming UI Design

## Overview

This document describes the design for improving OpenShip's frontend to stream LangGraph workflow progress in real-time. The current PoC uses a static, non-streaming approach where the entire workflow executes synchronously. This design introduces real-time updates via Server-Sent Events (SSE), vertical stage layout with progress indicators, and durable state management via LangGraph's checkpointing.

## Problem

The current PoC has several UX issues:
1. When user approves a stage and the workflow progresses to the next step, the LLM processing takes time with no visible feedback
2. The UI appears non-responsive during LLM processing with no progress indicator
3. Horizontal pill layout at the top doesn't scale well with stage status information

## Design Decisions

### 1. Streaming Approach: Server-Sent Events (SSE)

**Why SSE?**
- Simple one-way communication from backend to frontend
- Built into FastAPI and browsers
- Automatic reconnect support
- Proven reliable for chat bot and streaming use cases

**API Endpoint:**
```
POST /api/workflows → returns run_id
GET /api/workflows/{run_id}/events → SSE stream
```

### 2. Layout: Vertical Timeline

**Why vertical timeline?**
- Better space for stage status text and progress indicators
- Easier to expand for additional stages
- Clear visual flow of workflow progression
- Stage labels on left, content on right

**Layout Structure:**
- Left column: Stage labels with status indicators (dots, spinners, checkmarks) and connecting lines
- Right column: Content area for the active stage
- Active stage highlighted with green dot and spinner during processing
- Completed stages shown with green checkmark
- Upcoming stages shown with gray dots

### 3. Backend Integration: LangGraph Checkpointing + SSE

**Why LangGraph checkpointing?**
- Durable state management across restarts
- Resume capability from any checkpoint
- Uses LangGraph's native streaming via `astream()`
- Most robust long-term solution for long-running DevOps workflows

**Implementation:**
- Use LangGraph's `StateGraph` with `SqliteSaver` checkpoint saver
- Stream events via FastAPI's `StreamingResponse`
- Events emitted at each checkpoint transition

## Architecture

### System Components

```
Frontend (Vue3)           Backend (FastAPI)           External Services
─────────────────          ─────────────────          ──────────────────
• Workflow UI              • REST API endpoints        • LLM API
• EventSource SSE client   • SSE streaming endpoint    • Terraform CLI
• Mermaid diagram rendering • LangGraph orchestrator   • Cloud APIs
• Approval buttons         • SQLite checkpoint saver
```

### Data Flow

```
Frontend                Backend                 LangGraph              External
    |                        |                        |                    |
    |--POST /workflows------>|                        |                    |
    |                        |--compile graph------->|                    |
    |                        |--invoke(req)--------->|                    |
    |                        |                        |--LLM call--------->|
    |                        |                        |<--diagram---------|
    |                        |                        |--checkpoint------>|
    |                        |<--SSE:stage_complete--|                    |
    |<--SSE event-----------|                        |                    |
    |                        |                        |                    |
    |--GET /workflows/{id}/events------------------>|                    |
    |<--SSE stream of events|<--astream()-----------|                    |
    |                        |                        |                    |
    |--POST /approve-diagram>|                        |                    |
    |                        |--resume graph------->|                    |
    |                        |                        |--LLM call--------->|
    |                        |                        |<--terraform-------|
    |                        |<--SSE:stage_complete--|                    |
    |<--SSE event-----------|                        |                    |
```

## Backend Design

### Components

| Module | Responsibility | Key Functions |
|--------|----------------|---------------|
| `api.py` | REST endpoints + SSE streaming | `create_workflow`, `stream_events`, `approve_diagram` |
| `workflow.py` | LangGraph state machine | `compile_graph`, `run_workflow`, `resume_workflow` |
| `events.py` | Event types and serialization | `WorkflowEvent` class, `to_sse()` |
| `state.py` | Workflow state definition | `WorkflowState` TypedDict |
| `checkpoints.py` | Checkpoint management | `get_checkpointer`, `list_checkpoints` |
| `llm.py` | LLM integration | `generate_diagram`, `generate_terraform` |

### Event Types

```python
class WorkflowEvent:
    type: str  # "stage_start" | "stage_complete" | "llm_thinking" | "error" | "complete"
    stage: str  # "generating_diagram" | "generating_terraform" | "applying"
    message: str  # Human-readable description
    data: dict  # Optional additional data (e.g., diagram source)
    timestamp: float
```

### SSE Endpoint Implementation

```python
@router.get("/workflows/{run_id}/events")
async def stream_events(run_id: str):
    async def event_generator():
        config = {"configurable": {"thread_id": run_id}}
        async for chunk in graph.astream(req, config=config):
            event = WorkflowEvent(
                type="checkpoint",
                stage=chunk.get("stage", "unknown"),
                message="Processing...",
                data=chunk,
                timestamp=time.time()
            )
            yield event.to_sse()
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
```

## Frontend Design

### Components

| Component | Responsibility |
|-----------|----------------|
| `App.vue` | Main app, layout, routing |
| `WorkflowTimeline.vue` | Vertical stage timeline with status indicators |
| `StageItem.vue` | Single stage: label, status dot, connecting line |
| `StageContent.vue` | Right-side content area for active stage |
| `RequirementsEditor.vue` | Markdown editor for requirements |
| `DiagramPreview.vue` | Mermaid diagram rendering with live preview |
| `TerraformViewer.vue` | Code viewer for generated Terraform |
| `ApplyOutput.vue` | Terraform apply output display |
| `Spinner.vue` | Reusable loading spinner component |

### Workflow Timeline Layout

```
[Left Column]                          [Right Column]
─────────────                          ──────────────
● Requirements (complete)              ┌─────────────────────┐
│                                      │ Current Stage: Diagram│
│ Diagram (active) ● processing...     │ ⚡ Generating architecture│
│                                      │ diagram from requirements│
│ Terraform (pending) ○                │ This may take a moment...│
│                                      └─────────────────────┘
│ Apply (pending) ○
```

### SSE Event Handling

```javascript
const eventSource = new EventSource(
  `/api/workflows/${runId}/events`
);

eventSource.addEventListener("stage_start", (e) => {
  const data = JSON.parse(e.data);
  updateStageStatus(data.stage, "in_progress");
  showSpinner(data.stage);
});

eventSource.addEventListener("stage_complete", (e) => {
  const data = JSON.parse(e.data);
  updateStageStatus(data.stage, "complete");
  hideSpinner(data.stage);
  if (data.data.diagram) {
    renderDiagram(data.data.diagram);
  }
});

eventSource.addEventListener("complete", () => {
  eventSource.close();
  markWorkflowComplete();
});
```

## Error Handling

| Error Type | Handling |
|------------|----------|
| LLM API timeout | Retry 3x with exponential backoff, then emit error event |
| LLM API error | Capture error, emit error event with details, allow resume |
| Terraform validation error | Emit error with terraform output, pause for user review |
| SSE connection lost | Frontend auto-reconnects, requests missed events from last checkpoint |
| Checkpoint restore failure | Log error, start fresh workflow, notify user |
| Database lock | Retry with short delay, log if persistent |

## Testing Strategy

### Test Pyramid

- **Unit tests:** Workflow state transitions, event serialization, LLM prompt generation
- **Integration tests:** End-to-end workflow with mock LLM, SSE streaming, checkpoint save/restore
- **E2E tests:** Full workflow with real LLM (optional, slower)

### Mock LLM for Testing

```python
class MockLLMClient:
    def generate_diagram(self, requirements):
        return "graph TD\\nA-->B\\nB-->C"

    def generate_terraform(self, requirements, diagram):
        return 'resource "aws_instance" "test" {}'

# Use in tests
workflow = WorkflowEngine(llm_client=MockLLMClient())
result = workflow.run("Simple web app")
assert result["diagram"] == "graph TD\\nA-->B\\nB-->C"
```

## Migration Path from PoC

The current PoC can be migrated to this design with moderate effort (~1-2 days):
1. Replace synchronous workflow execution with LangGraph's `astream()`
2. Add SQLite checkpoint saver
3. Implement SSE streaming endpoint
4. Update frontend to use EventSource API
5. Implement vertical timeline layout

## Key Benefits

1. **Real-time progress updates** - User sees exactly what's happening at each stage
2. **Responsive UI** - Spinner and status text during LLM processing
3. **Durable state** - Checkpointing enables resume after failures
4. **Scalable layout** - Vertical timeline handles additional stages easily
5. **Improved user experience** - Clear visual indicators of workflow progress
