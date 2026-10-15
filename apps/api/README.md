# OpenShip API

Backend API server for OpenShip, built with FastAPI.

## Quick Start

```bash
cd apps/api
uv sync
cp .env.example .env
# Configure PostgreSQL, JWT_SECRET, model profiles, and credential bindings in .env.
uv run alembic upgrade head
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

## Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_PROFILES_JSON` | (required) | Explicit model profiles, endpoints, capabilities, and credential references |
| `MODEL_DEFAULT_PROFILE_ID` | (required) | ID of a declared profile to use by default |
| `OPENAI_API_KEY` | (unset) | Credential binding used by the example OpenAI profile |
| `MODEL_MAX_TOKENS` | `4000` | Default output token limit when a profile does not declare one |
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
