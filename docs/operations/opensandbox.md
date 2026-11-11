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

1. Select a maintained workload profile from
   [images.json](../../deploy/sandbox/images.json) and build it locally on the
   **same engine the provider uses**:

   ```bash
   scripts/openship-stack -e dev sandbox-images list
   scripts/openship-stack -e dev sandbox-images build logs-data   # or: build --all
   scripts/openship-stack -e dev sandbox-images smoke logs-data
   ```

   Copy the three settings printed by a successful build verbatim into protected
   worker configuration (`.env` for the Compose dev stack). The normal
   `--sandbox start` path preflights that selection and reuses a matching image;
   it never builds. `--sandbox start --build-sandbox` is the explicit dev-only
   opt-in and builds only the selected profile when missing or stale. A missing,
   stale or unverified image stops startup before messaging/artifact
   initialization, migrations, Compose changes or cutover.

   The legacy `deploy/sandbox/Dockerfile.python` and
   `deploy/sandbox/Dockerfile.python-plotting` builds remain supported with
   their existing `python311`/`stdlib` and
   `python311-plot`/`matplotlib-3.9.2-numpy-2.1.0` identities.

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

### Plotting layer (optional, general Python execution, legacy path)

Chat-directed Python execution may request PNG/JPEG chart outputs. The managed
`logs-data` profile already ships headless Matplotlib. The legacy operator-built
plotting layer remains available for the standard workload; it installs pinned
packages on top of the digest-pinned standard workload **without changing the
standard-library runtime identity** (`SANDBOX_PROFILE_ID`/
`SANDBOX_DEPENDENCY_IDENTITY` stay `python311`/`stdlib` unless the plotting
runtime is selected):

```sh
podman build --build-arg PLOTTING_BASE_IMAGE=openship-python@sha256:<workload-digest> \
  -f deploy/sandbox/Dockerfile.python-plotting -t openship-python-plot:local .
```

Selecting it records the built image identity plus `plotting_profile_id`
(`python311-plot`) and `plotting_dependency_identity`
(`matplotlib-3.9.2-numpy-2.1.0`) from `images.json` in protected worker
configuration; only `Agg` is installed, and the API-side verifier, not the
runtime, decides whether an output is retained.

### Managed profile contents

All managed images derive from the digest-pinned shared base, run Python 3.12
with the existing `python` entrypoint, UID/GID 1000, 1 CPU, 512 MiB and the
30-second command / 1,800-second lifetime limits. They add packages, not
execution permissions: networking, mounts, credentials and a shell tool remain
absent/deferred.

| Profile | Python packages (representative pins) | CLI tools |
| --- | --- | --- |
| `standard` | SymPy 1.14.0, regex 2026.11.7 | Node.js 22.23.4 |
| `logs-data` | pandas 3.1.1, NumPy 2.5.4, Matplotlib 3.11.2 (`Agg`), regex 2026.11.7, pygrok 1.0.0, Drain3 0.9.11 | none |
| `iac-validation` | Checkov 3.4.3, jsonschema 4.26.0, PyYAML 6.0.3, graphviz 0.21 | TFLint 0.65.0 (bundled Terraform rules only), Graphviz 2.42.4-3 |
| `devops-shell` | isolated Azure CLI venv, Azure CLI 2.92.0 | Bash 5.2.37, Git 2.47.3, AWS CLI 2.39.6, gcloud 592.0.0, Terraform 1.17.1, Terragrunt 1.2.1 |

`devops-shell` keeps `az` in `/opt/azure-cli` so cloud SDK dependencies cannot
shadow the workload Python. gcloud retains its bundled interpreter and
components (smoke-verified: core 2026.10.30, bq 2.1.41, gsutil 5.38, bundled
Python 3.14.9). Binaries may be invoked from submitted Python; this does not add
a shell execution API, authenticated cloud operations or provider/module bundles.

### Updating locks and pins

