# OpenShip API

Backend API server for OpenShip, built with FastAPI.

## Quick Start

```bash
cd apps/api
uv sync
cp .env.example .env
# Configure PostgreSQL, JWT_SECRET, model profiles, and credential bindings in .env.
uv run alembic upgrade head
uv run python -m src.openship.artifacts.maintenance init
uv run uvicorn src.openship.main:app --host 0.0.0.0 --port 8000
```

Then visit `http://localhost:8000/api/health` to verify the server is running.

Start the chat worker in a second terminal from `apps/api`:

```bash
uv run python -m src.openship.runs.worker
```

## Configuration

Declare model profiles and select their default in `.env`. The example supplies
an OpenAI profile whose `credential_ref` points to `OPENAI_API_KEY`; set that
binding locally. Other profiles can use different bindings or no credentials.
See [provider configuration](../../docs/provider-support.md) for profile fields,
capabilities, and local provider examples.

Chat uses durable run submission and the worker for every provider. There is no
implicit OpenAI profile or Phase 1 chat endpoint.

Search is optional and remains two independent tools. Set `TOOL_ENABLED_IDS` to
`brave_web_search`, `tavily_web_search`, or both, and configure only the matching
worker credentials. A missing Brave key does not disable Tavily, and neither key
is entered or stored by the browser. Docker Compose passes search keys only to
the runtime worker. See the [artifact storage runbook](../../docs/operations/artifact-storage.md).

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_PROFILES_JSON` | (required) | Explicit model profiles, endpoints, capabilities, and credential references |
| `MODEL_DEFAULT_PROFILE_ID` | (required) | ID of a declared profile to use by default |
| `OPENAI_API_KEY` | (unset) | Credential binding used by the example OpenAI profile |
| `TOOL_ENABLED_IDS` | (empty) | Exact maintained tool IDs enabled for the runtime worker |
| `BRAVE_SEARCH_API_KEY` | (unset) | Operator credential for `brave_web_search` |
| `TAVILY_API_KEY` | (unset) | Operator credential for `tavily_web_search` |
| `ARTIFACT_ROOT` | `var/artifacts` | Shared persistent POSIX directory for immutable artifact bytes |
| `ARTIFACT_STORAGE_ID` | (required for artifacts) | Stable UUID identifying the mounted artifact store |
| `ARTIFACT_CONVERSATION_QUOTA_BYTES` | `134217728` | Maximum artifact bytes per conversation |
| `ARTIFACT_CONVERSATION_QUOTA_COUNT` | `100` | Maximum retained artifacts per conversation |
| `ARTIFACT_PROJECT_QUOTA_BYTES` | `1073741824` | Maximum artifact bytes per project |
| `ARTIFACT_PROJECT_QUOTA_COUNT` | `1000` | Maximum retained artifacts per project |
| `MODEL_MAX_TOKENS` | `4000` | Default output token limit when a profile does not declare one |
| `WORKFLOW_RUN_TIMEOUT_SECONDS` | `1800` | Overall execution deadline in seconds, including model requests, retries and tools; supported range 1–3600 |
| `MODEL_CONTEXT_TOKENS` | `128000` | Default declared context window; explicit smaller provider declarations still apply |
| `MODEL_MAX_CONTEXT_BYTES` | `2097152` | Independent UTF-8 request-size guard, including messages, tool schemas and results |
| `DATABASE_URL` | `postgresql://postgres:change_me@localhost:5432/openship` | Database connection string |
| `JWT_SECRET` | `dev-secret-change-in-production` | JWT signing secret |
| `JWT_EXPIRY_HOURS` | `24` | JWT token expiry |
| `HOST` | `0.0.0.0` | Server host |
| `PORT` | `8000` | Server port |
| `WORKER_LEASE_SECONDS` | `30` | Job lease duration |
| `WORKER_HEARTBEAT_INTERVAL` | `10` | Lease renewal interval |
| `WORKER_POLL_INTERVAL` | `2` | Queue poll interval |
| `RECONCILER_INTERVAL` | `60` | Lease reconciliation interval |
| `EVENT_RETENTION_DAYS` | `30` | Days to retain events for completed runs |
| `CHECKPOINT_RETENTION_DAYS` | `30` | Days to retain checkpoints for completed runs |
| `LOG_RETENTION_DAYS` | `7` | Days to retain operational logs |

Set the common budgets directly in `apps/api/.env` for local development, or
the root `.env` for Docker Compose:

```dotenv
WORKFLOW_RUN_TIMEOUT_SECONDS=1800
MODEL_CONTEXT_TOKENS=128000
MODEL_MAX_CONTEXT_BYTES=2097152
```

