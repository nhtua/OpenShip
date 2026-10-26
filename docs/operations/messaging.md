---
type: runbook
summary: Proposed Phase 6A scripts and procedures for local, development Compose and production Compose messaging deployment, cutover and recovery.
status: proposed
date: 2026-10-25
---

# Messaging deployment and migration

**Proposal, not shipped tooling.** This runbook specifies what Phase 6A must
implement and qualify. At this revision the application still uses PostgreSQL
dispatch and SSE polling. `scripts/openship-stack`, `src.openship.messaging.cli`,
new Compose files, NATS configuration and new settings below do **not** yet
exist. Command blocks using them are the required future operator interface;
do not run them against a current installation.

Use the [design](../superpowers/specs/2026-10-25-phase6a-nats-messaging-design.md)
and [implementation plan](../superpowers/plans/2026-10-25-phase6a-nats-messaging.md)
for semantics and acceptance. The user selected a drained maintenance window.
The supported target is a developer workstation or single-host Docker Compose
stack, with separate development and production configurations. Kubernetes,
ECS and broker clustering are outside this delivery.

## Distribution and script contract

`scripts/openship-stack` runs from any directory, resolves the repository root,
validates configuration without printing secrets, and uses an explicit
environment. It never shells `source` on a dotenv file. Commands:

| Interface | Required behavior |
| --- | --- |
| `--environment ENV doctor` | ENV is `local`, `dev` or `prod`. Check tools, versions, config, identities/mounts, DB/broker resources, auth/ACL and readiness; nonzero exit and safe actionable codes on failure. |
| `... init-messaging` | Explicit first-time creation of private broker auth/config files, mode 0600 under mode 0700 directory. Refuse overwrite; never generate new existing Connector/storage/installation identities. |
| `... up-dependencies` | For local mode, start selected Compose PostgreSQL/NATS dependencies; allow `--only nats` when PostgreSQL is already managed locally. Never replace an existing local DB. |
| `... bootstrap --check` / `--apply` | Run a one-shot privileged messaging bootstrap, then remove that process. Check is read-only; apply creates missing compatible resources, refuses destructive drift. |
| `... start` / `stop` / `status` | Compose service lifecycle for dev/prod. Local start prints the separate native commands below; local stop/status operate only explicitly recorded managed dependencies, never kill arbitrary native processes. Stop preserves every volume. |
| `... cutover ACTION --cutover-id UUID` | ACTION is `preview`, `apply` or `verify`: read-only report, resumable bounded apply, then invariant checks. Apply requires admission closed and no old live owners. |
| `... admission close` / `open` | Gate new user submissions; active work may drain. Opening refuses unresolved migration/recovery. Old binaries do not honor this gate and must be stopped separately. |
| `... execution close` / `open` | Gate normal consumer pulls/new dispatch while already-owned work may drain with leases renewed. Opening refuses unresolved recovery. |
| `... rollback ACTION --cutover-id UUID` | ACTION is `preview` or `apply`. Validate and freeze the new epoch, account for in-flight work, retain additive DB schema; never deploy an old release automatically. |
| `... backup --output PATH` | Require stopped writers/consumers and a consistent cutover/recovery point; capture DB, artifacts, JetStream and non-secret version/identity manifest. Separate protected secret backup remains required. |
| `... restore --input PATH --verify-only` / `--apply` | Verify-only inspects without mutation. Apply requires stopped writers/consumers and restores to an explicitly empty recovery target; refuse overwrite of a running/nonempty target. Keep execution closed after restore. |
| `... reconcile --dry-run` / `--apply` | Compare broker/ledger/run/job/generation/dispatch state, inspect uncertain effects; apply only safe dispositions and emits a reviewable report. |
| `... rotate-auth --role ROLE` | Stage a new role credential, reload compatible broker config, roll that role's processes, verify reconnect, then explicitly retire the old credential; do not touch Connector keys. |

The wrapper delegates DB/broker operations to
`uv run python -m src.openship.messaging.cli` using the same subcommands
(`bootstrap`, `doctor`, `cutover`, `admission`, `execution`, `rollback`, `reconcile`).
`init-messaging`, dependency/lifecycle management, backup/restore and auth
rotation are wrapper orchestration. CLI reports contain counts, IDs, version
and safe dispositions only. Preview/check/verify-only/dry-run never write to
DB, broker or filesystem. Apply records progress so an interrupted rerun with
the same cutover ID resumes without duplicate commands. Nonzero exit stops the
script; it does not continue to opening admission after a failed step.

