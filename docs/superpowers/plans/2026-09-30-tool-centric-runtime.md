# OpenShip Tool-Centric Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tool-centric runtime for interactive AI agents with CLI and Web interfaces, flat tool/workflow registries, sandboxed execution, and sub-agent support.

**Architecture:** Python backend with FastAPI and LangGraph for agent orchestration, SQLite3 for state persistence, Vue.js frontend, Docker-based sandbox execution. Flat registries with metadata tags distinguish built-in from custom tools and workflows.

**Tech Stack:** Python 3.12+, FastAPI, LangGraph, LangChain, SQLite3, Vue.js 3, TypeScript, Docker, shadcn-vue

**Spec:** `docs/superpowers/specs/2026-09-30-tool-centric-runtime-design.md`

## Global Constraints

- Python 3.12+ with type hints
- SQLite3 embedded database
- Docker for sandbox execution
- FastAPI REST API
- LangGraph for workflow orchestration
- Vue.js 3 with TypeScript for web UI
- Semantic versioning for tools and workflows
- MIT license

## Review Focus

- Tool execution with invalid inputs should return structured errors, not crash
- Workflow checkpointing should survive process restarts
- Sandbox cleanup should occur on workflow completion and timeout
- Parallel sub-agent execution should not race on shared state
- Chat history should persist across workflow pauses and resumes

---

## File Structure

```
openship/
├── apps/
│   ├── agent/                    # Agent backend service
│   │   ├── src/
│   │   │   ├── __init__.py
│   │   │   ├── main.py           # FastAPI application
│   │   │   ├── api/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── routes.py     # API route definitions
│   │   │   │   └── middleware.py # API middleware
│   │   │   ├── core/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── engine.py     # LangGraph orchestration engine
│   │   │   │   ├── state.py      # State management
│   │   │   │   └── sandbox.py    # Sandbox lifecycle management
│   │   │   ├── registry/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── tools.py      # Tool registry operations
│   │   │   │   └── workflows.py  # Workflow registry operations
│   │   │   ├── tools/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py       # Tool base class
│   │   │   │   └── builtin/      # Built-in tool implementations
│   │   │   └── database/
│   │   │       ├── __init__.py
│   │   │       ├── schema.py     # SQLite schema definitions
│   │   │       └── operations.py # Database operations
│   │   ├── tests/
│   │   └── pyproject.toml
│   ├── cli/                      # CLI interface
│   │   ├── src/
│   │   │   ├── __init__.py
│   │   │   └── main.py           # CLI entry point
│   │   └── pyproject.toml
│   └── web/                      # Web interface
│       ├── src/
│       │   ├── App.vue
│       │   ├── views/
│       │   ├── components/
│       │   └── stores/
│       ├── package.json
│       └── vite.config.ts
└── docs/
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `apps/agent/pyproject.toml`
- Create: `apps/cli/pyproject.toml`
- Create: `apps/web/package.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/agent/src/__init__.py`
- Create: `apps/cli/src/__init__.py`

- [ ] **Step 1: Create agent pyproject.toml**

```toml
[project]
name = "openship-agent"
version = "0.1.0"
description = "OpenShip agent backend service"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "langgraph>=0.2.0",
    "langchain>=0.3.0",
    "uvicorn>=0.30.0",
    "pydantic>=2.0.0"
]

[project.optional-dependencies]
dev = ["pytest>=8.0.0", "ruff>=0.4.0"]
```

- [ ] **Step 2: Create CLI pyproject.toml**

```toml
[project]
name = "openship-cli"
version = "0.1.0"
description = "OpenShip CLI interface"
requires-python = ">=3.12"
dependencies = [
    "typer>=0.12.0",
    "rich>=13.0.0",
    "httpx>=0.27.0"
]

[project.scripts]
openship = "src.main:app"
```

- [ ] **Step 3: Create web package.json**

```json
{
  "name": "openship-web",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc && vite build"
  },
  "dependencies": {
    "vue": "^3.5.0",
    "pinia": "^2.2.0",
    "vue-router": "^4.4.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.1.0",
    "vite": "^6.0.0",
    "typescript": "^5.5.0",
    "vue-tsc": "^2.1.0"
  }
}
```

- [ ] **Step 4: Create web vite.config.ts**

```typescript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
```

- [ ] **Step 5: Create Python __init__.py files**

Create empty `__init__.py` files in all Python packages.

- [ ] **Step 6: Commit**

```bash
git add apps/
git commit -m "feat: scaffold project structure for agent, CLI, and web apps"
```

---

### Task 2: SQLite Database Schema

**Files:**
- Create: `apps/agent/src/database/schema.py`
- Create: `apps/agent/src/database/operations.py`
- Create: `apps/agent/tests/test_database.py`

**Interfaces:**
- Produces: `init_db(path)`, `close_db()`, CRUD operations for workflows, tools, executions, checkpoints

- [ ] **Step 1: Write test for database initialization**

```python
def test_init_db_creates_tables():
    db = init_db(":memory:")
    cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "workflows" in tables
    assert "tools" in tables
    assert "workflow_executions" in tables
    assert "checkpoints" in tables