Restart both the API and worker after changing settings. New runs pin the new
values; queued or executing runs retain their submitted configuration. A
profile's explicit `limits.run_timeout_seconds` or `limits.max_context_bytes`
can impose a smaller ceiling. Individual model requests still use a 45-second
network timeout and tool operations keep their own timeout. Tool-enabled chat
still permits two tool calls and three model generation attempts per run.
When the remaining allowance only fits an answer, the next model request
disables further calls and uses the collected results. Retries consume the
same allowance. A provider that requests another tool despite that restriction
fails with `tool_limit_exceeded`, rather than a misleading time/token error.

The example profile omits `capabilities.context_tokens` so the simple
`MODEL_CONTEXT_TOKENS` setting supplies it. Existing profiles with a smaller
explicit value (for example, `8192`) keep that value: remove it once to inherit
the environment setting. The examples use a single-quoted multiline JSON value
so other profile options are easier to edit.

Context tokens and request bytes are different units. The current OpenAI
adapter enforces the byte guard locally and leaves actual token-window
validation to the provider; `MODEL_CONTEXT_TOKENS` is a declaration, not a
qualified local tokenizer. The 2-MiB guard accommodates larger requests without
claiming an exact byte-to-token conversion. No history is silently truncated.

Prompt caching uses OpenAI's enabled defaults for supported models. Cache
hits depend on matching reusable prefixes; caching does not increase the
context window. The adapter records provider-reported cached input tokens and
does not force model-specific retention options. See [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

## Health and Readiness

- `/health` — Basic liveness check
- `/api/health` — Health with database status
- `/ready` — Readiness check including worker backlog and stuck jobs

## Operations

### Retention Policy

OpenShip retains durable workspace data indefinitely by default. To bound history:

```bash
# Dry run — see what would be deleted
cd apps/api
uv run python -m src.openship.runs.worker_commands retention --dry-run

# Run retention (default: 30-day event retention, 30-day checkpoint retention)
uv run python -m src.openship.runs.worker_commands retention

# Custom retention periods
EVENT_RETENTION_DAYS=14 CHECKPOINT_RETENTION_DAYS=14 uv run python -m src.openship.runs.worker_commands retention
```

**Retention behavior:**
- Events from completed/failed/cancelled/timed_out runs older than `EVENT_RETENTION_DAYS` are pruned
- Checkpoints for terminal runs older than `CHECKPOINT_RETENTION_DAYS` are deleted via the checkpointer API
- Active, queued, and running runs are never pruned
- Messages and run summaries are preserved until an explicit delete policy is implemented
- Log retention is handled by the deployment log driver (7 days by default)

### Artifact Storage

The API and runtime worker must share the same persistent artifact directory and
`ARTIFACT_STORAGE_ID`. Initialize the root explicitly, verify retained bytes, or
recover stale staging/deleting operations with:

```bash
uv run python -m src.openship.artifacts.maintenance init
uv run python -m src.openship.artifacts.maintenance verify
uv run python -m src.openship.artifacts.maintenance retention --dry-run
uv run python -m src.openship.artifacts.maintenance retention
uv run python -m src.openship.artifacts.maintenance purge <workspace-id> --dry-run
uv run python -m src.openship.artifacts.maintenance verify-restored
```

The `init` command creates a marker only in an empty root. Keep the marker,
database, and artifact volume together in backups and restores. See the
[artifact storage runbook](../../docs/operations/artifact-storage.md) for
drain, upgrade, recovery, and future Phase 5 Connector guidance. The chat
client shows files owned by the current conversation and reads or deletes a
selected immutable version by exact artifact ID. `verify-restored` checks the
configured storage identity and file digests after a coordinated database and
filesystem restore; it does not recreate missing storage or replay a search.

### Lease Reconciliation

Reclaim expired leases from crashed workers:

```bash
uv run python -m src.openship.runs.worker_commands reconcile
```

### Health Check

Check API and database status:

```bash
uv run python -m src.openship.runs.worker_commands health
```

### Backup and Recovery

**Backup:**
```bash
# Dump PostgreSQL database
pg_dump -h postgres -U openship openship > openship-backup.sql
```

**Restore:**
```bash
# Restore from dump
psql -h postgres -U openship -d openship < openship-backup.sql
```

**Worker recovery:**
- Workers automatically recover from lease expiry via the reconciler
- Stale jobs are reclaimed and requeued or marked as failed
- No manual intervention required for normal failure modes

## Project Structure

```
apps/api/
├── pyproject.toml      # Project dependencies
├── alembic.ini         # Database migration config
├── README.md
└── src/
    └── openship/
        ├── __init__.py
        ├── main.py       # FastAPI application entry point
        └── config.py     # Settings from environment variables
```
