# OpenShip

An agentic DevOps workflow platform where everything is a workflow. OpenShip orchestrates multi-step automation tasks through AI-driven agents — from infrastructure provisioning and debugging to CI/CD and custom business processes. Describe what you need, collaborate with the agent through conversation, and watch it plan, execute, and report — with you in control at every step.

## Philosophy

OpenShip is built on a simple principle: **AI as co-pilot, not replacement**. You articulate the requirements, the agent handles the heavy lifting of design, validation, and execution. You retain control at critical decision points through human-in-the-loop approval cards.

Built by DevOps and platform engineers for software engineers — practical automation tools from people who understand the daily challenges.

**Self-hosted by design.** You own your data, you control the environment, you choose the AI model provider. No vendor lock-in, no data sent to third-party services unless you explicitly configure it.

## How It Works

In OpenShip, every automation task is expressed as a workflow — a sequence of steps executed by AI agents using tools and connectors. The system uses two LangGraph workflows that work together:

### Builder Workflow

The builder turns natural language descriptions into executable plans:

1. **Describe** — Provide a workflow description in markdown (freeform or structured)
2. **Plan** — The builder uses an LLM to generate a structured execution plan with tool selection
3. **Validate** — Review the plan through human-in-the-loop approval (approve, reject with feedback, regenerate)
4. **Compile** — The approved plan is compiled to a LangGraph state machine and cached

### Executor Workflow

The executor runs compiled workflows with human-in-the-loop at runtime:

1. **Load** — Load a cached compiled workflow by input file hash with integrity verification
2. **Execute** — Run the workflow steps, invoking tools (shell commands, executables, etc.)
3. **Interact** — Pause at runtime for human input when needed (e.g., asking questions)
4. **Report** — Return structured results for each step

### Integrity & Drift Detection

Compiled workflows are cached as JSON with filename-embedded signatures:
- **Lookup:** By input file hash (SHA-256 of workflow content)
- **Verification:** Signature binds input file and cached JSON together
- **Drift detection:** If either changes, the signature won't match

This architecture implements the "everything is a workflow" principle — the agent lifecycle itself (plan → approve → compile → execute) is modeled as LangGraph workflows, not just the user-defined tasks.

## Where to Start

**Read the ideas first.** Understand the project's philosophy, design principles, and roadmap by reading [`docs/ideas.md`](docs/ideas.md). This document captures the core vision, workflow patterns, and architectural decisions.

**Try the Agent.** Run our workflow agent:

```bash
cd apps/agent
uv sync  # Install dependencies

# Build: Generate plan, approve, compile, and cache
uv run python -m src.cli build examples/my-workflow.md

# Execute: Load cached compiled workflow and run
uv run python -m src.cli execute examples/my-workflow.md
```

See [`apps/agent/examples/`](apps/agent/examples/) for workflow examples.

**Explore the mockups.** See the intended user experience through our interactive mockups:

```bash
cd mockup
pnpm install
pnpm dev
```

Open `http://localhost:5173` to see the chat-first interface, agent workspace, and workflow builder designs. These are static demonstrations — no functionality yet.

## Open Source Roadmap

OpenShip is developed in transparent phases:

### 🔴 Current Phase: Addressing Problems & Brainstorming Solutions

We're actively defining the problem space and exploring solution approaches. This phase includes:

- Collecting and validating ideas from real DevOps pain points
- Designing the agent architecture and workflow orchestration
- Building UI/UX mockups to demonstrate concepts
- Selecting the technology stack and building core components

**What's available now:** Draft HTML mockups in the [`mockup/`](mockup/) directory that demonstrate the intended UI/UX and interaction patterns. These are static demonstrations — no functionality yet.

### Upcoming Phases (Planned)

- **Phase 2:** Core agent implementation with basic workflow execution
- **Phase 3:** Built-in workflow templates and tool registry
- **Phase 4:** Expanded tool/connector ecosystem and production deployment
- **Phase 5:** Community workflow sharing and ecosystem growth

## Local Development

### Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/) package manager
- Node.js 18+ with [pnpm](https://pnpm.io/installation)
- PostgreSQL 15+ running locally

### Database Setup

Create the OpenShip database:

```bash
createdb -U postgres openship
```

### Backend (FastAPI)

```bash
cd apps/api
uv sync
cp .env.example .env  # Edit to add OPENAI_API_KEY
uv run alembic upgrade head
uv run uvicorn src.openship.main:app --reload --host 0.0.0.0 --port 8000
```

API docs available at [http://localhost:8000/docs](http://localhost:8000/docs).

### Frontend (Vue.js)

```bash
cd apps/web
pnpm install
pnpm dev
```

Frontend available at [http://localhost:5173](http://localhost:5173) (proxies API to :8000).

### Docker Compose

Alternative: run the full stack in containers:

```bash
docker compose up --build
```

This starts PostgreSQL, API, and frontend services. Access at [http://localhost:8080](http://localhost:8080).

### Running Tests

```bash
# Backend
cd apps/api && uv run pytest tests/ -v

# Frontend
cd apps/web && pnpm exec vitest run
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
├── poc/
│   ├── mockup/             # UI/UX prototypes (static HTML)
│   └── PLACEHOLDER         # Keeps poc/ from being auto-removed
├── apps/
│   ├── web/                # Frontend app (Vue.js) — planned
│   ├── api/                # API service (FastAPI) — planned
│   └── agent/              # Agent service (graduated from PoC)
├── packages/
│   ├── shared/             # Shared Python modules — planned
│   └── ui/                 # Shared frontend components — planned
├── docs/                   # Documentation, ideas, and design specs
├── .pre-commit-config.yaml # Pre-commit hooks for lint and tests
└── README.md
```

## License

MIT License — see [LICENSE](LICENSE) for details.
