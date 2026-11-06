---
type: operations
summary: OpenSandbox development setup, lifecycle ownership, and the verified chat-directed Python journey (analysis-parser journeys still pending).
status: chat-python-journey-verified-analysis-journeys-pending
date: 2026-11-06
---

# OpenSandbox development setup

The [revised 6B spec](../superpowers/specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md)
and [plan](../superpowers/plans/2026-10-28-phase6b-opensandbox-implementation.md)
define the small client integration. Tasks 1–5 implement the client, registered
analysis execution, checked publication and explicit resume. This is **not**
full-application verification: the historical direct-client run below does not
prove the registered API/worker/gateway journey. No new host provisioning or
isolation qualification is claimed here. Do not reuse the old branch's migrated database
or sandbox application volumes as a fresh `main` database.

## Rootful Docker/gVisor — first target

On Arch-family hosts, install/configure engine and gVisor dependencies with:

```sh
scripts/bootstrap/bootstrap.sh arch-docker --configure-only --verify
# Omit --configure-only if the declared packages need installation.
# Select arch-docker-podman instead to configure both rootful engines.
```

Review package/service changes before applying; these commands need operator
privileges and may upgrade/install packages. They have **not** been applied
by this scope-reset task. See the [bootstrap guide](../../scripts/bootstrap/README.md)
for prerequisites, optional rootless profiles, socket locations and check mode.
The Ansible roles install/configure engine/runtime dependencies; OpenSandbox
itself is supplied by the opt-in sandbox profile in `docker-compose.yml`, not
an Ansible installer role.

The primary manager socket mapping is:

```text
host /var/run/docker.sock → opensandbox-server /var/run/docker.sock
```

Only the manager mounts it. The API, worker and generated program must never
receive it. Keep the ordinary Docker runtime default; the TOML selects
`runtime.type = "docker"`, `secure_runtime.type = "gvisor"`, and
`secure_runtime.docker_runtime = "runsc"` for provider workloads.

## Images, configuration and authentication

1. Use [images.json](../../deploy/sandbox/images.json) as the carried release/image
   reference, not new qualification evidence. Build/preload the workload image
   from `deploy/sandbox/Dockerfile.python` with its `PYTHON_BASE_IMAGE` argument
   set to the recorded digest-pinned base; keep its resulting image identity in
   protected worker configuration.

## Plotting layer (optional, general Python execution)

Chat-directed Python execution may request PNG/JPEG chart outputs. A maintained
operator-built plotting layer installs pinned headless plotting packages on top
of the digest-pinned standard workload **without changing the standard-library
runtime identity** (`SANDBOX_PROFILE_ID`/`SANDBOX_DEPENDENCY_IDENTITY` stay
`python311`/`stdlib` unless the plotting runtime is selected):

```sh
podman build --build-arg PLOTTING_BASE_IMAGE=openship-python@sha256:<workload-digest> \
  -f deploy/sandbox/Dockerfile.python-plotting -t openship-python-plot:local .
```

Selecting it records the built image identity plus `plotting_profile_id`
(`python311-plot`) and `plotting_dependency_identity`
(`matplotlib-3.9.2-numpy-2.1.0`) from `images.json` in protected worker
configuration; only `Agg` is installed, and the API-side verifier, not the
runtime, decides whether an output is retained.
2. Copy `deploy/sandbox/config.example.toml` to a protected installation-specific
   path and replace `REPLACE_WITH_PINNED_EXECD_IMAGE`. Its maximum sandbox timeout
   is **1,800 seconds**, permitting the client's requested 30-minute lifetime.
   This maximum alone does not set a sandbox TTL: the application create request
   supplies `1800`.
3. Provide the Compose variables outside Git: `OPENSHIP_SANDBOX_SERVER_IMAGE`,
   `OPENSHIP_SANDBOX_GATEWAY_IMAGE`, `OPENSHIP_SANDBOX_CONFIG`,
   `OPENSHIP_SANDBOX_API_KEY`, `OPENSHIP_SANDBOX_AUTH_MAP` and
   `OPENSHIP_SANDBOX_ENGINE_SOCKET=/var/run/docker.sock`.
4. Keep authenticated Nginx ingress for the recorded `release-1.2.0`
   single-tenant proxy authentication gap. The worker endpoint is
   `http://opensandbox-gateway:8080`, not direct manager/execd access. The
   manager and gateway use separate private networks; workload network/storage
   controls remain operator/OpenSandbox configuration, not OpenShip code.

