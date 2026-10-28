# OpenShip

OpenShip is a self-hosted DevOps co-pilot accessed through a browser. Describe an outcome in conversation, follow durable runs and their results, and retain control over authorized work. Reusable workflows, tools, connectors, and isolated execution extend the same conversational harness as development progresses.

## Where to Start

- Read the [delivery roadmap](docs/roadmap.md) for delivered milestones and proposed next steps.
- Read the [harness architecture](docs/architects.md) for the current design direction and component boundaries.
- Run the API server, runtime worker, and web client below. Both backend processes share `apps/api`; the browser client lives in `apps/web`.

The [ideas collection](docs/ideas.md) preserves proposals and historical context. Entries marked **Stale** are superseded; use the architecture and roadmap to resolve conflicts.

## Current Status

**Roadmap Phases 1–5 are complete**, as confirmed by the project owner on 2026-10-25: authenticated project chat, durable runs/events, explicit model profiles, trusted tools and persistent artifacts, managed Connectors, and AWS basic-role discovery and bounded reads. Qualification claims remain scoped by the [provider support matrix](docs/provider-support.md) and phase-specific evidence.

**Phase 6A messaging is in progress on [PR #31](https://github.com/nhtua/OpenShip/pull/31).** The durable outbox/relay, JetStream run worker, API event fanout, read-only run/event cutover preview, resumable DB apply/verify, gate open/close controls, rollback preview/apply, and local/dev/prod deployment definitions are implemented. Native and Compose hot-reload workflows are documented. Full PostgreSQL/JetStream qualification, actual Compose builds and restart checks, and recovery exercises remain open. Do not run a production cutover yet. Read the [source review](docs/reviews/2026-10-25-phase6a-messaging-review.md), [design](docs/superpowers/specs/2026-10-25-phase6a-nats-messaging-design.md), [implementation plan](docs/superpowers/plans/2026-10-25-phase6a-nats-messaging.md) and [deployment runbook](docs/operations/messaging.md). Sandbox execution and mutable logical files remain Phase 6B.

## How It Works

1. The **web client** provides the conversational workspace and sends authenticated HTTP commands to the API.
2. The **API server** validates commands, enforces project access, and persists conversations and durable run commands in PostgreSQL.
3. The **runtime worker** claims queued runs, invokes the pinned model profile or an admitted maintained tool, and persists operation and artifact results using the shared LangGraph runtime and durable state.
4. The client receives progress through **Server-Sent Events** and reconstructs state after reconnecting.

The target architecture extends these same contracts for workflow revisions, capability discovery, policy checks, artifacts, and a separate sandbox execution worker. The current chat runtime worker is not that future sandbox worker. Ordinary chat does not require a saved workflow template.

OpenShip follows the **co-pilot** principle: engineers delegate scoped authority and retain control at critical decisions. Self-hosting keeps application state in your infrastructure; configured model providers and external integrations receive the context sent to them.

## Quick Start with Docker Compose or Podman

From the repository root:

```bash
cp .env.example .env
# Set a strong JWT_SECRET and a unique stable ARTIFACT_STORAGE_ID.
# Optional: set CONTAINER_ENGINE=podman in .env to use Podman Compose.
# Configure managed Connectors and tool availability after startup if needed.
scripts/openship-stack --environment dev init-messaging
scripts/openship-stack --environment dev init-artifacts
scripts/openship-stack --environment dev start
```

The stack starts PostgreSQL, migrations, NATS JetStream, NATS NUI, a one-shot messaging bootstrap, the API server, runtime worker, outbox relay, and web client in the separate `openship-dev` Compose project. On an inactive messaging installation, the first `scripts/openship-stack -e dev start` prints a read-only cutover preview and its Cutover ID, then exits. Confirm that exact preview with `scripts/openship-stack -e dev start --cutover <ID from preview>`, then run plain `start` again to verify relay catch-up and open dev gates. Containers reach PostgreSQL and NATS over the private Compose network using the `postgres` and `nats` service names. The API and web client publish on `DEV_BIND_ADDRESS` (default `0.0.0.0`) so developers can open the stack from another device on their LAN; set it to `127.0.0.1` for workstation-only access. NATS NUI is available at [http://localhost:31312](http://localhost:31312), bound to loopback, with a preloaded dev connection profile. The API and worker share the persistent `artifactdata` volume; search keys are passed only to the worker. Open [http://localhost:8080](http://localhost:8080). See [provider setup](docs/provider-support.md) before sending a chat message and the [artifact storage runbook](docs/operations/artifact-storage.md) for backup and restore. Use `scripts/openship-stack --environment dev --reload start` for the source-mounted hot-reload stack. The wrapper uses Docker by default and reads `CONTAINER_ENGINE=docker|podman` from `.env`; a non-empty shell environment value takes precedence. Podman requires a configured Compose provider. The tools below are for running processes directly.

For the full [messaging deployment runbook](docs/operations/messaging.md), see
the native startup, Docker/Podman selection, hot-reload, and queue cutover
steps. The separate production manifest is not a production cutover
qualification.

## Local Development

### Prerequisites

- Python 3.11+ with [uv](https://docs.astral.sh/uv/) package manager
- Node.js 22.12+ with [pnpm](https://pnpm.io/installation)
- PostgreSQL 15+ running locally
- The pinned NATS server from `deploy/versions.env`, or Docker/Podman Compose for a local NATS dependency

### Database Setup

Create the OpenShip database:

```bash
createdb -U postgres openship
```

### API Server and Runtime Worker (FastAPI / LangGraph)

```bash
cd apps/api
uv sync --extra dev
cp .env.example .env  # Configure DATABASE_URL, ARTIFACT_STORAGE_ID, model profiles, and credential bindings
uv run alembic upgrade head
# From the repository root, generate protected NATS role credentials and start
# either a local NATS server or the Compose-managed NATS dependency.
cd ../..
scripts/openship-stack --environment local init-messaging
scripts/openship-stack --environment local init-artifacts
scripts/openship-stack --environment local up-dependencies --only nats,nats-nui
scripts/openship-stack --environment local bootstrap --apply
cd apps/api
NATS_AUTH_FILE=../../.openship/messaging/local/api.json uv run uvicorn src.openship.main:app --reload --host 0.0.0.0 --port 8000
```

API docs available at [http://localhost:8000/docs](http://localhost:8000/docs).
The local dependency command starts NATS NUI at
[`http://localhost:31311`](http://localhost:31311) for JetStream inspection.
It imports a local connection profile for `nats://nats:4222`; click Connect
once in NUI. Startup stages the protected host profile into the NUI data volume
so the unprivileged NUI process can read it. The profile uses unrestricted
local operator credentials and is stored in that volume.

Run the chat worker and outbox relay in separate terminals from `apps/api`,
setting the role credential file generated by `init-messaging`:

```bash
NATS_AUTH_FILE=../../.openship/messaging/local/worker.json uv run python -m src.openship.runs.worker
NATS_AUTH_FILE=../../.openship/messaging/local/relay.json uv run python -m src.openship.messaging.relay
```

The API terminal uses `NATS_AUTH_FILE=../../.openship/messaging/local/api.json`.
Keep `NATS_SERVERS=nats://127.0.0.1:4222` in the API environment. The complete
native reload commands, Compose reload stack, and production configuration
requirements are documented in the [messaging runbook](docs/operations/messaging.md).

Chat uses explicitly declared model profiles and durable run commands. Configure
`MODEL_PROFILES_JSON` and `MODEL_DEFAULT_PROFILE_ID` in `.env`; credentials are
read from the bindings each profile names. See [provider setup](docs/provider-support.md).
The worker generates the conversation title from the first model response.
Enable installed tools in Global Settings and configure their Connectors on the
Connectors page. Search can be attached explicitly from the chat composer or
used by an enabled model profile. `TOOL_ENABLED_IDS` remains an optional
deployment ceiling for existing installations; see the [tool availability
runbook](docs/operations/tool-availability.md) before changing it. The
Conversation files panel lists exact saved artifact versions for reading,
download, and deletion. The browser never accepts provider credentials; see the
[artifact storage runbook](docs/operations/artifact-storage.md) for workspace
retention and recovery.

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
cd apps/api && uv run pytest tests/ -v -m "not postgres and not provider_live and not full_stack and not jetstream"

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
