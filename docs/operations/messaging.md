---
type: runbook
summary: Phase 6A deployment, cutover, and recovery procedures for native local development and Docker or Podman Compose stacks.
status: implementation-in-progress
date: 2026-10-27
---

# Messaging deployment and migration

**Phase 6A implementation in progress; deployment files are available for
review and qualification.** The Python `cutover preview` command is a
read-only Phase 5/6A queue inventory; it reports run-state dispositions,
event-notification coverage, queue row totals and bounded issue IDs without
printing outbox payloads. `status=preview` means the classifier found no known
run, duplicate-job or event-reference blockers. It does not certify cutover
readiness because retention policy, live process state, distributed gate
coordination and apply-time rechecks are not certified by preview. The
resumable DB apply/verify, explicit execution/admission gate actions, and
resumable rollback preview/apply are implemented in the Python CLI. Closing
gates and applying rollback do not require broker access. The deployment
wrapper, local dependency manifest, development hot-reload override, and
standalone production manifest are implemented. Do not use them for a
production queue cutover until Task 8 qualification and the rehearsed recovery
gates are complete. Actual Docker build/cold-start/reload checks and Podman
provider compatibility checks remain pending.

Use the [design](../superpowers/specs/2026-10-25-phase6a-nats-messaging-design.md)
and [implementation plan](../superpowers/plans/2026-10-25-phase6a-nats-messaging.md)
for semantics and acceptance. The user selected a drained maintenance window.
The supported target is a developer workstation or single-host Docker/Podman
Compose stack, with separate development and production configurations.
Kubernetes, ECS and broker clustering are outside this delivery.

## Distribution and script contract

`scripts/openship-stack` runs from any directory, resolves the repository root,
uses an explicit environment, and never executes or sources a dotenv file. It
reads only the `CONTAINER_ENGINE` assignment for Compose selection from the
environment-specific dotenv file. A non-empty shell value takes precedence;
the default is `docker`, and accepted values are `docker` and `podman`.
Messaging credentials and generated NATS configs live under the ignored,
owner-only `.openship/messaging/ENV` directory, separately from the existing
Connector `./secrets` mount. `init-messaging` refuses any existing target. For
production it requires a TLS certificate, key, and trust CA. Commands:
Use `-e ENV` as shorthand for `--environment ENV`; for example,
`scripts/openship-stack -e dev start`.

| Interface | Required behavior |
| --- | --- |
| `--environment ENV doctor` | ENV is `local`, `dev` or `prod`. The wrapper requires `uv` for local operations, Docker Compose 2.24+ for Docker stacks, or Podman with a configured Compose provider; the CLI checks PostgreSQL, JetStream resources, auth and advisory capacity without printing secrets. |
| `... init-messaging [--tls-cert FILE --tls-key FILE --tls-ca FILE]` | First-time creation of role credentials and NATS server configs. TLS options are required for production and rejected for local/dev. JSON/config/TLS files are mode 0600 under a mode 0700 directory. Refuse overwrite; never generate Connector, artifact or database installation identities. |
| `... init-artifacts` | Explicitly initializes or verifies the configured artifact root. Local uses `uv` from `apps/api`; dev/prod use a one-shot `worker` container with the selected engine. The marker is created only in an empty root; an existing marker must match `ARTIFACT_STORAGE_ID`. Use for a new store only after confirming its volume mapping. |
| `... up-dependencies [--only SERVICE[,SERVICE...]]` | Local mode only. With no selector, starts PostgreSQL, NATS and NATS NUI under the stable `openship` project and records which services this wrapper started. Accepts comma-separated `postgres`, `nats` and `nats-nui`; `--only nats,nats-nui` starts NATS and NUI, and selecting `nats-nui` alone also starts its NATS dependency. NUI imports a protected local connection profile at startup; click Connect once in the UI. The command never deletes volumes. |
| `... bootstrap --check` / `--apply` | Run a one-shot privileged messaging bootstrap, then remove that process. Check is read-only; apply creates missing compatible resources, refuses destructive drift. |
| `... startup preview` / `startup apply --cutover-id UUID --dev-confirmed --writers-stopped` | Read-only startup-state classification and a gated dev cutover apply. The stable ID binds apply to the preview; the extra flag identifies disposable dev automation. Production operators use a separately created ID with `cutover preview` and `cutover apply`. |
| `... start [--cutover]` / `stop` / `status` | Compose lifecycle for dev/prod. Dev checks startup state after migration/bootstrap: inactive transport requires a read-only preview and explicit `start --cutover`; blocked previews are never bypassed. The confirmed command applies and exits; the next plain start catches up, verifies and opens gates before starting the full stack. Production never auto-applies; inactive transport refuses app startup, while a manually applied cutover may start with both gates closed for operator verification. Dev stop runs Compose `down` with both dev manifests, removing containers/network while preserving volumes; production stop keeps its existing stop behavior. Local start prints native commands; local stop/status operate only explicitly recorded managed dependencies. |
| `... cutover ACTION --cutover-id UUID` | ACTION is `preview`, `apply` or `verify`: read-only report, resumable bounded apply, then invariant checks. Apply requires admission closed and no old live owners. |
| `... admission close` / `open` | Gate new user submissions; active work may drain. Closing needs PostgreSQL only. Opening refuses unresolved migration/recovery. Old binaries do not honor this gate and must be stopped separately. |
| `... execution close` / `open` | Gate new execution claims while already-owned work may drain with leases renewed. Close admission first; closing needs PostgreSQL only. Opening refuses unresolved recovery and requires healthy JetStream topology. |
| `... rollback ACTION --cutover-id UUID` | ACTION is `preview` or `apply`. Apply requires operator identity, stopped API/worker/relay processes, drained or reconciled consumers, closed gates and caught-up command/maintenance publications. It freezes a new epoch, bumps queued generations in bounded resumable batches and retains additive DB schema; it never deploys an old release automatically. |
| `... reconcile --preview` / `--apply` | Preview retained `OS_RUNS` sources by exact installation subject; apply only with closed DB gates, paused/drained execution durables, and an operator identity. |