```

- [ ] **Step 2: Implement schema definitions**

Define CREATE TABLE statements matching the spec schema with all columns including `sandbox_id`.

- [ ] **Step 3: Implement init_db and close_db**

- [ ] **Step 4: Run test**

- [ ] **Step 5: Commit**

---

### Task 3: Tool Registry Operations

**Files:**
- Create: `apps/agent/src/registry/tools.py`
- Create: `apps/agent/tests/test_tool_registry.py`

**Interfaces:**
- Consumes: Database operations from Task 2
- Produces: `register_tool(tool)`, `get_tool(name, version=None)`, `list_tools(category=None, origin=None)`, `find_tools(query)`

- [ ] **Step 1: Write test for tool registration**

```python
def test_register_and_get_tool():
    tool = {
        "name": "file.read",
        "version": "1.0.0",
        "origin": "builtin",
        "description": "Read file contents",
        "category": "file.read",
        "inputs": {"type": "object", "properties": {"path": {"type": "string"}}},
        "outputs": {"type": "object", "properties": {"content": {"type": "string"}}}
    }
    register_tool(tool)
    retrieved = get_tool("file.read")
    assert retrieved["name"] == "file.read"
    assert retrieved["version"] == "1.0.0"
```

- [ ] **Step 2: Implement tool registry operations**

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

---

### Task 4: Workflow Registry Operations

**Files:**
- Create: `apps/agent/src/registry/workflows.py`
- Create: `apps/agent/tests/test_workflow_registry.py`

**Interfaces:**
- Consumes: Database operations from Task 2
- Produces: `register_workflow(workflow)`, `get_workflow(name, version=None)`, `list_workflows(origin=None)`, `find_workflows(query)`

- [ ] **Step 1: Write test for workflow registration**

```python
def test_register_and_get_workflow():
    workflow = {
        "name": "test_workflow",
        "version": "1.0.0",
        "origin": "builtin",
        "description": "Test workflow",
        "definition": "# Test\nThis is a test workflow."
    }
    register_workflow(workflow)
    retrieved = get_workflow("test_workflow")
    assert retrieved["name"] == "test_workflow"
    assert retrieved["origin"] == "builtin"
```

- [ ] **Step 2: Implement workflow registry operations**

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

---

### Task 5: Sandbox Management

**Files:**
- Create: `apps/agent/src/core/sandbox.py`
- Create: `apps/agent/tests/test_sandbox.py`

**Interfaces:**
- Produces: `create_sandbox(config) -> sandbox_id`, `execute_in_sandbox(sandbox_id, tool, inputs) -> output`, `cleanup_sandbox(sandbox_id)`

- [ ] **Step 1: Write test for sandbox creation**

```python
def test_create_sandbox():
    config = {
        "type": "process",
        "working_dir": "/tmp/test-sandbox"
    }
    sandbox_id = create_sandbox(config)
    assert sandbox_id is not None
    cleanup_sandbox(sandbox_id)
```

- [ ] **Step 2: Implement sandbox management**

Support process sandbox initially. Container sandbox via Docker SDK deferred to later task.

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

---

### Task 6: Built-in Tool Implementations

**Files:**
- Create: `apps/agent/src/tools/base.py`
- Create: `apps/agent/src/tools/builtin/file_read.py`
- Create: `apps/agent/src/tools/builtin/file_write.py`
- Create: `apps/agent/src/tools/builtin/shell_exec.py`
- Create: `apps/agent/tests/test_builtin_tools.py`

**Interfaces:**
- Consumes: Tool registry from Task 3, sandbox from Task 5
- Produces: Built-in tool implementations for file.read, file.write, shell.exec

- [ ] **Step 1: Write test for file.read tool**

```python
def test_file_read_tool():
    # Write a test file
    with open("/tmp/test-file.txt", "w") as f:
        f.write("Hello, World!")
    
    # Execute tool
    result = execute_tool("file.read", {"path": "/tmp/test-file.txt"})
    assert result["content"] == "Hello, World!"
