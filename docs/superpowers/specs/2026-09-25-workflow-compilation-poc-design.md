---
type: spec
summary: Design specification for the workflow compilation PoC, demonstrating compilation of markdown workflows into executable LangGraph state machines.
status: approved
date: 2026-09-28
---

# Workflow Compilation PoC — Design

## Overview

This PoC demonstrates the ability to compile natural language workflow descriptions (markdown) into executable LangGraph state machines at runtime. The agent reads a workflow file, reasons about which tools to use, builds a dynamic LangGraph, presents the plan to the user for approval, and executes it.

**Goal:** Prove that dynamic LangGraph compilation from markdown workflows works, with human-in-the-loop approval.

## Architecture

### Directory Structure

```
openship/
├── poc/
│   └── agent/
│       ├── src/
│       │   ├── agent.py            # Agent class (state management & orchestration)
│       │   ├── builder.py          # Builder LangGraph workflow (plan generation & approval)
│       │   ├── executor.py         # Executor LangGraph workflow (compile & execute)
│       │   ├── compiler.py         # compile_to_langgraph() - build graph from plan
│       │   ├── tools.py            # Tool registry (shell/exec)
│       │   ├── llm_client.py       # LLMClient - OpenAI-compatible API
│       │   ├── state.py            # Typed state models (BuilderState, ExecutorState)
│       │   ├── workflow_cache.py   # JSON caching with integrity signature
│       │   └── cli.py              # CLI entry point (interface to Agent)
│       ├── tests/
│       ├── examples/
│       └── pyproject.toml
├── apps/           # Graduated services (future)
├── packages/       # Shared modules (future)
├── docs/
└── README.md
```

### Agent Class

The `Agent` class orchestrates the complete workflow lifecycle, encapsulating the builder and executor workflows:

```python
class Agent:
    def __init__(self):
        self.workflow = None
        self.graph = None
        self.plan = None
        self.builder = compile_builder_workflow()

    def load_workflow(self, path):
        """Load workflow markdown file."""

    def generate_plan(self, feedback=None):
        """Generate or regenerate execution plan using LLM. Returns plan JSON string."""

    def show_plan(self):
        """Display current plan to user."""

    def compile_graph(self, plan_json=None):
        """Compile plan to LangGraph. Uses self.plan if not provided."""

    def execute(self, inputs=None):
        """Execute compiled graph with human-in-the-loop. Returns results."""
```

### Builder Workflow (LangGraph)

The builder workflow uses LangGraph nodes with `interrupt()` for human-in-the-loop approval:

```python
builder_workflow = StateGraph(BuilderState)
builder_workflow.add_node("load_workflow", load_workflow_node)
builder_workflow.add_node("generate_plan", generate_plan_node)
builder_workflow.add_node("show_plan", show_plan_node)
builder_workflow.add_node("wait_for_approval", wait_for_approval_node)
# ... compile and save workflow
```

### Executor Workflow (LangGraph)

The executor workflow compiles and executes the workflow with `interrupt()` for runtime questions:

```python
executor_workflow = StateGraph(ExecutorState)
executor_workflow.add_node("compile", compile_node)
executor_workflow.add_node("execute", execute_node)
```

### Tool Registry

Tools are categorized by execution method:

```python
TOOL_REGISTRY = {
    # Shell commands (run via bash -c)
    "shell.echo": {"type": "shell", "cmd": "echo"},
    "shell.date": {"type": "shell", "cmd": "date"},
    "shell.xargs": {"type": "shell", "cmd": "xargs"},

    # Standalone executables (run directly)
    "exec.curl": {"type": "exec", "cmd": "curl"},
}
```

- **`shell.*`**: Unix shell commands (bash builtins or coreutils) — run via `subprocess.run(["bash", "-c", cmd])`
- **`exec.*`**: Standalone executables — run directly via `subprocess.run([cmd, args...])`

### Workflow Format

Workflows use markdown with structured annotations:

```markdown
# Workflow Title

Description of what this workflow does.

## Inputs
- name: [required/optional] Description
- value: [optional] Default value

## Steps
1. Step description
   - tool: shell.echo
   - args: {message}
2. Another step
   - tool: exec.curl
   - args: {url}
```

### LLM Client

The agent uses an OpenAI-compatible API:

- **Environment variables:**
  - `OPENAI_API_KEY` — OpenAI API key
  - `LOCAL_LLM_URL` — Local model URL (e.g., Llama.cpp)
- **Client:** OpenAI Python SDK with configurable base URL

### Workflow Caching & Integrity

Compiled workflows are cached as JSON with filename-embedded integrity signatures:

- **Cache location:** `~/.openship/workflows/`
- **Filename format:** `<input_hash>.<signature>.json`
  - `input_hash`: SHA-256 of input workflow file content
  - `signature`: SHA-256 of (input_content + json_cache_content)
- **Lookup:** Glob search by input hash to find cached file
- **Integrity check:** Recompute signature and verify match
- **Drift detection:** If either input or cache changes, signature won't match

This design provides:
- Exact workflow steps and tools without parsing text
- Integrity checking without extra storage
- Binds input file and cached JSON together

### Execution Model

1. **Parse** workflow.md → extract inputs, steps, tool references
2. **Compile** to LangGraph → nodes for tools, edges for flow
3. **Approve** → present plan to user, wait for Yes/No (interrupt)
4. **Execute** → run the graph, capture outputs (interrupt for runtime questions)
5. **Report** → show results to user

### Error Handling

- Tool execution errors are reported and stop execution
- No retry or rollback for PoC
- Compilation errors are reported to user

### Testing Strategy

- Unit tests for builder, executor, compiler, tools
- Integration tests for agent workflow
- Example workflow files for manual testing

## Scope

**In scope:**
- Markdown parsing with tool annotations
- LangGraph compilation and caching
- OpenAI-compatible LLM integration
- Shell and exec tool execution
- Human-in-the-loop approval (interrupt)
- CLI interface
- Workflow caching with integrity signature

**Out of scope:**
- Sandbox execution
- MCP tools or web API connectors
- Error retry/rollback
- Multi-step data flow (basic only)
- Frontend UI

## Dependencies

- `langgraph` — State machine orchestration
- `openai` — LLM API client
- `python-dotenv` — Environment variable loading