The committed locks are the only build inputs; `build`/`start` never resolve or
upgrade dependencies. Regenerate one profile explicitly with the pinned host
`uv` 0.12.23 on Linux x86_64:

```bash
python3 scripts/lock_sandbox_dependencies.py logs-data
python3 scripts/lock_sandbox_dependencies.py standard --upgrade-package sympy
```

The command resolves the complete transitive closure for Python 3.12 and
writes hash-pinned wheel/source entries atomically; a failed resolution leaves
the previous lock intact. Changing any declared base or profile input — a lock,
Dockerfile, installer, OS package list, tool manifest pin or the shared
`build-requirements.lock` — changes that recipe's fingerprint. Changing only
one profile's lock does not change another profile's identity.

Pin sources:

- `deploy/sandbox/pins.json` — digest-pinned Python base, the checksum-pinned
  uv release archive, and the signed Debian snapshot timestamp. The archive
  checksum is **not** an OCI image digest; the snapshot is passed to profiles
  that run `apt-get` as the `DEBIAN_SNAPSHOT` build argument, so editing it
  changes those recipes and requires an explicit rebuild.
- `deploy/sandbox/tools.lock.json` — platform-specific CLI archive URLs and
  published SHA-256 values.
- Profile `os-packages.lock` — exact apt packages installed from that snapshot
  by `iac-validation` and `devops-shell`. Profiles that add no OS packages
  inherit the digest-pinned base and keep an informational lock only; their
  Dockerfiles never run `apt-get`.
- `deploy/sandbox/build-requirements.lock` — PEP 517 prerequisites installed
  without network access during source-package builds.

After regenerating a lock, rebuild and smoke the profile, then update the three
runtime assignments. No dependency is upgraded implicitly, and a changed
recipe must be built explicitly.

### Engine, socket and local records

Every helper operation passes the selected Unix endpoint explicitly instead of
relying on the CLI's implicit default connection. Docker defaults to
`/var/run/docker.sock`; rootless Podman defaults to
`$XDG_RUNTIME_DIR/openship-sandbox/podman.sock`. An explicit
`OPENSHIP_SANDBOX_ENGINE_SOCKET` wins and must be an accessible absolute Unix
socket. The helper verifies the engine reports Linux amd64 before any build or
run.

```bash
scripts/openship-stack -e dev sandbox-images list
docker context inspect default --format '{{.Endpoints.docker.Host}}'
```

`list` reports each profile's `matching`, `missing` or `stale` status plus
recorded smoke. Records live under the gitignored
`.openship/sandbox/images/<engine>/<platform>/<profile>.json`. They bind the
recipe fingerprint, local image ID, build time and smoke evidence; the
fingerprint is **not** an image digest, and the image ID is engine-local, not a
registry manifest. Deleting a record does not delete the image but removes the
local build/smoke evidence. To roll back, check out the previous pins/locks,
rebuild that profile and restore its printed assignments; the legacy image
identities are unaffected.

### Local image validation record (2026-11-07)

All four images were built with the ordinary Docker daemon (Docker 29.8.2,
`linux/amd64`, default context `/var/run/docker.sock`). Each build ran its
installed smoke program in a fresh container; the same programs were then
re-run separately under the gVisor `runsc` runtime with `--network none`,
UID/GID 1000, 1 CPU, 512 MiB, all capabilities dropped and a 30-second timeout.