```

- [ ] **Step 2: Implement tool base class**

Define tool interface with `execute(inputs, context)` method.

- [ ] **Step 3: Implement file.read, file.write, shell.exec tools**

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

---

### Task 7: LangGraph Orchestration Engine

**Files:**
- Create: `apps/agent/src/core/engine.py`
- Create: `apps/agent/src/core/state.py`
- Create: `apps/agent/tests/test_engine.py`

**Interfaces:**
- Consumes: Tool registry, workflow registry, sandbox, tool implementations
- Produces: `build_graph(workflow)`, `run_graph(graph, inputs) -> output`, `pause_graph(graph_id)`, `resume_graph(graph_id, response)`

- [ ] **Step 1: Write test for graph construction**

```python
def test_build_graph():
    workflow = get_workflow("test_workflow")
    graph = build_graph(workflow)
    assert graph is not None
```

- [ ] **Step 2: Implement state management**

Define workflow state schema with checkpoint support.

- [ ] **Step 3: Implement graph construction from workflow definition**

Parse workflow definition into LangGraph nodes and edges.

- [ ] **Step 4: Implement graph execution with checkpointing**

- [ ] **Step 5: Run tests**

- [ ] **Step 6: Commit**

---

### Task 8: FastAPI Backend Service

**Files:**
- Create: `apps/agent/src/main.py`
- Create: `apps/agent/src/api/routes.py`
- Create: `apps/agent/src/api/middleware.py`
- Create: `apps/agent/tests/test_api.py`

**Interfaces:**
- Consumes: All core components
- Produces: REST API endpoints per spec

- [ ] **Step 1: Write test for API endpoint**

```python
def test_list_workflows():
    response = client.get("/api/workflows")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

- [ ] **Step 2: Implement FastAPI application**

Initialize app, database, registries.

- [ ] **Step 3: Implement API routes**

All endpoints per spec.

- [ ] **Step 4: Run tests**

- [ ] **Step 5: Commit**

---

### Task 9: CLI Interface

**Files:**
- Create: `apps/cli/src/main.py`
- Create: `apps/cli/tests/test_cli.py`

**Interfaces:**
- Consumes: Backend API from Task 8

- [ ] **Step 1: Write test for CLI command**

```python
def test_workflow_list():
    result = runner.invoke(app, ["workflow", "list"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Implement CLI with Typer**

Commands: workflow list/run/status, tool list/describe, session management.

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

---

### Task 10: Web Interface

**Files:**
- Create: `apps/web/src/App.vue`
- Create: `apps/web/src/views/HomeView.vue`
- Create: `apps/web/src/views/AgentWorkspace.vue`
- Create: `apps/web/src/components/ChatStream.vue`
- Create: `apps/web/src/components/ChatInput.vue`
- Create: `apps/web/src/components/ToolCallCard.vue`
- Create: `apps/web/src/components/ApprovalCard.vue`
- Create: `apps/web/src/stores/useAgentStore.ts`

**Interfaces:**
- Consumes: Backend API from Task 8

- [ ] **Step 1: Write test for component rendering**

```vue
import { mount } from '@vue/test-utils'
import ChatInput from '../components/ChatInput.vue'

test('renders chat input', () => {
  const wrapper = mount(ChatInput)
  expect(wrapper.find('textarea').exists()).toBe(true)
})
```

- [ ] **Step 2: Implement Vue.js application**

Chat-first interface with workflow management and artifact inspection.

- [ ] **Step 3: Run tests**

- [ ] **Step 4: Commit**

---

### Task 11: Integration & End-to-End Testing

**Files:**
- Create: `apps/agent/tests/test_integration.py`

- [ ] **Step 1: Write integration test for full workflow execution**

```python
def test_full_workflow_execution():
    # Register workflow
    # Run workflow
    # Verify output
    # Verify checkpointing
    pass
```

- [ ] **Step 2: Run all tests**

- [ ] **Step 3: Commit**

---

## Execution Notes

- Tasks 1-4 can be parallelized (scaffolding, database, registries)
- Task 5 depends on Task 1
- Task 6 depends on Tasks 3 and 5
- Task 7 depends on Tasks 3, 4, 5, and 6
- Task 8 depends on Tasks 2-7
- Task 9 depends on Task 8
- Task 10 depends on Task 8
- Task 11 depends on all preceding tasks

Recommended execution order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11

For subagent-driven development, tasks 1-4 can be dispatched in parallel, then tasks 5-6, then 7, then 8, then 9-10 in parallel, then 11.