## Environment matrix

| Concern | Developer machine (`local`) | Development stack (`dev`) | Production stack (`prod`) |
| --- | --- | --- | --- |
| Processes | Native API, worker, relay and Vite; native or Compose DB/NATS. | Root `docker-compose.yml`; optional `docker-compose.dev.yml` hot-reload override. | Standalone `docker-compose.prod.yml`, explicitly selected; never combined with the dev file. |
| Environment | Existing `apps/api/.env`; optional root `.env` only for Compose dependencies. | Existing root `.env`. | Explicit protected `.env.prod`; require supplied production values. |
| Broker URL | Loopback, default port 4222. | Service DNS `nats:4222`. | Private service DNS with TLS validation; no published broker port. |
| Broker state | Explicit stable host directory, or existing Compose project `natsdata`. | New `natsdata` named volume in the existing Compose project. | Explicit stable production `natsdata`; existing production DB/artifact volumes are mapped deliberately. |
| Access | API/Vite/DB/broker bind loopback; dev credentials still required. | Existing loopback web/API/DB ports; broker/monitor loopback only if explicitly enabled. | Web ingress only, with documented HTTPS reverse proxy; DB, API, broker and monitoring private by default. |
| Artifacts/Connectors | Existing local paths and keyrings shared by API/worker. | Existing `artifactdata` and read-only `./secrets` mounts. | Same identities, protected volume and role-specific secret mounts; preserve all existing keys. |
| Build/reload | Existing `uv`/`pnpm` commands. | Build source; reload override mounts source and uses dev dependencies, not host virtualenv/node_modules. | Immutable qualified API/web/broker/tool image digests; no source mounts, build-on-start or reload. |
| Lifecycle | Supervise each native process; relay is one extra terminal/process. | Dependency health + migrate + messaging bootstrap before API/worker/relay ready. | Explicit one-shot migration/bootstrap, restart policies, bounded logs/resources and graceful stops. |

Do not change a Compose project name during an upgrade without mapping the
existing volumes explicitly. A newly named `pgdata` or `artifactdata` is an
empty installation, not a migration. Preserve `ARTIFACT_STORAGE_ID`, marker,
`CONNECTOR_INSTALLATION_ID`, keyrings and defaults. `NATS_NAMESPACE` must match
the installation manifest and must not be generated afresh at every start.

New non-secret settings and concurrency/backlog defaults are specified in the
design. `NATS_AUTH_FILE` selects each process's role-specific auth file; it
does not put a password in `NATS_SERVERS`. API, worker, relay and bootstrap
need different permissions. Bootstrap/admin credentials are not mounted into
long-running application services. Only API/worker receive existing Connector
keyrings; the relay and broker do not need them. Never log effective Compose
config or credential file contents into public CI artifacts.

## Developer machine startup (proposed)

Prerequisites remain Python/uv, supported Node/pnpm and PostgreSQL; add the
qualified NATS server or Docker Compose for the broker dependency. Pin versions
from `deploy/versions.env`, not an unqualified `latest` download. Native NATS
runs with `nats-server -c deploy/nats/local.conf`; the config refers to protected
auth files and a stable writable store directory. Choose native or Compose
broker, not both on the same port.

```bash
# From the repository root; first-time messaging setup only.
scripts/openship-stack --environment local init-messaging
# Optional: use only the broker container with an existing local PostgreSQL.
scripts/openship-stack --environment local up-dependencies --only nats
scripts/openship-stack --environment local bootstrap --apply
```

Continue to use the existing API dotenv file, database migrations and artifact
initialization for a fresh installation. For an existing installation use the
cutover procedure instead of initializing storage. Start separate terminals
from `apps/api`, selecting each role's protected auth path in the environment:

```bash
uv run uvicorn src.openship.main:app --reload --host 127.0.0.1 --port 8000
uv run python -m src.openship.runs.worker
uv run python -m src.openship.messaging.relay
```

Start the web client using its existing `pnpm dev` command. Keep model profiles,
Connector setup, tool enablement and local artifact configuration unchanged.
Run `doctor` after all processes start. It checks role connectivity and known
resources without making a live provider/search/AWS request.

## Development Compose startup (proposed)

Preserve `docker compose up --build` as the root quick-start command after
first-time setup. The root file adds persistent NATS, messaging bootstrap and
relay services; bootstrap is idempotent. Optional hot reload:

```bash
scripts/openship-stack --environment dev init-messaging
docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
scripts/openship-stack --environment dev doctor
```

The wrapper's default `dev start` selects the root file; `--reload` selects the
additional override. API and worker share the same model configuration and
artifact identity as today; NATS/relay must not introduce a second copy of
provider or Connector configuration. Existing storage initialization remains
explicit and runs only for a genuinely new empty artifact store.

## Production Compose startup (proposed)

Provide a production example file with required values rather than usable
sample passwords. Set explicit image digests, stable project/volume mappings,
strong DB/JWT credentials, broker TLS certificate/CA paths and role auth paths.
Use file-based secret inputs where supported by the application; do not
invent unsupported `*_FILE` environment settings for existing services.
Keep existing `MODEL_*`, provider bindings and Connector keyring provisioning.
The production wrapper rejects missing/sample secrets before starting.

```bash
scripts/openship-stack --environment prod init-messaging
docker compose --env-file .env.prod -f docker-compose.prod.yml config --quiet
scripts/openship-stack --environment prod start
scripts/openship-stack --environment prod doctor
```

Fresh `prod start` orders: PostgreSQL/NATS health → Alembic migration →
messaging bootstrap → relay/runtime/API readiness → web. Existing installation
upgrades remain gated by the cutover procedure. Wait for actual JetStream
stream/consumer/auth readiness, not just TCP or NATS `/healthz` liveness.
Use an image-available Python HTTP probe for the API and a pinned broker probe
tool; do not assume `curl` or shell tools exist in minimal images.

Set production restart policies, CPU/memory limits and bounded logs for each
long-running service. Worker shutdown stops new pulls, drains active runs
within their pinned deadlines and keeps renewing leases/progress; allow up to
the configured maximum deadline plus shutdown margin (not a fixed 180 seconds
when runs may last 1,800–3,600 seconds). Forced termination reconciles safely
and never claims completion. Stop relay after producers drain, NATS last.

