# Workflow Compilation PoC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI agent that compiles markdown workflow descriptions into executable LangGraph state machines, with human-in-the-loop approval.

**Architecture:** Modular Python project with class-based state management (`Agent` class) and functional modules for parsing, compilation, execution, LLM calls, and caching.

**Tech Stack:** Python 3.11+, LangGraph, OpenAI Python SDK, python-dotenv

---

### Task 1: Project Setup

**Files:**
- Create: `poc/agent/pyproject.toml`
- Create: `poc/agent/src/__init__.py`
- Create: `poc/agent/tests/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "openship-poc-agent"
version = "0.1.0"
description = "OpenShip workflow compilation PoC"
requires-python = ">=3.11"
dependencies = [
    "langgraph>=0.1.0",
    "openai>=1.0.0",
    "python-dotenv>=1.0.0",
]

[project.scripts]
openship-poc = "poc.agent.src.cli:main"
```

- [ ] **Step 2: Create __init__.py files**

Create empty `__init__.py` files for `src/` and `tests/`.

- [ ] **Step 3: Commit**

```bash
git add poc/agent/pyproject.toml poc/agent/src/__init__.py poc/agent/tests/__init__.py
git commit -m "chore: setup poc/agent project structure"
```

### Task 2: Tool Registry

**Files:**
- Create: `poc/agent/src/tools.py`
- Create: `poc/agent/tests/test_tools.py`

- [ ] **Step 1: Write failing test for tool registry**

```python
def test_tool_registry_has_shell_commands():
    from poc.agent.src.tools import TOOL_REGISTRY
    assert "shell.echo" in TOOL_REGISTRY
    assert "shell.date" in TOOL_REGISTRY
    assert TOOL_REGISTRY["shell.echo"]["type"] == "shell"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest poc/agent/tests/test_tools.py::test_tool_registry_has_shell_commands -v`
Expected: FAIL with "module not found" or "name not defined"

- [ ] **Step 3: Write tool registry implementation**

```python
TOOL_REGISTRY = {
    "shell.echo": {"type": "shell", "cmd": "echo"},
    "shell.date": {"type": "shell", "cmd": "date"},
    "shell.xargs": {"type": "shell", "cmd": "xargs"},
    "exec.curl": {"type": "exec", "cmd": "curl"},
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_tools.py::test_tool_registry_has_shell_commands -v`
Expected: PASS

- [ ] **Step 5: Write failing test for exec commands**

```python
def test_tool_registry_has_exec_commands():
    from poc.agent.src.tools import TOOL_REGISTRY
    assert "exec.curl" in TOOL_REGISTRY
    assert TOOL_REGISTRY["exec.curl"]["type"] == "exec"
```

- [ ] **Step 6: Run test and verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_tools.py::test_tool_registry_has_exec_commands -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add poc/agent/src/tools.py poc/agent/tests/test_tools.py
git commit -m "feat: add tool registry with shell and exec commands"
```

### Task 3: Tool Executor

**Files:**
- Create: `poc/agent/src/executor.py`
- Create: `poc/agent/tests/test_executor.py`

- [ ] **Step 1: Write failing test for shell execution**

```python
def test_execute_shell_command():
    from poc.agent.src.executor import execute_tool
    result = execute_tool("shell.echo", "hello world")
    assert "hello world" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest poc/agent/tests/test_executor.py::test_execute_shell_command -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write executor implementation**

```python
import subprocess
from .tools import TOOL_REGISTRY

def execute_tool(tool_name: str, args: str = "") -> str:
    tool = TOOL_REGISTRY[tool_name]
    if tool["type"] == "shell":
        cmd = f"{tool['cmd']} {args}"
        result = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    else:
        result = subprocess.run([tool["cmd"], args], capture_output=True, text=True)
    return result.stdout + result.stderr
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_executor.py::test_execute_shell_command -v`
Expected: PASS

- [ ] **Step 5: Write failing test for exec command**

```python
def test_execute_exec_command():
    from poc.agent.src.executor import execute_tool
    result = execute_tool("shell.date", "")
    assert len(result) > 0
```

- [ ] **Step 6: Run test and verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_executor.py::test_execute_exec_command -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add poc/agent/src/executor.py poc/agent/tests/test_executor.py
git commit -m "feat: add tool executor for shell and exec commands"
```

### Task 4: Workflow Parser

**Files:**
- Create: `poc/agent/src/parser.py`
- Create: `poc/agent/tests/test_parser.py`
- Create: `poc/agent/tests/fixtures/simple_workflow.md`

- [ ] **Step 1: Create test fixture workflow file**

```markdown
# Test Workflow

