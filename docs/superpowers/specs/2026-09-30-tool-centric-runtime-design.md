---
type: spec
summary: Tool-centric runtime architecture for interactive AI agents serving CLI and Web interfaces, with flat registries for tools and workflows, sandboxed execution, and sub-agent support.
status: draft
date: 2026-09-30
---

# OpenShip Tool-Centric Runtime Design

## Vision

OpenShip is a tool-centric runtime for interactive AI agents that serves both CLI and Web chat interfaces. The platform provides a unified, extensible infrastructure where agents dynamically select and compose tools to accomplish DevOps tasks, with human-in-the-loop integration at critical decision points.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenShip Runtime                         │
│                                                             │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │   CLI Interface   │  │   Web Interface  │                │
│  │  (openship cli)   │  │ (openship web)   │                │
│  └────────┬─────────┘  └────────┬─────────┘                │
│           │                      │                          │
│           └──────────┬───────────┘                          │
│                      ▼                                      │
│  ┌─────────────────────────────────────┐                   │
│  │        Agent Orchestration           │                   │
│  │   (LangGraph workflow engine)        │                   │
│  └──────────────┬──────────────────────┘                   │
│                 │                                           │
│  ┌──────────────┴──────────────────────┐                   │
│  │      Unified Tool Registry           │                   │
│  │  (flat, metadata-tagged)             │                   │
│  └──────────────┬──────────────────────┘                   │
│                 │                                           │
│  ┌──────────────┴──────────────────────┐                   │
│  │      Unified Workflow Registry       │                   │
│  │  (flat, metadata-tagged)             │                   │
│  └──────────────┬──────────────────────┘                   │
│                 │                                           │
│  ┌──────────────┴──────────────────────┐                   │
│  │         Connector Layer             │                   │
│  │  (platform-agnostic abstractions)   │                   │
│  └──────────────┬──────────────────────┘                   │
│                 │                                           │
│  ┌──────────────┴──────────────────────┐                   │
│  │         Sandbox Execution           │                   │
│  │  (isolated, ephemeral environments) │                   │
│  └─────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

## Key Architectural Decisions

1. **Unified interfaces:** CLI and Web are separate clients that communicate with the same agent orchestration backend. The runtime is interface-agnostic.

2. **Flat registries with metadata:** Tools and workflows are registered in single flat registries. Metadata fields (`origin`, `visibility`, `permissions`) distinguish built-in from custom without architectural separation.

3. **Agent as orchestrator:** The LangGraph workflow engine coordinates tool selection, execution, and human-in-the-loop interactions. The agent dynamically determines execution order at runtime (not rigid DAGs).

4. **Connector abstraction:** Platform-specific APIs are wrapped behind a unified connector interface. Tools reference connectors by name, not platform-specific implementations.

5. **Sandbox isolation:** Tool execution happens in isolated, ephemeral environments to prevent side effects on the host system or other workflows.

## Tool Registry Design

### Flat Registry with Metadata Tags

All tools — built-in, community, and custom — are registered in a single flat registry. The distinction between tool origins is captured in metadata, not in separate namespaces.

### Tool Metadata Schema

```json
{
  "name": "aws.s3.create_bucket",
  "version": "1.2.0",
  "origin": "builtin",
  "description": "Create an S3 bucket with versioning enabled",
  "category": "cloud.storage",
  "connector": "aws",
  "inputs": {
    "type": "object",
    "properties": {
      "bucket_name": { "type": "string", "required": true },
      "region": { "type": "string", "default": "us-east-1" },
      "versioning": { "type": "boolean", "default": true }
    }
  },
  "outputs": {
    "type": "object",
    "properties": {
      "bucket_arn": { "type": "string" },
      "bucket_url": { "type": "string" }
    }
  },
  "permissions": ["s3:CreateBucket", "s3:PutBucketVersioning"],
  "tags": ["s3", "aws", "storage"]
}
```

### Tool Categories (Initial Scope)

The initial scope focuses on tools for the agent's local working environment:

