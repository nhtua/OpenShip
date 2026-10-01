# OpenShip

An agentic DevOps workflow platform where everything is a workflow. OpenShip orchestrates multi-step automation tasks through AI-driven agents — from infrastructure provisioning and debugging to CI/CD and custom business processes. Describe what you need, collaborate with the agent through conversation, and watch it plan, execute, and report — with you in control at every step.

## Philosophy

OpenShip is built on a simple principle: **AI as co-pilot, not replacement**. You articulate the requirements, the agent handles the heavy lifting of design, validation, and execution. You retain control at critical decision points through human-in-the-loop approval cards.

Built by DevOps and platform engineers for software engineers — practical automation tools from people who understand the daily challenges.

**Self-hosted by design.** You own your data, you control the environment, you choose the AI model provider. No vendor lock-in, no data sent to third-party services unless you explicitly configure it.

## How It Works

OpenShip uses a tool-centric runtime architecture with flat registries, sandboxed execution environments, and LangGraph-based orchestration.

### Architecture

```
CLI / Web UI
     ↓
FastAPI Backend (REST API)
     ↓
Orchestration Engine (LangGraph)
     ↓
Tool Registry + Workflow Registry (SQLite)
     ↓
Sandboxed Execution (process isolation)
```

- **Flat Registries**: Tools and workflows are registered without internal/exposed namespace splits. All tools are addressable by any workflow.
- **Sandboxed Execution**: Each workflow execution runs in a dedicated sandbox, enabling parallel execution and easy cleanup.
- **Human-in-the-Loop**: LangGraph's `interrupt` mechanism enables pause/resume at any step for human approval or input.
- **Conditional Branching**: Workflows can branch based on step results, enabling approval/rejection flows and decision logic.

### Workflow Execution

1. **Register** — Define tools and workflow templates in the registries
2. **Run** — Execute a workflow with input parameters
3. **Interact** — Pause at runtime for human approval or input
4. **Resume** — Continue execution after human decision
5. **Monitor** — Track execution status and results

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager

### Backend

```bash
cd apps/agent
uv sync  # Install dependencies
uv run python -m src.main  # Start FastAPI server on :8000
```

The API will be available at `http://localhost:8000/api`.

### CLI

```bash
cd apps/cli
uv sync
uv run python -m src.main --help
```

Run a workflow:

```bash
uv run python -m src.main workflow run my-workflow --inputs '{"param": "value"}'
```

Monitor status:

```bash
uv run python -m src.main workflow status <execution-id>
```

Resume paused execution:

```bash
uv run python -m src.main workflow resume <execution-id> --response '{"decision": "approve"}'
```

### Web Interface

```bash
cd apps/web
npm install
npm run dev  # Start dev server on :5173
```

The web app proxies API requests to the backend at `http://localhost:8000`.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSHIP_API_URL` | Backend API URL for CLI | `http://localhost:8000/api` |
| `OPENSHIP_DB_PATH` | SQLite database path | `:memory:` |
| `OPENAI_API_KEY` | OpenAI API key for LLM features | (required for LLM features) |
| `OPENAI_BASE_URL` | Custom OpenAI API base URL | (uses OpenAI default) |
| `OPENAI_MODEL` | OpenAI model to use | `gpt-4o` |

### Database

By default, OpenShip uses an in-memory SQLite database. For persistent storage, set `OPENSHIP_DB_PATH` to a file path:

```bash
export OPENSHIP_DB_PATH=/path/to/openship.db
```

### Tool Configuration

Tools can be configured through the CLI:

```bash
# List registered tools
uv run python -m src.main tool list

# Describe a specific tool
uv run python -m src.main tool describe file.read

# Search tools by category
uv run python -m src.main tool search --category "file"
```

Built-in tools include:
- `file.read` — Read file contents
- `file.write` — Write content to a file
- `shell.exec` — Execute shell commands

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/workflows/run` | Run a workflow |
| `GET` | `/api/workflows/status/:id` | Get execution status |
| `GET` | `/api/workflows` | List workflows |
| `GET` | `/api/workflows/search` | Search workflows by metadata |
| `POST` | `/api/workflows/resume/:id` | Resume paused execution |
| `GET` | `/api/tools` | List tools |
| `GET` | `/api/tools/search` | Search tools by metadata |
| `POST` | `/api/sandbox/create` | Create sandbox |
| `POST` | `/api/sandbox/execute` | Execute in sandbox |
| `DELETE` | `/api/sandbox/cleanup` | Cleanup sandbox |

## Where to Start

**Read the ideas first.** Understand the project's philosophy, design principles, and roadmap by reading [`docs/ideas.md`](docs/ideas.md).

**Try the CLI.** Run a workflow:

```bash
cd apps/cli
uv sync
uv run python -m src.main workflow list
```

**Explore the web interface.** Start the dev server:

```bash
cd apps/web
npm install
npm run dev
```

## Contributing

OpenShip is in its early days and we'd love your help shaping its future. Here's how to get involved:

- **⭐ Star the repo** — Let us know you're following the development
- **💬 Share feedback** — Join discussions on the project's design and features
- **🐛 Report issues** — Found a bug or have an improvement suggestion? Open an issue
- **📝 Contribute ideas** — Add your thoughts to [`docs/ideas.md`](docs/ideas.md)
- **🔨 Submit pull requests** — Help build features, fix bugs, or improve documentation

All contributions are welcome, no matter your experience level. We're building this together.

### Pre-commit Hooks

We use pre-commit hooks to enforce code quality before commits are created. The hooks run `ruff` linting and `pytest` on every commit.

To install:

```bash
# From repo root
apps/agent/.venv/bin/pre-commit install
```

To run hooks manually on all files:

```bash
apps/agent/.venv/bin/pre-commit run --all-files
```

## Repository Layout

```
openship/
├── apps/
│   ├── web/                # Frontend app (Vue.js 3)
│   ├── cli/                # CLI interface (Typer)
│   └── agent/              # Backend service (FastAPI + LangGraph)
├── docs/                   # Documentation, ideas, and design specs
├── mockup/                 # UI/UX prototypes (static HTML)
├── .pre-commit-config.yaml # Pre-commit hooks for lint and tests
└── README.md
```

## License

MIT License — see [LICENSE](LICENSE) for details.