Generate an API key and matching protected, case-sensitive Nginx map **outside
Git**, for example in an existing operator-owned protected directory:

```python
import os
import secrets

key = secrets.token_hex(32)
for filename, content in (
    ("sandbox-api-key", key + "\n"),
    ("sandbox-auth.map", '"~^' + key + '$" 1;\n'),
):
    with os.fdopen(os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(content)
```

This refuses to overwrite existing files. Do not print/copy keys into chat,
source, reports or checkpoints. The map and server/worker use the same key.
Nginx plain-string map keys are case-insensitive; use the anchored regex above.
Remote exposure/TLS/production qualification is not delivered by this local
development setup. Upstream advisory findings remain deferred, not fixed.

## Start/stop the provider infrastructure

After preparing configuration/images/secrets and the existing dev stack's
messaging/artifact prerequisites:

```sh
export CONTAINER_ENGINE=docker
export DOCKER_HOST=unix:///var/run/docker.sock
export OPENSHIP_SANDBOX_ENGINE_SOCKET=/var/run/docker.sock
scripts/openship-stack -e dev --sandbox start
scripts/openship-stack -e dev --sandbox status
scripts/openship-stack -e dev --sandbox stop
```

The option starts the manager and authenticated gateway along with the existing
development stack. Capability registration alone does not enable execution;
the worker still needs its protected provider/runtime configuration and existing
gateway policy. The manager refuses to start without an API key; missing
images, configuration and authentication-map settings must also be supplied.
Do not start stacks from different worktrees against the same project/database/
volumes without an explicit operator decision.

For provider-only testing, `docker-compose.sandbox.yml` is a separate override
that attaches the authenticated gateway to a test ingress network and publishes
it on `127.0.0.1:18080` by default. Only the gateway joins that ingress network;
local bridge peers can reach it and must still authenticate. With
the protected provider settings above configured and the fixed sandbox-data
network confirmed not to belong to another stack, start only the manager and
gateway from the repository root:

```sh
docker compose -p openship-phase6b-client -f docker-compose.yml \
  -f docker-compose.sandbox.yml --profile sandbox up -d --no-deps \
  opensandbox-server opensandbox-gateway
```

This is a provider setup command, not evidence that the execution client works.

After starting the provider, run the opt-in client journey from `apps/api`.
Set the key-file path to an operator-owned file, not a committed secret:

```sh
OPENSHIP_LIVE_OPENSANDBOX=1 \
OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT=http://127.0.0.1:18080 \
OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE=/path/to/protected/sandbox-api-key \
OPENSHIP_LIVE_OPENSANDBOX_IMAGE=openship-python:local \
uv run pytest -q -m provider_live tests/live/test_opensandbox.py
```

The test creates provider-owned sandboxes, including a 60-second expiry
fixture, and does not delete them. It exercises the client directly, not the
application's complete parser/resume journey or isolation certification.

On 2026-11-03, the direct-client journey ran on rootful Docker with `runsc`:
create/inspect, changed-input reuse, declared output collection, nonzero exit,
the provider's 30-second command timeout, symlink rejection, diagnostic/output
overflow, cancellation and provider expiry passed (one test, 94.19 seconds).
The server, execd and gateway used the digest-pinned images in
`deploy/sandbox/images.json`; the locally preloaded Python test image had image
ID `sha256:c3a1aae6caee0d8d5b1ac002193399e11b6d1b7d5fdd7f0929b5ba7e0ec7477c`.
This is evidence for that local provider/client journey only; it is not the
full application demonstration, security qualification, or a portable image pin.

## Runtime ownership and next verification

OpenShip requests a 30-minute lifetime and saves one conversation-context
pointer. Compatible runs in the same project/conversation can reuse it; each
execution still transfers selected inputs to a fresh directory. **Do not add per-step delete,
TTL renewal, orphan reconciliation or deletion-gated physical reservations.**
OpenSandbox handles expiration/cleanup. Accepted reports remain in OpenShip
storage regardless of provider disappearance. A lost/ambiguous response is an
interrupted application step, not permission to resend code.

Task 5 is review-cleared, including duplicate resume idempotency and malformed
persisted-operation projection. Its A/B resume contract and Task 4's digest-bound
replay remain unchanged. Task 6's real parser/reuse/resume demonstration is still
**blocked**, not passed. A dedicated qualification launcher/runner/CI certification
platform, security scans and production matrix are deferred. Optional host probes
do not certify provider isolation.

