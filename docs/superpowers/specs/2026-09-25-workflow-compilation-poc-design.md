# Workflow Compilation PoC — Design

> **Status:** Approved
> **Date:** 2026-09-25
> **Author:** OpenShip Team

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
│       │   ├── agent.py        # Agent class (state management)
│       │   ├── parser.py       # parse_workflow() - extract inputs/steps/tools
│       │   ├── compiler.py     # compile_to_langgraph() - build graph
│       │   ├── executor.py     # execute_tool() - run commands
│       │   ├── llm_client.py   # LLMClient - OpenAI-compatible API
│       │   ├── cache.py        # GraphCache - checksum-based caching
│       │   └── cli.py          # main() - CLI entry point
│       ├── tests/
│       ├── examples/
│       └── pyproject.toml
├── apps/           # Graduated services (future)
├── packages/       # Shared modules (future)
├── docs/
└── README.md
```

### Agent Class

The `Agent` class manages the complete workflow lifecycle:

```python
class Agent:
    def __init__(self, llm_client, tool_registry, cache_dir):
        self.llm = llm_client
        self.tools = tool_registry
        self.cache = GraphCache(cache_dir)
        self.workflow = None
        self.graph = None

    def load_workflow(self, path):
        """Parse workflow.md, validate, extract inputs/steps/tools"""

    def compile_graph(self):
        """Compile parsed workflow to LangGraph. Check cache first."""

    def execute(self, inputs):
        """Run the compiled graph with provided inputs"""

    def approval_loop(self):
        """Ask user to approve plan [Yes/No]. Loop until approved."""
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

### Graph Caching

Compiled LangGraph instances are cached to disk:

- Cache key: SHA-256 checksum of workflow.md content
- Cache location: `.openship-poc-cache/`
- On load: compute checksum, check cache, recompile if missing or stale

### Execution Model

1. **Parse** workflow.md → extract inputs, steps, tool references
2. **Compile** to LangGraph → nodes for tools, edges for flow
3. **Approve** → present plan to user, wait for Yes/No
4. **Execute** → run the graph, capture outputs
5. **Report** → show results to user

### Error Handling

- Tool execution errors are reported and stop execution
- No retry or rollback for PoC
- Compilation errors are reported to user

### Testing Strategy

- Unit tests for parser, compiler, executor
- Integration tests for agent workflow
- Example workflow files for manual testing

## Scope

**In scope:**
- Markdown parsing with tool annotations
- LangGraph compilation and caching
- OpenAI-compatible LLM integration
- Shell and exec tool execution
- Human-in-the-loop approval
- CLI interface

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