| Profile | Recipe fingerprint | Image ID | Smoke |
| --- | --- | --- | --- |
| `standard` | `0bd7e473c8c5602e2f981d8dcb633b4f613496afaade7e11ba6ca1810d393ef9` | `sha256:35172a0a7d72c9c42af14cfc0b7a912fb04f41c2458822e3b6fd0fad8adf7d77` | Python 3.12.16, SymPy 1.14.0, regex 2026.11.7, Node answer 42 |
| `logs-data` | `51e55f2b24334b844319219bd8671f188bd7b3bfb84d2cc349795c80b801c2f9` | `sha256:3309a813331ac51aaa6c09fd62d38b589a169066169da9ac737f131bc270917c` | CSV groups `{"api": 5, "worker": 7}`, JSON round-trip, grok/regex metadata, two Drain3 clusters, decoded `Agg` PNG |
| `iac-validation` | `31f132439a64330c464e1600095a3345976d3f88a36faa5f7d98e6db26c9d615` | `sha256:1d654f857e0950aa79b72963221e69cff9bb37a2535bcb5c821c9b2b4f974511` | JSON Schema accept/reject, YAML load, Checkov violation/corrected, TFLint violation/corrected, decoded Graphviz PNG |
| `devops-shell` | `b54ad6cb23a16ae3a27ff6e122c041c4dad0ab9e6310fda84ad5c66cc15d71ed` | `sha256:b89323b20c107bfe186907bdfbb3e16f0019eeda4acc8f203cc9ef9b475d5ecd` | Bash 42, local Git staging/diff, Terraform formatting, offline CLI version checks |

This is image-build/smoke evidence, not evidence that a live chat journey ran
under a managed profile. pygrok 1.0.0 emits an upstream Python 3.12 invalid-
escape `SyntaxWarning`; its parsing behavior is verified. TFLint uses bundled
rules only: arbitrary plugins and Terraform providers/modules remain absent.
Live provider testing and user acceptance are recorded separately.


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

## Scripted dev preparation (`init-sandbox`)

For the dev stack, `scripts/openship-stack -e dev init-sandbox` prepares all of
the provider material above in one idempotent step:

- Renders `deploy/sandbox/config.example.toml` to `deploy/sandbox/config.local.toml`
  with the digest-pinned execd image from `deploy/sandbox/images.json`
  (the rendered file is gitignored and only written when absent).
- Generates `secrets/openship-sandbox-key` and `secrets/opensandbox-auth.map`
  (mode 0600, owning user only) **only when both are absent**; a half pair is
  reported and never rotated. Existing files are kept, so re-running is safe.

`scripts/openship-stack -e dev --sandbox <command>` then fails with
"run init-sandbox first" if any of those files is missing and otherwise exports
`OPENSHIP_SANDBOX_CONFIG`, `OPENSHIP_SANDBOX_AUTH_MAP` and
`OPENSHIP_SANDBOX_API_KEY` (read from the protected key file per invocation,
never stored in `.env`) into every Compose invocation selecting the sandbox
profile. Without them, the Compose sentinel fallbacks
`.openship/sandbox/config-required.toml` and `auth-required.map` would make
the engine create root-owned placeholder **directories** and fail with a
confusing bind-mount error rather than a clear one.

UID interaction: rootful Compose bind mounts keep numeric ownership, so these
files are created as the invoking user (UID 1000). The manager and Nginx
gateway run as root and read them regardless; the app containers run as UID
1000 and read `./secrets` mounts (`SANDBOX_KEY_FILE=/run/secrets/openship-sandbox-key`)
as the file owner. Under rootless Podman, keep the rootless overlay, which maps
the manager to the invoking user.

## Failure signatures on the dev stack

- **Chat UI shows "The search service is temporarily unavailable." for a sandbox
  tool call:** that string is the shared `provider_unavailable` vocabulary entry
  (`execution/gateway.py` coerces unrecognized tool failures onto it), not a
  search-tool complaint. The common cause is the worker having no workload image:
  `SANDBOX_IMAGE` must name an image **built on the engine the sandbox server
  uses** (rootful `/var/run/docker.sock` unless the rootless overlay selects
  Podman), with `SANDBOX_PROFILE_ID`/`SANDBOX_DEPENDENCY_IDENTITY` matching the
  `images.json` identity for that closure. Build the closures with the
  Dockerfiles above before starting the profile.