Initial broker profile: one file-backed replica, 2 GiB file-store limit,
stream budgets from the spec, production disk sync `always` after release
qualification. Reserve host disk beyond broker limits for PostgreSQL,
artifacts, logs and snapshots. This profile tolerates process restarts; host or
disk loss requires recovery. It does not provide high availability. Disk-sync
configuration needs verification on the pinned server; see
[NATS sync settings](https://docs.nats.io/reference/config/jetstream/sync_interval).

## Existing installation: drained cutover (proposed)

Choose a unique cutover UUID and record it. Execute this in a rehearsal before
a real upgrade. Keep an immutable copy of the old release and configuration.

1. Record current commit/image digests, actual Compose project/volume names,
   artifact/Connector IDs and Alembic head. Run the read-only preflight against
   old-schema data; the preview command supports that schema without requiring
   new columns. Report duplicate jobs, retained events and run-state counts.
2. Block new submissions at ingress and stop all old API instances/native
   servers; the new DB gate cannot stop an old binary. Signal old workers to
   stop fetching and finish their current work. Wait up to pinned deadlines;
   inventory and reconcile any force-stopped runs using current dispatch
   rules. Preserve waiting Connector runs. Verify no old live worker/lease.
3. Back up PostgreSQL, artifacts and configuration at this stopped-writer
   point; protect existing Connector keys separately. No JetStream application
   state exists yet on the first migration. The resulting pre-cutover backup
   is the rollback baseline.
4. Apply additive migrations and persist admission/execution closed before
   any new worker starts. Provision NATS/auth/streams, verify resource budgets
   and ACLs. Do not initialize existing artifact storage.
5. Run the preview again on expanded schema. Apply in bounded batches using
   the same cutover ID; reruns must resume. Normalize legacy event notices,
   backfill missing retained event notifications and generate commands only
   for eligible queued undispatched runs. Terminal/waiting/uncertain work
   retains the spec's distinct disposition.
6. Start relay/API under closed user admission. Catch up publications and
   verify eligible generations, identities and retained cursors. Run the
   synthetic smoke run/maintenance dry-run in a separate disposable namespace
   with no live provider credentials. Normal command consumers do not pull
   while the execution gate is closed, avoiding delivery exhaustion.
7. Verify the report, then explicitly open execution/admission and web ingress.
   Observe the existing queued work, cancel/resume/reconnect, Connector and
   artifact journeys. Record counts and operator acceptance.

Required command grammar, shown with a documentation placeholder:

```text
scripts/openship-stack --environment ENV cutover preview --cutover-id UUID
scripts/openship-stack --environment ENV cutover apply --cutover-id UUID
scripts/openship-stack --environment ENV cutover verify --cutover-id UUID
scripts/openship-stack --environment ENV execution open
scripts/openship-stack --environment ENV admission open
```

The report includes scanned/eligible/converted/skipped/conflicting counts,
queued/waiting/terminal/uncertain run counts, event coverage and high-water
sequences, publication/disposition counts and unresolved IDs. Verification
fails on a queued eligible generation without a command, duplicate identity,
lost event reference, live old owner or unresolved unsafe dispatch.

## Rollback and broker outage (proposed)

Before opening the new release, rollback can resume the old runtime using
unchanged application rows and additive schema after verification. After new
execution starts, first close admission, stop fetching and drain/reconcile
active work. Stop new API/worker/relay processes and freeze the broker epoch.
`rollback preview` must account for every outstanding command; run/task kinds
the old release cannot understand must be finished or explicitly blocked.
Do not downgrade the database or delete broker state to force a green report.
Run `rollback apply`, deploy the old release and reopen ingress only when safe.

A broker outage does not cancel accepted runs. Running work may finish under
DB ownership; pending publications remain durable. Submission stays bounded
by 10,000 pending records / 300-second oldest pending age, then rejects new
work with a safe 503 before creating rows. Keep history/cancel/recovery usable.
Restore broker connectivity/storage/auth, run `doctor`, let relay catch up and
inspect quarantined/expired work. Never switch automatically to polling or
replay all messages. Full streams reject publication; inspect disk and resource
budgets instead of purging pending commands.

## Backup, restore and reconciliation (proposed)

Stop admission, all API/CLI writers and consumers, drain/reconcile workers,
flush the relay, then capture one coordinated backup set. Include PostgreSQL,
artifact bytes/marker, JetStream stream **and consumer** state, failure ledger,
pins/configuration/IDs/epoch and a manifest with hashes/counts/high-waters.
Use supported stream snapshots or a cleanly stopped full-store copy; never
tar a live store and call it a consistent backup. Back up protected NATS auth,
TLS material and existing Connector encryption/HMAC keys separately. Test
restoring this exact method; [NATS backup guidance](https://docs.nats.io/learn/backup-recovery/)
distinguishes state backups from identity material.

Restore into an isolated empty target with ingress, execution and publishers
disabled. Restore the matched DB/artifact/JetStream set and configuration,
then protected keys. Verify the artifact marker/digests and Connector
ciphertext with the existing maintenance commands. Preserve the existing
managed-dispatch lock, but also keep the new **global execution gate** closed:
the Connector lock alone does not stop model calls or plain chat.

Run reconciliation before consumers or publication resume. A database backup
may precede effects performed after it was taken; restored pending work is
not automatically safe to execute. Inspect dispatched/uncertain operations
and require a deliberate disposition. Only undispatched, currently eligible
generations may be re-enqueued. For broker-only loss, preserve the authoritative
DB and reconstruct safe pending delivery under a new epoch **and incremented
dispatch generations**; supersede old commands without modifying their
envelopes. A rerun with the same recovery ID resumes the same epoch/generations.
Never reset all `processed_at` flags. Old-epoch commands remain fenced out.

For the first production profile, require a coordinated backup before every
upgrade and at least daily, plus a restore rehearsal before acceptance.
Disaster recovery point is the last verified coordinated set, potentially
losing later committed DB/artifact data. Target recovery time is 60 minutes on
the declared qualification dataset/hardware; measure it, record actual time
and require review if missed. Process-restart recovery and disaster recovery
have different guarantees; neither promises exactly-once external effects.

## Monitoring and qualification

Expose safe health/readiness and counters for outbox oldest age/pending/errors,
relay connectivity/leadership, broker disk use, stream/consumer lag,
redeliveries/exhaustion, quarantine count, run/background slots, expired leases,
SSE overflow/replay and event latency. Alert before resource limits and on
stalled publication/exhaustion, with safe IDs rather than message bodies.

Record clean start/stop, upgrades and restart recovery in all three environments;
exercise two APIs, multiple workers, 50 synthetic conversations, background
isolation, ACL failure, broker-full behavior, interrupted cutover, rollback
and coordinated restore. Publish exact qualified version pins, latency/resource
measurements, backup counts and limitations in the Phase 6A qualification
record. These checks use synthetic external services; real provider/cloud
calls require separate explicit authorization.