## Task 6 prerequisite stop — 2026-11-04

Candidate application revision: `763cdd0`. Read-only inspection found only the
running `openship-phase6b-client` manager and authenticated gateway (loopback port
18080). The manager alone mounts the Docker socket. Compose labels match this
worktree, but that is not authorization to attach application services, use its
volumes, extract credentials, or alter/stop it. No such action was performed.

Neither the process environment nor repository/API `.env` files supplied
`TEST_POSTGRES_URL`, the existing JetStream test settings (`TEST_NATS_URL`,
`TEST_NATS_AUTH_FILE`, `TEST_NATS_SERVER_CONFIG`), OpenSandbox endpoint/key-file/image
settings, or a configured full-stack application URL. Both `.env` files were
absent. No operator-owned protected configuration granting safe reuse was
supplied. Do not infer credentials from container inspection or invent database
and broker targets. These are missing configuration prerequisites, not observed
provider failures.

Before resuming, an operator must provide authorized disposable PostgreSQL and
authenticated JetStream configuration, protected worker provider/runtime settings,
and either explicit safe provider-stack reuse authorization or an isolated
provider deployment. Use a fresh database and artifact root/storage identity; do
not attach this application to the running stack's volumes. Keep credentials out
of reports, shell arguments and browser traces.

The implemented worker settings are `SANDBOX_ENABLED`, `SANDBOX_ENDPOINT`,
`SANDBOX_DEPLOYMENT_ID`, `SANDBOX_KEY_FILE`, `SANDBOX_IMAGE`,
`SANDBOX_PROFILE_ID` and `SANDBOX_DEPENDENCY_IDENTITY`. The first supported
profile is `python311` / `stdlib`. Use an installation-specific deployment ID;
the key file belongs only in protected worker configuration, not task data or
browser configuration. These are separate from the `OPENSHIP_LIVE_OPENSANDBOX_*`
selectors used by the historical direct-client test.

The planned `apps/api/tests/full_stack/test_sandbox_analysis.py` and browser
parser/resume journey have **not been implemented or executed** at this stop.
There is no configured real-application invocation or expected-output artifact
to cite yet. Do not substitute the direct-client command above, a skipped test,
or mocked browser coverage. When implemented, explicitly enabled real-service
tests must fail on missing prerequisites rather than silently skip.

Existing network-free regression commands (run from `apps/api` and `apps/web`,
respectively):

```sh
uv run ruff check .
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'
pnpm typecheck
pnpm test:run
pnpm build
```

Expected output is a clean lint/typecheck/build and passing ordinary suites;
these commands intentionally exclude the real-service lanes. Exact observed
counts, local output references and limitations are recorded in the
[Task 6 report](../../.superpowers/sdd/2026-10-28-phase6b-opensandbox-implementation/task-6-report.md).
They cannot establish full-stack success.

### Demonstration and acceptance still required

After prerequisites are supplied, implement and run the existing application and
Playwright lanes, with clearly labeled deterministic generation/judge fixtures
(not real-model evidence). Show upload → captured parser → validated
`summary.json`/`report.txt` → evidence-linked answer; changed logs must reuse the
compatible sandbox ID while using current inputs. Show provider expiry/absence
replacement using a disposable fixture, leaving production lifetime at 1,800
seconds. Demonstrate accepted/rejected returned source, explicit A/B resume,
retained downloads after restart/provider disappearance, and a visible
unavailable-provider failure without altering the protected provider stack.

Present that runnable demonstration to the user for **accept / revise / reject**.
No runnable full-stack demonstration is available at this prerequisite stop;
**user acceptance is pending** and is separate from implementation and test
results. Do not mark Task 6 or the overall Phase 6B acceptance gate complete.

## Real chat-directed Python journey — 2026-11-06

The chat-directed journey (URL → bounded download → model-written Python → PNG
artifact → durable resume) now runs end-to-end against real services. This
covers the chat Python execution lane; the fixed-analysis parser journeys from
Task 6 above remain pending and are not claimed here.

Disposable services used for the lane (the running dev stack was used for the
browser qualification only; no dev-stack volume, database or artifact root was
modified):

- PostgreSQL: dedicated lane container `openship-p6b-pg-lane` (postgres:15) on
  host port 5444, one fresh database per run, dropped afterwards.
- Artifact store: fresh temporary root and storage identity per run
  (`ARTIFACT_STORAGE_ID` = random UUID, maintenance CLI `init`).
