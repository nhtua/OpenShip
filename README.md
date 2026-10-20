# OpenShip

OpenShip is a self-hosted DevOps co-pilot accessed through a browser. Describe an outcome in conversation, follow durable runs and their results, and retain control over authorized work. Reusable workflows, tools, connectors, and isolated execution extend the same conversational harness as development progresses.

## Where to Start

- Read the [delivery roadmap](docs/roadmap.md) for delivered milestones and proposed next steps.
- Read the [harness architecture](docs/architects.md) for the current design direction and component boundaries.
- Run the API server, runtime worker, and web client below. Both backend processes share `apps/api`; the browser client lives in `apps/web`.

The [ideas collection](docs/ideas.md) preserves proposals and historical context. Entries marked **Stale** are superseded; use the architecture and roadmap to resolve conflicts.

## Current Status

**Roadmap Phases 1–3 are delivered:** authenticated project chat, a durable PostgreSQL-backed runtime with reconnectable events and cancellation, and explicit model-provider profiles. See the [provider support matrix](docs/provider-support.md) for qualified provider/model combinations.

**Phase 4 implementation is in acceptance review.** It adds independent Brave and Tavily search tools, model-directed OpenAI tool calls, and persistent conversation workspaces for immutable filesystem artifacts. The acceptance record identifies which PostgreSQL and synthetic full-stack checks have run and which still need a configured test database. Tools use the existing runtime worker; sandbox execution and mutable logical files stay in Phase 6. Connectors, reusable workflows, approvals, and broader deployment qualification remain later roadmap phases.

## How It Works

1. The **web client** provides the conversational workspace and sends authenticated HTTP commands to the API.
2. The **API server** validates commands, enforces project access, and persists conversations and durable run commands in PostgreSQL.
3. The **runtime worker** claims queued runs, invokes the pinned model profile or an admitted maintained tool, and persists operation and artifact results using the shared LangGraph runtime and durable state.
4. The client receives progress through **Server-Sent Events** and reconstructs state after reconnecting.

The target architecture extends these same contracts for workflow revisions, capability discovery, policy checks, artifacts, and a separate sandbox execution worker. The current chat runtime worker is not that future sandbox worker. Ordinary chat does not require a saved workflow template.

OpenShip follows the **co-pilot** principle: engineers delegate scoped authority and retain control at critical decisions. Self-hosting keeps application state in your infrastructure; configured model providers and external integrations receive the context sent to them.

## Quick Start with Docker Compose

From the repository root:

```bash
cp .env.example .env
# Set a strong JWT_SECRET and a unique stable ARTIFACT_STORAGE_ID.
# Add search keys and exact tool IDs only if you want to enable search.
docker compose run --rm --no-deps --entrypoint uv worker run python -m src.openship.artifacts.maintenance init
docker compose up --build
```

Compose starts PostgreSQL, migrations, the API server, runtime worker, and web client. The API and worker share the persistent `artifactdata` volume; ports bind to loopback by default. Search keys are passed only to the worker. Open [http://localhost:8080](http://localhost:8080). See [provider setup](docs/provider-support.md) before sending a chat message and the [artifact storage runbook](docs/operations/artifact-storage.md) for backup and restore. Docker with Compose is required for this route; the tools below are for running processes directly.

## Local Development

### Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/) package manager
- Node.js 22.12+ with [pnpm](https://pnpm.io/installation)
- PostgreSQL 15+ running locally

### Database Setup

Create the OpenShip database:

```bash
createdb -U postgres openship
```

### API Server and Runtime Worker (FastAPI / LangGraph)

```bash
cd apps/api
uv sync --extra dev
cp .env.example .env  # Configure DATABASE_URL, model profiles, and credential bindings
uv run alembic upgrade head
uv run python -m src.openship.artifacts.maintenance init
uv run uvicorn src.openship.main:app --reload --host 0.0.0.0 --port 8000
```

API docs available at [http://localhost:8000/docs](http://localhost:8000/docs).

Run the chat worker in a second terminal from `apps/api`:

```bash
uv run python -m src.openship.runs.worker
```

Chat uses explicitly declared model profiles and durable run commands. Configure
`MODEL_PROFILES_JSON` and `MODEL_DEFAULT_PROFILE_ID` in `.env`; credentials are
read from the bindings each profile names. See [provider setup](docs/provider-support.md).
The worker generates the conversation title from the first model response.
For search, set `TOOL_ENABLED_IDS` to `brave_web_search`,
`tavily_web_search`, or both, and configure only the selected tools' keys in the
worker environment. Search can be attached explicitly from the chat composer or
invoked by an enabled model profile. The Conversation files panel lists exact
saved artifact versions for reading, download, and deletion. The browser never
accepts provider credentials; see the [artifact storage runbook](docs/operations/artifact-storage.md)
for workspace retention and recovery.

### Web Client (Vue.js)

```bash
cd apps/web
pnpm install
pnpm dev
```

Frontend available at [http://localhost:5173](http://localhost:5173) (proxies API to :8000).

### Running Tests

```bash
# Backend
cd apps/api && uv run pytest tests/ -v -m "not postgres and not provider_live and not full_stack"

# Frontend
cd apps/web && pnpm exec vitest run
```

PostgreSQL integration tests require `TEST_POSTGRES_URL`; full-stack tests use synthetic search transports and require the same disposable database; live-provider tests are opt-in. See [Phase 4 acceptance](docs/testing/phase4-acceptance.md) and [provider support](docs/provider-support.md) for qualification details.

## Contributing

OpenShip is in its early days and we'd love your help shaping its future. Here's how to get involved:

- **⭐ Star the repo** — Let us know you're following the development
- **💬 Share feedback** — Join discussions on the project's design and features
- **🐛 Report issues** — Found a bug or have an improvement suggestion? Open an issue
- **📝 Contribute ideas** — Add your thoughts to [`docs/ideas.md`](docs/ideas.md)
- **🔨 Submit pull requests** — Help build features, fix bugs, or improve documentation

All contributions are welcome, no matter your experience level. We're building this together.

### Pre-commit Hooks

We use pre-commit hooks to enforce code quality before commits are created. The hooks run `ruff` linting, `pytest` (backend), and `pnpm typecheck` + `pnpm test:run` (frontend) on every commit.

To install:

```bash
# From repo root
pip install pre-commit
pre-commit install
```

To run hooks manually on all files:

```bash
pre-commit run --all-files
```

**Note:** For new worktrees, always run `pre-commit install` after creating the worktree to ensure hooks are active.

## Repository Layout

```text
openship/
├── apps/
│   ├── api/                # API server and durable runtime worker
│   └── web/                # Vue / TypeScript browser client
├── poc/
│   ├── agent/              # Experimental workflow CLI
│   └── mockup/             # Vue UI/UX demonstrations
├── docs/                   # Architecture, roadmap, ideas, and design records
├── docker-compose.yml      # PostgreSQL, migrations, API, worker, and web
├── .pre-commit-config.yaml
└── README.md
```

## Historical Prototypes

The [agent CLI](poc/agent/README.md) and [UI mockups](poc/mockup/README.md) are proof-of-concept experiments. They were never graduated into the application. Their examples demonstrate earlier approaches, not the supported server/worker/client entry point. See [poc/README.md](poc/README.md) for how to explore them.

## License

MIT License — see [LICENSE](LICENSE) for details.