- **Worker logs `nats.errors.Error: nats: permissions violation for
  subscription to "openship.<installation>.v1.control.<run>"`:** the worker's
  NATS policy must allow subscribing to run control-notice subjects (the
  cancel/fence wakeup channel in the messaging design). `scripts/init_messaging.py`
  grants this since the fix; an installation whose messaging files predate it
  needs the auth set regenerated (move the messaging directory aside, rerun
  `init-messaging`) and the stack restarted.

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
`SANDBOX_PROFILE_ID` and `SANDBOX_DEPENDENCY_IDENTITY`. Select a managed profile
(`standard`, `logs-data`, `iac-validation` or `devops-shell`) with the three
assignments printed by `sandbox-images build`; the legacy `python311` / `stdlib`
and `python311-plot` identities remain supported through their own Dockerfiles.
Use an installation-specific deployment ID; the key file belongs only in
protected worker configuration, not task data or browser configuration. These
are separate from the `OPENSHIP_LIVE_OPENSANDBOX_*` selectors used by the
historical direct-client test.

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

## Agentic task activation, rollback and crash recovery — 2026-11-10

Phase 7A puts continuous agentic tasks behind two switches that stay **off** by
default: `AGENTIC_TASKS_ENABLED` (with `AGENTIC_TASK_PROFILE_IDS` naming the
qualified tool-capable profiles) and the pinned profile snapshot the API and
worker mount. Activation is a coordinated operation, not a file write.

### Activation

1. Build the profile snapshot only through the consented build command, e.g.
   `scripts/openship-stack -e dev sandbox-images build logs-data`, and confirm
   `deploy/sandbox/sandbox-profiles.yaml` stays gitignored. The tracked empty
   example remains the disabled fallback; a custom `SANDBOX_PROFILES_FILE`
   requires the operator's own path.