- OpenSandbox: the authorized dev-stack provider (gateway on `127.0.0.1:18080`,
  loopback overlay), preflight-probed before use (403 unauthenticated / 404
  authenticated).
- Model/download bodies: deterministic scripted fixtures only; the OpenSandbox
  transport is never stubbed (observations come from a logging wrapper around
  the client's own http session).

Commands (from `apps/api`; credentials via files/env only, never arguments):

```sh
# provider started with the sandbox profile + loopback overlay
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml \
  -f docker-compose.sandbox.rootless.yml \
  OPENSHIP_SANDBOX_CONFIG=./deploy/sandbox/config.local.toml \
  OPENSHIP_SANDBOX_AUTH_MAP=./secrets/opensandbox-auth.map \
  OPENSHIP_SANDBOX_API_KEY="$(cat secrets/openship-sandbox-key)" \
  start opensandbox-server opensandbox-gateway

# full-stack journey: API + durable worker + Playwright browser qualification
TEST_POSTGRES_URL="postgresql+psycopg://openship:<lane-pass>@127.0.0.1:5444/lane" \
  uv run pytest -q tests/full_stack/test_sandbox_python_chat.py
# observed: 1 passed in 29.55s; browser step "1 passed (6.9s)"

# live client journeys against the same provider
OPENSHIP_LIVE_OPENSANDBOX=1 \
OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT=http://127.0.0.1:18080 \
OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE=../../secrets/openship-sandbox-key \
OPENSHIP_LIVE_OPENSANDBOX_IMAGE=openship-python-plot:phase6b-task4 \
OPENSHIP_LIVE_OPENSANDBOX_PLOT_IMAGE=openship-python-plot:phase6b-task4 \
  uv run pytest -q tests/live/test_opensandbox.py
```

Recorded evidence:

- Image identities: `openship-python-plot:phase6b-task4` with
  `SANDBOX_PROFILE_ID=python311-plot`,
  `SANDBOX_DEPENDENCY_IDENTITY=matplotlib-3.9.2-numpy-2.1.0` (from
  `deploy/sandbox/images.json`).
- One sandbox create per chat turn (observed sandbox id e.g.
  `f8721c8f-fda3-4d40-9cca-7642425558d8`) and exactly one `/command` per
  execution in a fresh directory `/outputs/{operation_id}-{uuid}` with
  `argv ["python", ".../main.py"]`.
- Downloaded CSV digest `07a0fbf747e205f87f9c78a62e3aea1f9366428b194373cd216eb2a349e0ea97`
  (55 bytes); chart PNG retrieved through `/api/artifacts/{id}/content`
  (`\x89PNG\r\n\x1a\n` magic) and previewed in the browser via an authenticated
  blob fetch.
- Resume replay: the continuation re-emits the recorded decisions under fresh
  provider call ids and reuses the committed operations — the download log and
  sandbox call log keep the same counts (no refetch, no re-executed command);
  the same `client_request_id` returns the same continuation run.
- Restart durability: after stopping and restarting API + worker, the
  conversation snapshot and the chart artifact content are restored from the
  artifact store.

Reconciliation with the original Task 6 criteria
(`2026-10-28-phase6b-opensandbox-implementation.md`):

- Real registered API/worker/gateway + PostgreSQL + artifact store + OpenSandbox
  exercised in one journey: **met** by the chart journey above.
- Sandbox ID reuse for a compatible second request with changed inputs, and
  provider expiry/absence replacement via a disposable fixture: **carried by the
  existing live lane** (expiry fixture verified; changed-input reuse journeys
  still need the stdlib image tag, recorded as remaining above).
- Upload → captured parser → `summary.json`/`report.txt` analysis outputs:
  **not met** — the chat scenario replaces the analysis-specific executor by
  design; the parser fixtures have not been run and stay pending.
- Accepted/rejected returned source, explicit A/B resume, retained downloads
  after restart/provider disappearance, visible unavailable-provider failure:
  chat continuation and idempotent resume **met**; from-beginning (B) and the
  analysis A/B pair **pending**; restart retention **met**; provider-failure
  display **pending**.
- Unit transport stub as substitute: **not used** — the sandbox transport is
  real in every lane.

Limitations: the scripted model fixture and canned download body are
deterministic fixtures, not real-model evidence (no real-model demonstration is
claimed); the analysis-parser journeys (upload → captured parser →
`summary.json`/`report.txt`) and their Task 6 acceptance remain pending; host
probes are not isolation certification.
