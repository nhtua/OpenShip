# OpenShip Agent CLI PoC

Proof of concept for an AI-powered DevOps workflow agent that compiles natural language workflow descriptions into executable LangGraph workflows with human-in-the-loop interaction.

## Architecture

The agent consists of two LangGraph workflows:

**Builder Workflow** (`builder.py`)
- Loads workflow markdown files
- Generates execution plans using LLM with tool selection
- Interactive approval loop (approve, revise, or provide feedback)
- Caches standardized workflows for deterministic execution

**Executor Workflow** (`executor.py`)
- Verifies workflow checksums against cached versions
- Compiles plans to LangGraph executable graphs
- Executes workflows with human-in-the-loop interrupts

**Shared Components**
- `compiler.py`: Compiles plan JSON to LangGraph StateGraph
- `tools.py`: Tool registry (shell.echo, shell.date, shell.xargs, exec.curl, user.ask)
- `tool_executor.py`: Executes tools with shell command safety
- `llm_client.py`: OpenAI-compatible API client
- `state.py`: Typed Pydantic state models

## Usage

### Build a Workflow

Standardize a workflow description and cache it:

```bash
openship build <workflow.md>
# Or with streaming LLM reasoning:
openship build <workflow.md> --show-thinking
```

The builder will:
1. Load the workflow file
2. Generate an execution plan with LLM
3. Present the plan for your approval
4. Loop until you approve (can request changes)
5. Save standardized version to `~/.openship/workflows/<checksum>.md`

### Execute a Workflow

Execute a previously built and cached workflow:

```bash
openship execute <workflow.md>
```

The executor will:
1. Compute the SHA-256 checksum of the workflow file
2. Look up the cached standardized version
3. Verify the checksums match (tamper detection)
4. Compile the plan to a LangGraph
5. Execute with human-in-the-loop interaction

## Workflow Format

Workflows can be specified in two styles:

**Explicit Steps**
```markdown
# Greeting Workflow

## Steps
1. Ask their name
   - tool: user.ask
   - args: What is your name?
2. Get current date
   - tool: shell.date
   - args: +"%Y-%m-%d"
3. Greet the user
   - tool: shell.echo
   - args: Hello {step_1}, today is {step_2}
```

**Freeform Description**
```markdown
Create a greeting workflow that asks the user for their name, gets today's date,
and then greets them with their name and the date.
```

The builder standardizes both formats into explicit step-by-step plans with tool selection.

## Available Tools

| Tool | Description | Args |
|------|-------------|------|
| `shell.echo` | Print text | Text to print |
| `shell.date` | Get date/time | Format string quoted |
| `shell.xargs` | Execute with piped input | Command |
| `exec.curl` | HTTP requests | URL and options |
| `user.ask` | Ask user a question | Question text |

## Template Resolution

Use `{step_N}` placeholders to pass data between steps:

```markdown
## Steps
1. Ask name
   - tool: user.ask
   - args: What is your name?
2. Greet
   - tool: shell.echo
   - args: Hello {step_1}!
```

## Testing

```bash
# Install dependencies
uv sync

# Run tests
uv run python -m pytest tests/ -v
```

## Examples

See `examples/` for sample workflow files:
- `my-workflow.md` - Explicit step format with user input
- `freeform-greeting.md` - Freeform natural language description

## Design Principles

- **Everything is a workflow**: The agent itself is a LangGraph workflow
- **Deterministic execution**: Cached standardized workflows ensure consistent results
- **Human-in-the-loop**: Interactive approval at key decision points
- **Tamper detection**: SHA-256 checksums verify workflow integrity
- **Tool safety**: Shell command quoting prevents injection attacks
# Test