Compose commands use `docker compose` by default or `podman compose` when
`CONTAINER_ENGINE=podman` is set in the process environment or selected dotenv
file (`.env` for local/dev, `.env.prod` for prod). Docker requires Compose
2.24.0 or newer. Podman requires an installed Compose provider; the wrapper
checks `podman compose version` before invoking it. Podman documents
`podman compose` as a wrapper around an external provider such as
`docker-compose` or `podman-compose`, so install a provider that supports the
Compose features used by these manifests. The manifests and reload behavior
still need a full Podman qualification run.

The wrapper delegates DB/broker operations to
`uv run python -m src.openship.messaging.cli` using the same subcommands
(`bootstrap`, `doctor`, `startup`, `cutover`, `admission`, `execution`, `rollback`, `reconcile`).
`init-messaging` and dependency/lifecycle management are wrapper orchestration.
CLI reports contain counts, IDs, version
and safe dispositions only. Preview/check/verify-only/dry-run never write to
DB, broker or filesystem. Apply records progress so an interrupted rerun with
the same cutover ID resumes without duplicate commands. Nonzero exit stops the
script; it does not continue to opening admission after a failed step.

The wrapper does not implement backup/restore or credential rotation yet. Use
the repository's artifact/database backup procedures and the NATS operator
recovery process until a dedicated, reviewed implementation is added. Do not
interpret a command error as a partial or successful operation.

## Environment matrix

| Concern | Developer machine (`local`) | Development stack (`dev`) | Production stack (`prod`) |
| --- | --- | --- | --- |
| Processes | Native API, worker, relay and Vite; native or Compose DB/NATS. | Root `docker-compose.yml` through the selected engine; optional `docker-compose.dev.yml` hot-reload override. | Standalone `docker-compose.prod.yml` through the selected engine; never combined with the dev file. |
| Environment | Existing `apps/api/.env`; optional root `.env` selects a Compose engine for managed dependencies. | Root `.env` supplies settings and selects `CONTAINER_ENGINE` (`docker` by default). | Explicit protected `.env.prod`; may select `CONTAINER_ENGINE`; require supplied production values. |
| Broker URL | Loopback, default port 4222. | Service DNS `nats:4222`. | Private service DNS with TLS validation; no published broker port. |
| Broker state | Explicit stable host directory, or existing Compose project `natsdata`. | Separate `openship-dev_natsdata` volume. | Explicit stable production `natsdata`; existing production DB/artifact volumes are mapped deliberately. |
| Access | API/Vite/DB/broker and optional NATS NUI bind loopback; dev credentials still required. | API/web publish on `DEV_BIND_ADDRESS` (default `0.0.0.0`); NUI binds loopback on port 31312; PostgreSQL and NATS stay private on Compose DNS. | Web ingress only, bound to host loopback for a host reverse proxy; DB, API, broker and monitoring private by default. |
| Artifacts/Connectors | Existing local paths and keyrings shared by API/worker. | Separate `openship-dev_artifactdata` volume and read-only `./secrets` mounts. | Same identities, protected volume and role-specific secret mounts; preserve all existing keys. |
| Build/reload | Existing `uv`/`pnpm` commands. | Build source; reload override mounts source and uses dev dependencies, not host virtualenv/node_modules. | Immutable API/web/PostgreSQL/NATS image digests; no source mounts, build-on-start or reload. |
| Lifecycle | Start NATS/DB, apply migrations/bootstrap, then supervise API, worker, relay and web as separate processes. | PostgreSQL health + migration + authenticated JetStream bootstrap before API/worker/relay; API readiness checks worker health. | PostgreSQL health + one-shot migration and bootstrap, restart policies, bounded logs/resources and graceful stops. |

The local dependency project is `openship`; the dev stack uses the separate
`openship-dev` project so both environments do not try to manage containers
with the same names. Production explicitly maps the legacy `openship_pgdata`
and `openship_artifactdata` volume names and adds `openship_natsdata`. The dev
project therefore has separate `openship-dev_*` volumes. Existing volumes
under the previous shared `openship` project remain intact and are not migrated
or deleted. The first start of the separated dev project uses a new database,
artifact and NATS volume; copy or restore data explicitly if the previous
shared-project data is needed. Verify actual volume names before an upgrade. A
newly named volume is an empty installation, not a migration. Preserve
`ARTIFACT_STORAGE_ID`, marker, `CONNECTOR_INSTALLATION_ID`, keyrings and
defaults. Runtime NATS subjects use the database installation identity; do not
generate another namespace at every start.