| Category | Examples |
|----------|----------|
| `file.read` | Read file contents |
| `file.write` | Write/create/overwrite files |
| `file.list` | List directory contents |
| `file.search` | Search files by pattern (glob) |
| `file.grep` | Search file contents by pattern |
| `code.edit` | Edit file contents (targeted changes) |
| `code.format` | Format code (prettier, black, etc.) |
| `code.lint` | Lint code (ruff, eslint, etc.) |
| `git.status` | Show git status |
| `git.diff` | Show changes |
| `git.commit` | Commit changes |
| `git.push` | Push to remote |
| `git.branch` | Branch operations |
| `shell.exec` | Execute shell command |
| `config.parse_yaml` | Parse YAML configuration |
| `config.parse_json` | Parse JSON configuration |
| `config.validate` | Validate configuration files |
| `terraform.plan` | Generate Terraform plan |
| `terraform.apply` | Apply Terraform changes |
| `terraform.state` | Inspect Terraform state |

### Deferred Categories

The following categories are explicitly out of scope for this working session but reserved for future implementation:

- `aws.*`, `gcp.*`, `azure.*` (cloud provider-specific tools)
- `ci_cd.*` (pipeline operations)
- `orchestration.*` (Kubernetes, ECS, etc.)
- `monitoring.*` (metrics, logs, alerts)
- `communication.*` (Slack, email, notifications)
- `secrets.*` (Vault operations)

### Tool Discovery & Selection

The agent discovers tools through:

1. **Registry queries:** Filter by category, connector, or tags
2. **Semantic search:** Natural language queries matched against tool descriptions
3. **Workflow context:** Tools declared in workflow definitions are pre-loaded

### Tool Execution Model

Tools execute through a standardized lifecycle:

```
Tool Request → Registry Lookup → Permission Check → Connector Resolution
    → Input Validation → Sandbox Setup → Execution → Output Processing → Cleanup
```

## Workflow Registry Design

### Flat Registry with Metadata Tags

Like tools, workflows are registered in a flat registry with metadata tags distinguishing their origin.

### Workflow Metadata Schema

```json
{
  "name": "provision_infrastructure",
  "version": "2.1.0",
  "origin": "builtin",
  "description": "Provision infrastructure from requirements document",
  "template_type": "provision",
  "inputs": {
    "type": "object",
    "properties": {
      "requirements_doc": { "type": "string", "required": true },
      "environment": { "type": "string", "enum": ["dev", "staging", "prod"] }
    }
  },
  "outputs": {
    "type": "object",
    "properties": {
      "infrastructure_arns": { "type": "array" },
      "validation_report": { "type": "string" }
    }
  },
  "required_tools": ["terraform.plan", "terraform.apply", "file.read"],
  "required_connectors": ["aws"],
  "permissions": ["terraform:plan", "terraform:apply"],
  "tags": ["infrastructure", "terraform", "aws"]
}
```

### Workflow Definition Format

Workflows are defined as conversational markdown documents with structured YAML frontmatter:

```yaml
---
name: Create Jira Ticket from Bug Report
version: 1.0.0
origin: builtin
template_type: custom
required_tools: ["jira.create_issue"]
required_connectors: ["jira"]
---

## Step 1: Validate Bug Report
Check that the bug description is complete and actionable...

## Step 2: Create Jira Ticket
Using the Jira connector, create a new issue with...
```

### Workflow Discovery & Selection

Users discover workflows through:

1. **Chat-based suggestions:** User describes their task, agent suggests matching workflows
2. **Browse/search interface:** Users browse workflow catalog or search for specific workflows
3. **Template list:** Built-in workflow templates displayed on the home page

## Sandbox Execution Model

Tool execution happens in isolated, ephemeral sandboxes to prevent side effects and ensure security. **One sandbox per workflow execution (session), not per tool invocation.**

### Sandbox Lifecycle

```
Workflow Start → Sandbox Creation → Tool Invocations (shared sandbox) → Workflow Complete → Sandbox Cleanup
```

### Benefits of Session-Lifetime Sandboxes

- **Performance:** Eliminates sandbox creation overhead for each tool call
- **State persistence:** Files, environment variables, and intermediate results persist within the session
- **Realistic for interactive workflows:** Engineers expect state to carry between steps
- **Matches DevOps patterns:** Similar to CI/CD runners that persist for the duration of a pipeline

### Sandbox Types