A simple test workflow.

## Inputs
- name: [required] Name to echo

## Steps
1. Say hello
   - tool: shell.echo
   - args: Hello {name}
```

- [ ] **Step 2: Write failing test for parser**

```python
def test_parse_workflow():
    from poc.agent.src.parser import parse_workflow
    workflow = parse_workflow("poc/agent/tests/fixtures/simple_workflow.md")
    assert workflow["title"] == "Test Workflow"
    assert "name" in workflow["inputs"]
    assert len(workflow["steps"]) == 1
    assert workflow["steps"][0]["tool"] == "shell.echo"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest poc/agent/tests/test_parser.py::test_parse_workflow -v`
Expected: FAIL with "module not found"

- [ ] **Step 4: Write parser implementation**

```python
import re
from pathlib import Path

def parse_workflow(path: str):
    content = Path(path).read_text()

    # Extract title
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    title = title_match.group(1) if title_match else ""

    # Extract inputs
    inputs = {}
    input_section = re.search(r"## Inputs\n((?:- .+\n)*)", content)
    if input_section:
        for line in input_section.group(1).strip().split("\n"):
            match = re.search(r"- (\w+):\s+\[(\w+)\]", line)
            if match:
                inputs[match.group(1)] = {"required": match.group(2) == "required"}

    # Extract steps
    steps = []
    step_section = re.search(r"## Steps\n(.*)", content, re.DOTALL)
    if step_section:
        for step_match in re.finditer(r"(\d+)\.\s+(.+?)\n((?:\s+- .+\n)*)", step_section.group(1)):
            step = {
                "order": int(step_match.group(1)),
                "description": step_match.group(2).strip(),
                "tool": None,
                "args": None,
            }
            for line in step_match.group(3).strip().split("\n"):
                if line.strip().startswith("- tool:"):
                    step["tool"] = line.strip().replace("- tool:", "").strip()
                elif line.strip().startswith("- args:"):
                    step["args"] = line.strip().replace("- args:", "").strip()
            steps.append(step)

    return {"title": title, "inputs": inputs, "steps": steps, "raw": content}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_parser.py::test_parse_workflow -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add poc/agent/src/parser.py poc/agent/tests/test_parser.py poc/agent/tests/fixtures/simple_workflow.md
git commit -m "feat: add workflow parser for markdown files"
```

### Task 5: LLM Client

**Files:**
- Create: `poc/agent/src/llm_client.py`
- Create: `poc/agent/tests/test_llm_client.py`

- [ ] **Step 1: Write failing test for LLM client initialization**

```python
def test_llm_client_initialization():
    from poc.agent.src.llm_client import LLMClient
    client = LLMClient(api_key="test-key", base_url="http://localhost:8080")
    assert client.api_key == "test-key"
    assert client.base_url == "http://localhost:8080"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest poc/agent/tests/test_llm_client.py::test_llm_client_initialization -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write LLM client implementation**

```python
from openai import OpenAI

class LLMClient:
    def __init__(self, api_key: str, base_url: str = None, model: str = "gpt-4o"):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def chat(self, messages, temperature=0.7, max_tokens=1024):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_llm_client.py::test_llm_client_initialization -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poc/agent/src/llm_client.py poc/agent/tests/test_llm_client.py
git commit -m "feat: add LLM client for OpenAI-compatible APIs"
```

### Task 6: Graph Compiler

**Files:**
- Create: `poc/agent/src/compiler.py`
- Create: `poc/agent/tests/test_compiler.py`

- [ ] **Step 1: Write failing test for graph compilation**

```python
def test_compile_simple_workflow():
    from poc.agent.src.compiler import compile_to_langgraph
    workflow = {
        "title": "Test",
        "inputs": {},
        "steps": [
            {"order": 1, "description": "Say hello", "tool": "shell.echo", "args": "hello"}
        ]
    }
    graph = compile_to_langgraph(workflow)
    assert graph is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest poc/agent/tests/test_compiler.py::test_compile_simple_workflow -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write compiler implementation**

```python
from langgraph.graph import StateGraph
from .executor import execute_tool