2. Drain in-flight work through the existing messaging gates and cutover
   (see [messaging qualification](messaging.md#qualified-transport-versions)).
   Task runs parked on a plan confirmation, an input request or a recovery
   request are **preserved**: the drain never cancels a waiting task, and the
   database, LangGraph checkpoints, artifact store, storage identity and pending
   decision records are carried over unchanged.
3. Recreate the API and the worker **together** from the same image/build.
   Writing YAML alone never recreates services; `openship-stack` restart of one
   service is not activation.
4. Verify before reopening admission: the mounted snapshot bytes and digest are
   identical in both containers (`sha256sum` of the mounted file inside API and
   worker), `/ready` reports the configured `SANDBOX_PROFILE_ID` as eligible,
   and the worker is consuming its queue. If the API and worker resolve
   different snapshots, the profile catalog digest differs, v5 dispatch stays
   blocked, and admission is not reopened.
5. Reopen admission only after the qualification gates that own this feature
   passed. The fixture gates are recorded in the
   [Phase 7A qualification boundary](../validations.md) and the plan; the live
   immutable-ID and live-journey gates in this file's commands below are **not
   run yet**, so rollout remains off.

### Rollback and downgrade

- Stop v5 admissions first: clear `AGENTIC_TASKS_ENABLED`, drain, then restore
  the API/worker pair. A run already on a v5 plan is served only by a runtime
  that understands v5 records and the three waiting statuses; with older code it
  stays visibly parked and reported, never silently rewritten.
- Never downgrade the schema while task, plan, attempt, decision or recovery
  rows exist. Migration `phase7a033` and `phase7a034` data (recovery reports,
  skipped-step and call-binding state) must survive coordinated backup/restore
  together with the matching checkpoints, artifact store identity and transport
  identities. Restoring the database without them is not a restore.
- An operator command, a UI refresh, or a service restart must never repin a
  profile, restart a task, or re-issue a tool call. A restarted worker resumes
  from the durable queue claim; a restarted browser reads the same card.

### Crash decision table

| What the records show | What the operator sees | What the product does |
| --- | --- | --- |
| An approval transaction never committed | The same ask, with the same plan revision, request id and digest | Re-presents the open request; no new dispatch, no new plan revision |
| A choice committed, its HTTP response lost | The same settled card after reconnect | Recovers from the decision-command record and the existing outbox |
| A tool command may have been dispatched | A diagnosis (plan revision, step, attempt, unknowns, risks) before any button | Parks and asks for an explicit choice — in Auto as well as Copilot. Repeat is disabled while a command may still be running; continue is disabled while a later step depends on the step's output |
| A completed plan gate and an unsettled run | The plan card in its parked state | Leaves the task queued; the reconciler extends the wait, it never re-dispatches |

Choices, budgets and honesty: attempt budgets (`TaskLimits`: 8 steps, 3 attempts
per step, 2 repairs, 24 generation attempts, lowered only by operator caps) are
recorded and reported; a budget stop is a budget disposition, not a completion.
Partial outputs stay retained and listed. A user skip is recorded as skipped,
never as a verified step, and a step that depends on skipped work stays blocked
until its required inputs exist independently. Final output claims are computed
from verified outputs only.

### Commands for the gates that remain

Nothing below runs in a normal test lane: each one is opt-in and names its own
prerequisites. From `apps/api` (credentials and URLs come from files/env, never
arguments):

```sh
# Fixture journeys: real gateway + disposable PostgreSQL + real browser.
# Model transport and the source body are synthetic; the sandbox transport is not.
OPENSHIP_FULL_STACK_AGENTIC_TASKS=1 \
SYNTHETIC_PROFILE_SNAPSHOT="$(python3 ../../scripts/sandbox_images.py --root ../.. --engine docker profiles-source)" \
  uv run pytest -q -m full_stack tests/full_stack/test_agentic_tasks.py

# Live qualification: real model tuple, real manager, real source. Not run;
# selected-without-prerequisites fails by design.
OPENSHIP_LIVE_AGENTIC_TASKS=1 \
OPENSHIP_LIVE_TOOL_PROFILE_ID=<tool-capable profile id> \
OPENSHIP_LIVE_AGENTIC_PROFILES_FILE=<operator profiles file> \
OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT=<authorized endpoint> \
OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE=<protected key file, 0600> \
OPENSHIP_LIVE_OPENSANDBOX_PROFILE_ID=<pinned profile> \
OPENSHIP_LIVE_OPENSANDBOX_DEPENDENCY_IDENTITY=<identity from the profiles file> \
OPENSHIP_LIVE_AGENTIC_SOURCE_URL=<controlled CSV> \
OPENSHIP_LIVE_AGENTIC_DEFECTIVE_SOURCE_URL=<same data, one known defect> \
OPENSHIP_LIVE_AGENTIC_INVALID_SOURCE_URL=<refused source> \
OPENSHIP_LIVE_AGENTIC_EXPECTED_AVERAGES='{"January":5,"February":8}' \
TEST_POSTGRES_URL=<disposable service> TEST_NATS_URL=<disposable broker> \
TEST_NATS_AUTH_FILE=<broker auth file> TEST_NATS_SERVER_CONFIG=<broker config> \
  uv run pytest -q -m provider_live tests/live/test_agentic_task_journey.py

# Immutable-reference smoke through the manager (separately authorized selector):
OPENSHIP_LIVE_SANDBOX_PROFILES=1 \
OPENSHIP_LIVE_SANDBOX_PROFILES_FILE=<operator profiles file> \
OPENSHIP_LIVE_SANDBOX_PROFILE_ID=<pinned profile> \
  uv run pytest -q -m provider_live tests/live/test_sandbox_profiles.py
```

`OPENSHIP_LIVE_AGENTIC_ENGINE_INSPECTION=1` additionally checks the pinned image
id against the manager's own engine. Where the manager executes the pinned
engine-local id only by pulling or retagging, this gate blocks and the design is
reviewed: qualification is never weakened to a mutable tag.