| Type | Use Case | Isolation Level |
|------|----------|-----------------|
| **Process sandbox** | Simple file/shell operations | OS process isolation |
| **Container sandbox** | Complex multi-step operations | Container runtime (Docker) |
| **VM sandbox** | High-security or multi-tenant | Virtual machine isolation |

### Sandbox Configuration

```json
{
  "type": "container",
  "image": "openship/agent-sandbox:latest",
  "mounts": [
    { "source": "/path/to/project", "target": "/workspace", "readOnly": false }
  ],
  "env": {
    "AWS_PROFILE": "openship",
    "GITHUB_TOKEN": "${secret:github_token}"
  },
  "network": "host"
}
```

### Working Directory

- Each sandbox has a working directory mounted from the project
- Files generated by tools are available in this directory
- Changes persist to the project directory after sandbox cleanup

### Sandbox Timeout & Cleanup

- Global workflow timeout (default 4 hours)
- Sandbox is automatically destroyed when timeout is reached
- User can manually resume the workflow at the exact paused step
- For parallel sub-agents, each sub-agent gets its own sandbox within the parent session

## Sub-Agent Execution Model

Workflows can spawn sub-agents for parallel or linear execution of complex tasks.

### Sub-Agent Types

| Type | Use Case |
|------|----------|
| **Linear sub-agent** | Sequential multi-step task (e.g., "validate then deploy") |
| **Parallel sub-agents** | Independent tasks that can run concurrently (e.g., "run tests on multiple branches") |
| **Specialist sub-agent** | Task requiring specific expertise (e.g., "security scan", "cost analysis") |

### Sub-Agent Spawning

```json
{
  "type": "parallel",
  "sub_agents": [
    {
      "name": "terraform_validate",
      "workflow": "validate_terraform",
      "inputs": { "config_dir": "/workspace/infra" }
    },
    {
      "name": "security_scan",
      "workflow": "security_scan",
      "inputs": { "target": "/workspace/src" }
    }
  ],
  "join_condition": "all_complete"
}
```

### Sub-Agent Communication

- Sub-agents report results back to the parent workflow
- Parent workflow can inspect sub-agent results and make decisions
- Sub-agents can share state through the workflow context

### Sub-Agent Sandboxing

Each sub-agent runs in its own sandbox, providing isolation, independent resource allocation, and clean failure recovery.

## Agent Orchestration Engine

The agent orchestrates workflow execution using a dynamic, context-aware model implemented in LangGraph.

### LangGraph as the Orchestration Substrate

LangGraph provides all the required primitives:

- **Graph execution:** Directed graph with nodes (steps) and edges (routing)
- **State management:** Built-in state schema and updates
- **Checkpointing:** Durable execution with resume capability
- **Human-in-the-loop:** `interrupt()` for pause/resume
- **Subgraphs:** Maps directly to sub-agent execution
- **Parallel execution:** Built-in for concurrent operations
- **Tool integration:** First-class LangChain tool support

### Dynamic Decision-Making

The "dynamic step sequencing" is implemented as routing nodes within the graph. At each decision point, a node runs the LLM to decide the next action:

```python
def decide_next_step(state):
    # LLM evaluates current state and decides next action
    response = llm.invoke(prompt.format(state))
    return {"next_step": response.action}

# Graph routing based on LLM decision
graph.add_conditional_edges(
    "execute_step",
    decide_next_step,
    {
        "continue": "next_step_node",
        "pause": "interrupt",
        "complete": END
    }
)
```

### Sub-Agents as Subgraphs

```python
# Parent workflow defines subgraph for sub-agent
terraform_subgraph = build_terraform_validation_workflow()
security_subgraph = build_security_scan_workflow()

# Parallel execution via graph routing
graph.add_edge("spawn_subagents", "terraform_validation")
graph.add_edge("spawn_subagents", "security_scan")
graph.add_edge("terraform_validation", "join_results")
graph.add_edge("security_scan", "join_results")
```

### Human-in-the-Loop Integration

The agent pauses at critical decision points:

- **Explicit pause points** defined in workflow definitions
- **Agent-initiated pauses** when uncertainty is high or unexpected results occur
- **Runtime interaction** where the agent asks questions during execution

### Error Handling & Recovery

The agent handles errors at multiple levels:

- **Tool-level errors:** Retry logic, fallback behavior
- **Step-level errors:** Skip, repeat, or reorder steps
- **Workflow-level errors:** Rollback, report, or request human intervention

## State Persistence & Storage

### SQLite3 as the Primary Store

- **Embedded database:** No separate server process, simple deployment
- **Durable checkpoints:** Workflow execution state persisted at each step
- **Transaction support:** Atomic state updates
- **ACID compliance:** Reliable crash recovery

### State Schema

```sql
CREATE TABLE workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'custom',
    definition TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workflow_executions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    status TEXT NOT NULL DEFAULT 'running',
    inputs TEXT,
    outputs TEXT,
    checkpoint_data BLOB,
    sandbox_id TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error TEXT
);

CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES workflow_executions(id),
    step_name TEXT NOT NULL,
    state_data BLOB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tools (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    origin TEXT NOT NULL,
    definition TEXT NOT NULL
);
```

### State Lifecycle

```
Workflow Start → Checkpoint → Step Execution → Checkpoint → ... → Completion
```

- **On start:** Create execution record, initial checkpoint
- **After each step:** Update checkpoint with new state
- **On pause:** Save checkpoint, update execution status to 'paused'
- **On resume:** Load checkpoint, continue execution
- **On completion:** Final checkpoint, update execution status to 'completed'

### Multi-User Considerations

For multi-user deployments (future), the architecture supports migrating to Postgres with the same schema design and query interface.

## CLI Interface Design

### Design Philosophy

Terminal-native, keyboard-friendly, low-latency interaction for engineers working in their shell.

### Key Features

- **Chat interaction:** Natural language conversation with the agent
- **Workflow commands:**
  - `openship workflow list` — List available workflows
  - `openship workflow run <name> [inputs]` — Execute a workflow
  - `openship workflow status <execution-id>` — Check execution status
- **Tool commands:**
  - `openship tool list` — List available tools
  - `openship tool describe <name>` — Show tool details
- **Session management:**
  - Resume paused workflows
  - View execution history
  - Approve/reject HITL prompts

### UX Considerations

- Minimal, clean output
- Keyboard shortcuts for common actions
- Real-time streaming of agent reasoning and tool execution
- Terminal colors and formatting for readability

## Web Interface Design

### Design Philosophy

Rich, visual experience with chat-first interaction, artifact inspection, and workflow management.

### Key Features

- **Chat interface:** Natural language conversation with the agent
- **Workflow management:** Browse, run, and manage workflows
- **Artifact inspector:** View and interact with generated documents, diagrams, code
- **Execution monitoring:** Real-time status of running workflows
- **Tool registry:** Browse and search available tools

### UX Considerations

- Chat-centric home page
- Inline tool call cards and approval cards in chat stream
- Artifact preview and editing within chat context
- Real-time streaming of agent activity

## Backend API

Both interfaces communicate with the backend through a REST API:

- `POST /workflows/run` — Start workflow execution
- `GET /workflows/status/:id` — Check execution status
- `POST /workflows/resume/:id` — Resume paused workflow
- `POST /chat/send` — Send chat message
- `GET /chat/messages/:execution-id` — Retrieve chat history
- `GET /tools/list` — List available tools
- `GET /workflows/list` — List available workflows

## Technology Stack

- **Backend:** Python, FastAPI, LangGraph, LangChain
- **Database:** SQLite3 (embedded), Postgres (future multi-user)
- **CLI:** Python with rich terminal output
- **Web:** Vue.js 3 with TypeScript, shadcn-vue component library
- **Container runtime:** Docker (for sandbox execution)

## Open Questions

1. **Workflow versioning:** How should workflow versions be managed? Semantic versioning? Git tags?
2. **Tool versioning:** Should tool versions be pinned in workflow definitions?
3. **Sandbox image management:** How should sandbox images be built, versioned, and distributed?
4. **Secrets management:** Should secrets be stored in SQLite or in a separate secrets vault?
5. **Multi-tenant isolation:** For future multi-user deployments, how should tenant isolation be implemented?

## Next Steps

This design is ready for implementation planning. The next phase involves:

1. Breaking down the design into implementation tasks
2. Estimating effort for each task
3. Identifying dependencies and critical path
4. Creating a detailed implementation plan