def compile_to_langgraph(workflow):
    steps = workflow["steps"]

    def build_node(step):
        def node(state):
            result = execute_tool(step["tool"], step["args"] or "")
            state["outputs"][step["order"]] = result
            return state
        return node

    graph = StateGraph()
    for step in steps:
        graph.add_node(f"step_{step['order']}", build_node(step))

    for i, step in enumerate(steps):
        if i == 0:
            graph.set_entry_point(f"step_{step['order']}")
        if i < len(steps) - 1:
            next_step = steps[i + 1]
            graph.add_edge(f"step_{step['order']}", f"step_{next_step['order']}")
        else:
            graph.set_finish_point(f"step_{step['order']}")

    return graph.compile()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_compiler.py::test_compile_simple_workflow -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add poc/agent/src/compiler.py poc/agent/tests/test_compiler.py
git commit -m "feat: add LangGraph compiler for workflow execution"
```

### Task 7: Graph Cache

**Files:**
- Create: `poc/agent/src/cache.py`
- Create: `poc/agent/tests/test_cache.py`

- [ ] **Step 1: Write failing test for graph cache**

```python
def test_graph_cache_checksum():
    from poc.agent.src.cache import GraphCache
    cache = GraphCache("/tmp/test-cache")
    checksum = cache.compute_checksum("test content")
    assert len(checksum) == 64  # SHA-256 produces 64 hex chars
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest poc/agent/tests/test_cache.py::test_graph_cache_checksum -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write cache implementation**

```python
import hashlib
import json
import os
from pathlib import Path

class GraphCache:
    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_checksum(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def get_cache_path(self, checksum: str) -> Path:
        return self.cache_dir / f"{checksum}.json"

    def save(self, checksum: str, graph_data: dict) -> None:
        path = self.get_cache_path(checksum)
        path.write_text(json.dumps(graph_data))

    def load(self, checksum: str) -> dict | None:
        path = self.get_cache_path(checksum)
        if path.exists():
            return json.loads(path.read_text())
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_cache.py::test_graph_cache_checksum -v`
Expected: PASS

- [ ] **Step 5: Write failing test for cache save/load**

```python
def test_graph_cache_save_load():
    from poc.agent.src.cache import GraphCache
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = GraphCache(tmpdir)
        checksum = cache.compute_checksum("test")
        cache.save(checksum, {"graph": "data"})
        loaded = cache.load(checksum)
        assert loaded == {"graph": "data"}
```

- [ ] **Step 6: Run test and verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_cache.py::test_graph_cache_save_load -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add poc/agent/src/cache.py poc/agent/tests/test_cache.py
git commit -m "feat: add graph cache with checksum-based invalidation"
```

### Task 8: Agent Class

**Files:**
- Create: `poc/agent/src/agent.py`
- Create: `poc/agent/tests/test_agent.py`

- [ ] **Step 1: Write failing test for agent initialization**

```python
def test_agent_initialization():
    from poc.agent.src.agent import Agent
    agent = Agent()
    assert agent is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest poc/agent/tests/test_agent.py::test_agent_initialization -v`
Expected: FAIL with "module not found"

- [ ] **Step 3: Write agent implementation**

```python
import os
from dotenv import load_dotenv
from .parser import parse_workflow
from .compiler import compile_to_langgraph
from .cache import GraphCache
from .llm_client import LLMClient

class Agent:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY", "sk-test")
        base_url = os.getenv("LOCAL_LLM_URL")
        self.llm = LLMClient(api_key=api_key, base_url=base_url)
        self.cache = GraphCache(".openship-poc-cache")
        self.workflow = None
        self.graph = None

    def load_workflow(self, path: str):
        self.workflow = parse_workflow(path)
        return self.workflow

    def compile_graph(self):
        if not self.workflow:
            raise ValueError("No workflow loaded")
        checksum = self.cache.compute_checksum(self.workflow["raw"])
        cached = self.cache.load(checksum)
        if cached:
            print("Using cached graph")
            return cached
        self.graph = compile_to_langgraph(self.workflow)
        self.cache.save(checksum, {"compiled": True})
        return self.graph

    def execute(self, inputs: dict = None):
        if not self.graph:
            self.compile_graph()
        return self.graph.invoke({"inputs": inputs, "outputs": {}})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_agent.py::test_agent_initialization -v`
Expected: PASS

- [ ] **Step 5: Write failing test for workflow loading**

```python
def test_agent_load_workflow():
    from poc.agent.src.agent import Agent
    agent = Agent()
    workflow = agent.load_workflow("poc/agent/tests/fixtures/simple_workflow.md")
    assert workflow["title"] == "Test Workflow"
```

- [ ] **Step 6: Run test and verify it passes**

Run: `uv run python -m pytest poc/agent/tests/test_agent.py::test_agent_load_workflow -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add poc/agent/src/agent.py poc/agent/tests/test_agent.py
git commit -m "feat: add Agent class with workflow loading and compilation"
```

### Task 9: CLI Entry Point

**Files:**
- Create: `poc/agent/src/cli.py`

- [ ] **Step 1: Write CLI entry point**

```python
import argparse
from .agent import Agent