New non-secret settings and concurrency/backlog defaults are specified in the
design. `NATS_AUTH_FILE` selects each process's role-specific auth file; it
does not put a password in `NATS_SERVERS`. API, worker, relay and bootstrap
need different permissions. The full NATS server config contains every user
password and is mounted only into NATS. API, worker and relay receive only
their own credential file. The operator credential is mounted only into the
one-shot bootstrap/doctor container. The operator user is unrestricted, so
keep that file owner-only and never mount it into application containers. API
and worker receive existing Connector keyrings through their separate
`./secrets` mount; relay and broker do not. ACL subjects currently use a
single-installation wildcard, so dedicate one broker to one OpenShip
installation. The relay can publish its namespaced outbox subjects and read
stream/consumer metadata for the configured streams and durables so it can
refuse startup on missing or drifted topology; it cannot create or mutate
JetStream resources. Never log effective Compose config or credential file
contents into public CI artifacts.

The local dependency Compose file and dev stack run [NATS NUI](https://github.com/nats-nui/nui)
on loopback: local uses `http://127.0.0.1:${NATS_NUI_PORT:-31311}` and dev uses
`http://127.0.0.1:${DEV_NATS_NUI_PORT:-31312}`. Each Compose project gets its
own persistent NUI data volume. The local `up-dependencies` command and dev
`start` generate a protected NATS CLI context for `nats://nats:4222` from that
environment's operator credentials and mount the context directory read-only.
The startup wrapper stages the profile in NUI's writable data volume before the
image drops to its unprivileged user, so the host copy can keep restrictive
permissions. NUI imports the profile at startup; open the UI and click Connect
once. The profile contains unrestricted operator credentials, and NUI stores
it in its persistent `natsnui_data` volume. NUI can modify streams and delete
messages, so keep it on a trusted workstation. NUI documents this CLI-context
startup option in its [connection import guide](https://natsnui.app/help/).

For worker health checks, the API role may publish only to
`openship.I.v1.rpc.worker.health` and subscribe to its installation's reply
subjects. Runtime workers subscribe only to that installation's health request
subject and publish only to its health reply subjects. The API mints a unique
reply subject for each request and accepts only a matching response; no broad
`_INBOX.>` permission is required for this RPC. A worker that cannot currently
admit a run stays silent, and the API reports unavailable within two seconds.

## Qualified transport versions

The server image and Python client are pinned in `deploy/versions.env` and
`apps/api/uv.lock`. The selected client is `nats-py` 2.15.0; the required
qualification lane runs it against Python 3.11, 3.12 and 3.13. The official
NATS CLI pin and platform checksums are recorded there for operator tooling.
The declared Docker Compose floor is 2.24.0; production and dev files are
qualified against that interface before the stack is released.

Run the authenticated real-broker transport checks from `apps/api`:

```bash
uv run python scripts/qualify_jetstream.py
```

The launcher uses the pinned server image, generates disposable credentials in
a private temporary directory, requires all JetStream tests to execute without
skips, and removes the broker and credentials when it exits. JetStream
provides at-least-once delivery, so application ownership fences and duplicate
handling remain required; see the [NATS JetStream contract](https://docs.nats.io/concepts/jetstream).

## Shared execution resources and broker primitives

The Python messaging CLI implements `bootstrap --check`, `bootstrap --apply`,
`doctor`, resumable cutover and rollback, explicit gate controls, and the
bounded exhaustion `reconcile --preview` / `--apply` flow.
Bootstrap checks are read-only. Bootstrap apply creates missing resources and
reports existing drift without rewriting streams or consumers. Readiness includes
`run-v1` on `OS_RUNS`, `maintenance-v1` on `OS_MAINTENANCE`, and `exhaustion-v1`
on `OS_ADVISORIES`. The execution durables are filtered to this installation's
exact command subjects. `exhaustion-v1` is filtered to
`$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.OS_RUNS.run-v1`; it captures
MaxDeliver notices so a worker restart does not lose exhaustion observations.
All three use explicit ACK, instant replay, persistent state and one replica.
The run and maintenance consumers use MaxDeliver 10; the advisory observer
uses unlimited redelivery. All use a 30-second AckWait with no BackOff or
inactivity deletion.

`OS_ADVISORIES` is a 64 MiB, no-age-expiry work-queue stream with `DiscardNew`.
Its durable consumer must be bound before the worker binds `run-v1`; worker
health remains unavailable if that observer exits. Monitor its stored bytes
and messages. The exhaustion supervisor emits `advisory_capacity_alert` when
stored bytes reach 48 MiB (75% of the configured limit),
`advisory_capacity_full` at the byte limit, and
`advisory_capacity_recovered` after usage falls below 75%. `doctor` includes an
`advisory_capacity` JSON section and exits nonzero at or above the alert
threshold. Route the structured worker log event through the deployment's
existing log alerting before production cutover. At the threshold, close
admission and execution with the operator gate, drain active work, then let
`exhaustion-v1` drain the advisory backlog. Resume only after the stream is
below the alert threshold and the recovery report is complete.

At 64 MiB, `DiscardNew` can reject new advisory capture. The corresponding
`OS_RUNS` source remains stored. The `reconcile --preview` tool scans retained
run-subject sources up through the `run-v1` delivered high-water mark in bounded
batches and reports exact sequence, timestamp and body digest without exposing
the command body. These are reconciliation candidates: JetStream's consumer
info has aggregate counters and cannot establish an exact per-message delivery
count. Preview is read-only.

Use the reviewed recovery procedure after a capacity stop:

1. Keep admission closed. Close execution, pause both `run-v1` and
   `maintenance-v1`, and drain active work until both consumers report zero
   pending acknowledgements. Stop any old process that does not honor these
   gates. Preserve both streams and their durables.
2. Run `reconcile --preview` and retain its JSON report. For a large backlog,
   review each bounded batch and continue with the emitted
   `next_after_sequence` until `truncated` is false.
3. Apply the reviewed batch with `reconcile --apply --operator IDENTIFIER`.
   Apply refuses an inactive transport, open DB gates, unpaused consumers, or
   any in-flight execution acknowledgement. It stores
   `discovery_method=capacity_scan`, the operator identity, and an unknown
   delivery count (`0`) in PostgreSQL. It then delegates disposition to the
   existing fenced exhaustion state machine. It never treats the scan as an
   authoritative MaxDeliver count or auto-quarantines an unbound command.
4. Keep execution closed if the report contains `blocked`, `settling`, or
   `manual_review_unbound_source` outcomes. Resolve those cases through the
   existing operator recovery path and repeat the preview. Resume only when
   every candidate is resolved, the advisory observer has drained the
   retained advisory backlog, and `doctor` reports capacity below 75%.

The scan is deliberately bounded to 500 sources per invocation; the CLI default
is 100. A truncated apply is incomplete and returns nonzero so the operator
continues from the reported cursor. A later received MaxDeliver advisory can
upgrade an operator-discovered row with its real diagnostic count without
overwriting who discovered it. Do not clear either stream or recreate its
durable as a repair. Phase 6A remains blocked from production cutover until the
capacity alert, real-broker scan contract, recovery procedure and full
PostgreSQL/JetStream qualification pass. Do not deploy or cut over while the
stream is at the alert threshold.

Do not delete advisory messages or exhausted sources manually.

The runtime role can retrieve and delete an exact source sequence only in
`OS_RUNS`; it cannot retrieve or delete messages in another stream. Exhaustion
cleanup first verifies the committed run/outbox state or committed failure
notice, then rechecks the original subject, JetStream timestamp and body digest
before deleting that exact sequence. If state is uncertain or those fields do
not match, the source stays in `OS_RUNS` for recovery or operator inspection.

| Setting | Default | Allowed range |
| --- | --- | --- |
| `WORKER_RUN_CONCURRENCY` | 4 | 1–128 |
| `WORKER_BACKGROUND_CONCURRENCY` | 1 | 1–128 |
| `MESSAGING_RUN_MAX_ACK_PENDING` | 4 | 1–1024 |
| `MESSAGING_MAINTENANCE_MAX_ACK_PENDING` | 1 | 1–1024 |

Local concurrency cannot exceed its corresponding aggregate. Each consumer's
MaxAckPending and MaxWaiting equal its declared aggregate. Multiple processes
must budget their combined capacity explicitly; changing the declaration
reports drift and requires a separate reviewed capacity update. Bootstrap does
not resize existing consumers or discard pending/ACK progress.

`messaging.gates.pause_execution_consumers(js)` requests the explicit
`2100-01-01T00:00:00Z` deadline on both consumers; this is a deadline, not an
indefinite setting. `resume_execution_consumers(js)` uses the client's explicit
resume API. Results include each confirmed state and deadline; timeouts and
partial failures remain incomplete, without automatic rollback. Bootstrap
preserves existing pauses. Runtime credentials cannot pause/resume or mutate
shared topology; those permissions belong to operator/bootstrap credentials.

These primitives do not change database gates. Task 5 must commit pending
operation records before sends, serialize and conflict-fence gate operations,
and resolve unknown broker outcomes before any conflicting action. Never await
NATS while holding an application transaction. The later runtime must read
`active` and `execution_open` before fetching. Broker restart persistence of
pause and the complete gate ordering protocol remain later qualification.

## Explicit retention maintenance

The worker maintenance CLI preserves direct retention execution for recovery:

```bash
cd apps/api
uv run python -m src.openship.runs.worker_commands retention --dry-run
uv run python -m src.openship.runs.worker_commands retention
```

After 3B2f switches and qualifies the production worker entry, queue-backed
retention requests use the dedicated `OS_MAINTENANCE` stream and
`maintenance-v1` durable:

```bash
uv run python -m src.openship.runs.worker_commands enqueue-retention \
  --requested-by "operator@example.test" --dry-run
uv run python -m src.openship.runs.worker_commands retention-status <task-id>
```

Do not enqueue these tasks before the JetStream worker entry is active. The
command refuses an inactive transport; it does not silently run the task
directly. The PostgreSQL task row and reference-only outbox command are
committed atomically. `requested_by`, retention periods, and result data remain
in PostgreSQL; the broker envelope carries only the task UUID. Tasks persist
`requested`, `running`, `completed`, or `failed` state. The worker commits
`running` before pruning and the bounded result or fixed safe failure code
before ACK. Before sending ACK, it records the exact JetStream stream and
consumer sequences in PostgreSQL. After JetStream confirms, it records
`consumer_acked_at`. If that PostgreSQL write fails after the broker accepted
the ACK, the worker compares the saved sequences with the durable consumer ACK
floor and records confirmation once that floor has passed them. A task and its
delivery digest remain until that confirmation has aged past the retention
window. Redelivery of a terminal task is an ACK-only no-op; an interrupted
`running` task can safely repeat its idempotent bounded pruning pass under the
task row lock.

The defaults are 30 days for event, checkpoint and messaging history and 1,000
rows per table/category per task. Override `MESSAGING_RETENTION_DAYS` and
`MESSAGING_RETENTION_BATCH_SIZE` only with reviewed values in the protected
service environment. A result with `limit_reached: true` means additional
eligible records remain; enqueue another task after reviewing the prior result.
Conversation messages and run summaries remain untouched. Never remove
unprocessed event/failure notices, requested/running tasks, active-run
dispositions, or unresolved exhaustion observations.

## Developer machine startup

Prerequisites remain Python/uv, supported Node/pnpm and PostgreSQL; add the
qualified NATS server or Docker Compose/Podman Compose for the broker
dependency. Pin versions from `deploy/versions.env`, not an unqualified
`latest` download. The initialization script generates `server.local.json`
with a loopback listener and a stable store path. Choose native or Compose
NATS, not both on the same port. For Compose dependencies, the root `.env`
selects `CONTAINER_ENGINE`; otherwise the wrapper uses Docker.

```bash
# Prepare the native API environment and apply the existing DB migrations.
cd apps/api
uv sync --extra dev
cp .env.example .env
# Edit DATABASE_URL, ARTIFACT_STORAGE_ID, model profiles, and provider bindings.
uv run alembic upgrade head
cd ../..

# From the repository root; first-time messaging setup.
scripts/openship-stack --environment local init-messaging
scripts/openship-stack --environment local init-artifacts
# Compose-managed NATS and NUI; omit this line if using native NATS instead.
scripts/openship-stack --environment local up-dependencies --only nats-nui
# After the selected NATS server is ready, create JetStream resources.
scripts/openship-stack --environment local bootstrap --apply
```

For native NATS, start this command in a separate terminal instead of running
`up-dependencies --only nats-nui`. The Compose-managed NUI service is available
only with the Compose-managed NATS service; it is not started alongside a
native NATS process.

```bash
nats-server -c .openship/messaging/local/server.local.json
```

Do not run both NATS options on the same port. For Compose dependencies, the
wrapper records which services it started; `stop` only stops those services
and leaves volumes in place. The `apps/api/.env` file supplies
`DATABASE_URL`, model settings and the stable `ARTIFACT_STORAGE_ID`.
`init-artifacts` runs the maintenance initializer from `apps/api`; it
initializes an empty root or verifies the existing storage marker without
replacing it. `NATS_AUTH_FILE` is selected per process below; `NATS_SERVERS`
defaults to loopback.

For source development, keep the current native hot-reload workflow. Run each
process from its existing package directory; the API terminals use the API's
installed development dependencies, including `watchmedo`:

```bash
# apps/web
pnpm run dev --host 0.0.0.0

# apps/api — worker
NATS_AUTH_FILE=../../.openship/messaging/local/worker.json \
uv run watchmedo auto-restart --patterns="*.py" --recursive -- \
  python -m src.openship.runs.worker --log-level DEBUG

# apps/api — outbox relay
NATS_AUTH_FILE=../../.openship/messaging/local/relay.json \
uv run watchmedo auto-restart --patterns="*.py" --recursive -- \
  python -m src.openship.messaging.relay

# apps/api — API
NATS_AUTH_FILE=../../.openship/messaging/local/api.json \
uv run uvicorn src.openship.main:app --reload --host 0.0.0.0 \
  --port 8000 --log-level debug
```

The relay uses its own publish-only NATS role and is watched like the worker.
These commands preserve the native Vite, Uvicorn, worker, and relay reload
workflow. Bind the broker and PostgreSQL to loopback when they run natively.

Continue to use the existing API dotenv file and database migrations. For an
existing installation, use the cutover procedure below instead of changing the
artifact storage identity. Start separate terminals from `apps/api`, selecting
each role's protected auth path in the environment:

```bash
NATS_AUTH_FILE=../../.openship/messaging/local/api.json uv run uvicorn src.openship.main:app --host 127.0.0.1 --port 8000
NATS_AUTH_FILE=../../.openship/messaging/local/worker.json uv run python -m src.openship.runs.worker
NATS_AUTH_FILE=../../.openship/messaging/local/relay.json uv run python -m src.openship.messaging.relay
```

Start the web client using its existing `pnpm dev` command. Keep model profiles,
Connector setup, tool enablement and local artifact configuration unchanged.
Run `doctor` after all processes start. It checks role connectivity and known
resources without making a live provider/search/AWS request.

## Development Compose startup

From the repository root, prepare `.env` and run the wrapper commands. Set
`CONTAINER_ENGINE=podman` in `.env` to use Podman; the default is Docker. You
can override the file setting for one command with
`CONTAINER_ENGINE=podman scripts/openship-stack ...`. `init-messaging` creates
the protected NATS credentials. `init-artifacts` initializes a new empty
`artifactdata` volume or verifies that its marker matches the stable ID.

```bash
cp .env.example .env
# Set a strong JWT_SECRET and a stable ARTIFACT_STORAGE_ID in .env.
# Optionally set CONTAINER_ENGINE=podman in .env.
scripts/openship-stack --environment dev init-messaging
scripts/openship-stack --environment dev init-artifacts
scripts/openship-stack --environment dev start
scripts/openship-stack --environment dev doctor
```

The root file adds persistent PostgreSQL, NATS, NATS NUI, messaging bootstrap
and relay services. `start` generates the protected dev NUI context, brings up
PostgreSQL/NATS, runs migration and bootstrap as one-shot commands, then checks
the persisted messaging state. On an inactive installation, the first plain
`start` prints a read-only cutover preview and stable ID, then exits without
starting API, worker, relay or web. If the preview is unblocked, explicitly
confirm with `start --cutover`; that invocation stops any running Compose app
writers. Stop native or separately managed API/worker/relay processes outside
this Compose project that point at the same database; the wrapper cannot
supervise them. It then
rechecks and applies, then exits with instructions to run plain
`start` again. The next plain start runs relay under closed gates, waits for
cutover verification, opens execution then admission, and starts the full
stack. A blocked preview is never force-applied; follow the manual recovery
procedure below. Keep any such native processes stopped until this sequence
finishes. These steps stay ordered with Podman Compose as well as Docker
Compose. Use the source-mounted hot-reload stack when working on Python or web
source:

```bash
scripts/openship-stack --environment dev --reload start
scripts/openship-stack --environment dev doctor
```

The same cutover gate applies with or without `--reload`. Stop the development
stack with `scripts/openship-stack -e dev stop`; this uses both Compose files
even if the preceding start omitted `--reload`, removes containers and network,
and preserves named volumes.

The default `start` builds the root quick-start stack without source mounts.
Add `--reload` before `start` to use the development override. It mounts
Python/web source read-only, keeps
container-installed dependencies in the image or a named node_modules volume,
runs Uvicorn reload, watches worker and relay Python files, and runs Vite on
port 5173 with `/api` proxied to `api:8000`. Dependency/lockfile changes need a
rebuild. API and worker preserve the existing model configuration, artifact
volume and Connector secret mounts. Messaging credentials are mounted one
role at a time from `.openship`, not through Connector `./secrets`. Existing
artifact storage initialization remains explicit: `init-artifacts` refuses a
non-empty root without its marker or a marker whose value differs from
`ARTIFACT_STORAGE_ID`. For an existing store, keep its current ID and volume.

The web client proxies `/api` to the API using the internal `api:8000` service
name, in both Vite reload mode and the built Nginx image. PostgreSQL and NATS
are likewise private to the Compose network. The API and web host bindings use
`DEV_BIND_ADDRESS` (default `0.0.0.0`) for LAN access; set it to `127.0.0.1` in
`.env` to restrict them to the developer machine. Open the web app at
`http://localhost:5173` with `--reload`, or `http://localhost:8080` with the
default stack. NATS NUI is at `http://localhost:31312`; its preloaded `OpenShip
Dev` profile needs one click on Connect. The API docs are at
`http://localhost:8000/docs`. Use the same
ports with the developer machine's LAN address to access the stack from
another LAN device. Use the wrapper's `status` and `stop` commands to inspect
and stop services without deleting volumes:

```bash
scripts/openship-stack --environment dev status
scripts/openship-stack --environment dev stop
```

Run the drained cutover procedure below against disposable development data to
exercise PostgreSQL-to-JetStream queue migration and recovery. The startup flow
does not bypass a blocked preview or delete existing PostgreSQL, artifact or
JetStream data.

## Production Compose startup (not yet qualified)

Copy `.env.prod.example` to mode `0600` `.env.prod`, replace every example,
and publish immutable API/web/PostgreSQL images. The NATS server reference is
pinned in `deploy/versions.env`. Preserve the current Compose project and volume
mapping (`openship_pgdata`, `openship_artifactdata`) and provide existing
artifact and Connector identities/keys. Use a production NATS certificate
whose SAN covers the Compose service DNS name `nats`, and provide the issuing
CA. The wrapper validates secrets/image digests without printing their values.
Set `CONTAINER_ENGINE=podman` in the protected `.env.prod` to use Podman; Docker
is the default.
`init-messaging` copies the TLS files into the ignored protected directory and
generates TLS-enabled server configuration with `sync_interval: always`.

```bash
cp .env.prod.example .env.prod
chmod 600 .env.prod
# Edit .env.prod and set qualified immutable API, web, and PostgreSQL images.
scripts/openship-stack --environment prod init-messaging \
  --tls-cert /path/to/server.crt --tls-key /path/to/server.key \
  --tls-ca /path/to/ca.crt
# Only for a deliberately new, empty artifact volume after confirming its mapping:
scripts/openship-stack --environment prod init-artifacts
scripts/openship-stack --environment prod start
scripts/openship-stack --environment prod doctor
```

`prod start` brings up PostgreSQL and NATS, runs Alembic migration and
authenticated JetStream bootstrap as one-shot commands, then checks startup
state. It starts API/worker/relay and web only when transport is already active
with both gates open, or after an operator has applied cutover and left both
gates closed; it never applies, opens gates or restores ingress automatically.
An inactive transport refuses application-service startup and points to the
manual procedure below. API readiness uses Python's standard HTTP client and
checks event subscription plus worker RPC. The one-shot bootstrap checks the
real JetStream topology; TCP reachability alone is not readiness. Existing
installation upgrades still require the drained cutover procedure. Production
binds web only to host loopback for a host HTTPS reverse proxy; it publishes no
API, database, NATS, or monitoring port.

Run `init-artifacts` only when provisioning a new, confirmed-empty production
artifact volume. For an existing installation, preserve the mapped volume and
its current `ARTIFACT_STORAGE_ID`; a missing marker is a storage recovery issue,
not a reason to initialize a replacement store.

Set production restart policies, CPU/memory limits and bounded logs for each
long-running service. Worker shutdown stops new pulls, drains active runs
within their pinned deadlines and keeps renewing leases/progress; allow up to
the configured maximum deadline plus shutdown margin (not a fixed 180 seconds
when runs may last 1,800–3,600 seconds). Forced termination reconciles safely
and never claims completion. Stop relay after producers drain, NATS last.

Initial broker profile: one file-backed replica, 2 GiB file-store limit,
stream budgets from the spec, and disk sync `always`. Reserve host disk beyond broker limits for PostgreSQL,
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
   retains the spec's distinct disposition. The current apply requires the
   expanded schema including migration `phase6a020`, no preview blockers, no
   active retention task, ready JetStream topology, and an explicit
   `--writers-stopped` attestation. It activates the Phase 6A transport with
   both DB gates closed and commits each event/run batch with its progress
   cursor. A failed process resumes with the same cutover ID.
6. Start relay/API under closed user admission. Catch up publications and
   verify eligible generations, identities and retained cursors. Run the
   synthetic smoke run/maintenance dry-run in a separate disposable namespace
   with no live provider credentials. Normal command consumers do not pull
   while the execution gate is closed, avoiding delivery exhaustion.
7. Verify the report, then explicitly open execution/admission and web ingress.
   Observe the existing queued work, cancel/resume/reconnect, Connector and
   artifact journeys. Record counts and operator acceptance.

### Production command sequence

Use this sequence only after the production cutover has been rehearsed and
accepted. Keep ingress blocked and old writers stopped through preview, apply
and verification. Generate a unique UUID yourself and record it with the
change record; production does not use the dev startup ID or auto-apply path.

```bash
# Create and record a unique ID. Then block ingress, stop every old API,
# worker and relay, and take the stopped-writer backup.
scripts/openship-stack -e prod cutover preview --cutover-id <CUTOVER_UUID>
# This first preview examines the legacy schema and is read-only.

# Apply additive migrations and bootstrap NATS. On an inactive installation
# this intentionally exits before application services start.
scripts/openship-stack -e prod start

# Preview again against the expanded Phase 6A schema. Resolve every blocker
# and rerun until status=preview.
scripts/openship-stack -e prod cutover preview --cutover-id <CUTOVER_UUID>
scripts/openship-stack -e prod cutover apply --cutover-id <CUTOVER_UUID> \
  --operator <OPERATOR_ID> --writers-stopped

# Starts the new services with admission and execution still closed.
scripts/openship-stack -e prod start
scripts/openship-stack -e prod cutover verify --cutover-id <CUTOVER_UUID>
scripts/openship-stack -e prod execution open --cutover-id <CUTOVER_UUID>
scripts/openship-stack -e prod admission open --cutover-id <CUTOVER_UUID>
# Restore ingress only after both gate operations succeed.
```

`cutover apply` is bounded and resumable: if it stops partway, rerun it with
the same UUID after confirming writers remain stopped. Do not open either gate
until verification succeeds; open execution before admission. If preview is
blocked, resolve the reported classifications using the reconciliation
guidance below, then preview again. Never bypass a blocked report or delete
JetStream/PostgreSQL state to force startup. `prod start` refuses an inactive
transport and does not open gates; after apply it starts app services only
while both gates are closed so the relay can catch up before the operator
verifies.

Required command grammar, shown with a documentation placeholder:

```text
scripts/openship-stack --environment ENV cutover preview --cutover-id UUID
scripts/openship-stack --environment ENV cutover apply --cutover-id UUID --operator IDENTIFIER --writers-stopped
scripts/openship-stack --environment ENV cutover verify --cutover-id UUID
scripts/openship-stack --environment ENV admission close --cutover-id UUID
scripts/openship-stack --environment ENV execution close --cutover-id UUID
scripts/openship-stack --environment ENV execution open --cutover-id UUID
scripts/openship-stack --environment ENV admission open --cutover-id UUID
```

The current apply reports its phase, event/run scan totals, backfilled event
count, created/reused command counts, recorded operator identity and that gates
remain closed. Verification
requires a completed apply, exact event/run identities, no missing retained
event notifications, no eligible queued run without a command, no pending
event/command PubAck, and ready JetStream resources. Opening execution reruns
DB verification while holding the messaging state lock, then records the
opening time. Admission can open only after execution has been explicitly
opened. To stop traffic for rollback or maintenance, close admission first,
allow active work to drain, then close execution. Closing these DB gates does
not require a reachable broker. The wrapper delegates these operations to the
Python CLI. Direct invocation from `apps/api` remains available for recovery:

```sh
uv run python -m src.openship.messaging.cli admission close --cutover-id UUID
uv run python -m src.openship.messaging.cli execution close --cutover-id UUID
uv run python -m src.openship.messaging.cli execution open --cutover-id UUID
uv run python -m src.openship.messaging.cli admission open --cutover-id UUID
```

### Read-only run classification

The current `cutover preview` emits `run_disposition_counts`,
`blocked_run_groups`, and at most 100 `blocked_run_ids`. The category counts and
the total blocker count cover every run; the ID list is only a bounded sample.
Classification uses this precedence so a terminal or waiting status cannot
hide unsafe queue state:

1. A missing job or duplicate jobs is a blocker.
2. The run and single job fences must match.
3. An owner or lease on a nonterminal run is a blocker, including an expired
   lease. Stop or reconcile that owner under the existing worker recovery
   rules. A terminal run with matching fences and no admitted, dispatching or
   running tool operation and no `started` tool attempt is reported as
   `terminal_owned_preserved`; it remains terminal and is never replayed. The
   attested cutover apply clears only that row's stale owner and lease fields
   while holding its run/job locks. Terminal rows with active execution
   records remain blocked.
4. A queued run is eligible only when `provider_started_at` is null and there
   are no provider-attempt, tool-operation, or tool-attempt rows, and its exact
   `(kind, graph_version)` exists in the runtime graph registry. Any dispatch
   history blocks replay, including interrupted or otherwise terminalized
   records. Unsupported graph versions block replay so migration cannot turn
   accepted queued work into a worker-side `unsupported_graph_version` failure.
5. On Phase 6A, a queued run may have no command row yet, or exactly one
   current-generation row with the installation, epoch, message ID, subject,
   run, generation and canonical payload all matching. A current delivery
   disposition is accepted only for a digest-matching `deferred` hold caused
   by `transport_inactive` or `execution_closed`; orphaned dispositions, all
   other dispositions and identity mismatches block the run. Apply must reuse
   a matching command identity rather than minting a second one.
6. Waiting-Connector and terminal runs are preserved only after the job and
   fence checks above and when no operation is still admitted, dispatching or
   running and no attempt remains `started`.
7. `created` and `running` runs require recovery even when no owner is visible.
   Any unrecognized status is unsupported and blocks the cutover.

Phase 5 queued work starts at dispatch generation 1. The expanded-schema
migration persists the installation ID and transport epoch once; preview does
not mint them, and a future apply must use those persisted values. Before an
apply exists, a `status=preview` report is informational only. Apply must close
admission and execution, stop legacy workers, then repeat these checks under
the run/job locks in the same transaction that records each bounded batch.
That recheck prevents a stale preview from authorizing a command after queue
state changes.

### Read-only event coverage

The preview also reports `event_notification_rows`,
`missing_event_notification_rows`, `event_identity_conflicts`,
`orphan_event_references`, and bounded `missing_event_ids` and
`event_issue_ids` samples. Sample lists contain at most 100 IDs and include a
truncation flag; the preview never prints event or outbox payloads.

On Phase 5, every retained event is counted as missing a JetStream notification
because legacy outbox records are not accepted as current reference-only
notices. Missing notices are backfill work and do not alone block preview. On
Phase 6A, each retained event must have either no notice or exactly one notice
whose event/conversation/type, schema version, installation-derived message
ID, subject, canonical `{"event_id": ...}` payload, and transport epoch match
the persisted event and messaging state. A malformed or duplicate notice is
an identity conflict. A notice with a null or pruned event reference is an
orphan; both conflicts and orphans block preview and require reconciliation.
This remains an inventory only: apply repeats event checks while holding the
appropriate locks, backfills missing notices idempotently, and records
progress under the cutover ID. Resumable apply/verification, explicit gate
open/close commands and rollback preview/apply are implemented. Real
PostgreSQL/broker rollback qualification remains open.

## Rollback and broker outage

Before opening the new release, rollback can resume the old runtime using
unchanged application rows and additive schema after verification. After new
execution starts, close admission, close execution to stop new claims, and
drain or reconcile active work. Stop the API and worker processes, keep the
relay running until command and maintenance publications receive confirmation,
then stop the relay. Drain or reconcile the JetStream consumer and wait for
publisher leases to expire. The read-only
`rollback preview` blocks live owners, uncertain command dispositions,
unresolved exhaustion records, active retention tasks, unconfirmed command or
maintenance publications, identity conflicts and unsupported run states.
Missing event notices are backfilled; dispatch-generation exhaustion blocks
before the epoch changes. `rollback apply` requires explicit
operator, process-stop and consumer-drain attestations; it rechecks blockers
under the messaging state lock, rotates to a persisted rollback epoch, sets the
transport inactive with both gates closed, and processes event/run rows in
batches of at most 500. It requeues retained event notices for a future
forward cutover at the rollback epoch, increments each safe queued run's
dispatch generation, keeps old command records immutable in their original
epoch, and preserves the additive schema. A retry with the same cutover ID
resumes the same epoch and cursors; rows at or before the run cursor have
already had their generation bump committed. A queued run whose next generation
cannot be represented blocks before epoch rotation. Do not downgrade the
database or delete broker state to force a green report.

After a `status=rolled_back` report, keep the NATS relay and workers stopped,
deploy the old PostgreSQL-polling release, then reopen web ingress. The old
release does not honor the Phase 6A DB gates. A later forward cutover must use
a **new cutover ID**; it reuses the persisted rollback epoch and the bumped
generation to create fresh queued-command identities. The original cutover ID
remains an audit record and cannot be reused for forward apply.

For rollback, the direct CLI remains available from `apps/api` after the
normal drained rollback procedure:

```sh
uv run python -m src.openship.messaging.cli admission close --cutover-id UUID
uv run python -m src.openship.messaging.cli execution close --cutover-id UUID
uv run python -m src.openship.messaging.cli rollback preview --cutover-id UUID
uv run python -m src.openship.messaging.cli rollback apply --cutover-id UUID \
  --operator IDENTIFIER --processes-stopped --consumers-drained
```

Closing gates and rollback apply use PostgreSQL only; they remain available if
the broker is offline. Do not start the old runtime until apply reports
`rolled_back`.

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