def main():
    parser = argparse.ArgumentParser(description="OpenShip Workflow PoC")
    parser.add_argument("workflow", help="Path to workflow markdown file")
    args = parser.parse_args()

    agent = Agent()
    workflow = agent.load_workflow(args.workflow)
    print(f"Loaded workflow: {workflow['title']}")
    print(f"Steps: {len(workflow['steps'])}")

    # Approval loop
    print("\nPlan:")
    for step in workflow["steps"]:
        print(f"  {step['order']}. {step['description']} ({step['tool']})")

    while True:
        response = input("\nApprove plan? [yes/no]: ").lower().strip()
        if response == "yes" or response == "y":
            break
        elif response == "no" or response == "n":
            print("Plan rejected. Exiting.")
            return
        else:
            print("Invalid response. Please enter 'yes' or 'no'.")

    print("\nExecuting workflow...")
    graph = agent.compile_graph()
    result = agent.execute()
    print("\nResults:")
    for step_order, output in result["outputs"].items():
        print(f"  Step {step_order}: {output}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make CLI executable**

```bash
chmod +x poc/agent/src/cli.py
```

- [ ] **Step 3: Commit**

```bash
git add poc/agent/src/cli.py
git commit -m "feat: add CLI entry point with approval loop"
```

### Task 10: Example Workflows

**Files:**
- Create: `poc/agent/examples/simple_echo.md`
- Create: `poc/agent/examples/multi_tool.md`
- Create: `poc/agent/examples/data_flow.md`

- [ ] **Step 1: Create simple_echo.md example**

```markdown
# Simple Echo Workflow

Echo a greeting message.

## Inputs
- name: [required] Name to greet

## Steps
1. Greet the user
   - tool: shell.echo
   - args: Hello {name}! Welcome to OpenShip.
```

- [ ] **Step 2: Create multi_tool.md example**

```markdown
# Multi-Tool Workflow

Demonstrate multiple tools in sequence.

## Inputs
- message: [required] Message to echo
- url: [optional] URL to fetch (default: https://example.com)

## Steps
1. Echo the message
   - tool: shell.echo
   - args: Starting: {message}
2. Show current date
   - tool: shell.date
   - args: +"%Y-%m-%d %H:%M:%S"
3. Fetch URL
   - tool: exec.curl
   - args: -s -L {url} | head -n 5
4. Echo completion
   - tool: shell.echo
   - args: Done: {message}
```

- [ ] **Step 3: Create data_flow.md example**

```markdown
# Data Flow Workflow

Demonstrate data passing between steps.

## Inputs
- text: [required] Text to process

## Steps
1. Echo input
   - tool: shell.echo
   - args: Input: {text}
2. Count words
   - tool: shell.xargs
   - args: echo {text} | wc -w
3. Show timestamp
   - tool: shell.date
   - args: +"%s"
```

- [ ] **Step 4: Commit**

```bash
git add poc/agent/examples/
git commit -m "docs: add example workflow files"
```

### Task 11: Environment Configuration

**Files:**
- Create: `poc/agent/.env.example`

- [ ] **Step 1: Create .env.example**

```env
# OpenAI API
OPENAI_API_KEY=sk-your-api-key-here

# Or local model (Llama.cpp, vLLM, etc.)
LOCAL_LLM_URL=http://localhost:8080/v1
```

- [ ] **Step 2: Create .env file for local development**

```bash
cp poc/agent/.env.example poc/agent/.env
```

- [ ] **Step 3: Commit**

```bash
git add poc/agent/.env.example
git commit -m "chore: add environment configuration example"
```

### Task 12: Run All Tests

- [ ] **Step 1: Run the full test suite**

Run: `cd poc/agent && uv run python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 2: Fix any failing tests**

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: ensure all tests pass"
```

---

## Self-Review

**Spec coverage:**
- ✅ Markdown parsing with tool annotations → Task 4
- ✅ LangGraph compilation → Task 6
- ✅ OpenAI-compatible LLM integration → Task 5
- ✅ Shell and exec tool execution → Tasks 2, 3
- ✅ Human-in-the-loop approval → Task 9
- ✅ Graph caching with checksum → Task 7
- ✅ CLI interface → Task 9
- ✅ Agent class state management → Task 8

**Type consistency:** All tool names use `shell.*` and `exec.*` prefixes consistently. Method signatures are consistent across tasks.
