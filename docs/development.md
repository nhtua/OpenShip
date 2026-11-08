---
type: guide
summary: Development setup details, sandbox images, native/custom services, tests, and contributor checks.
date: 2026-11-07
status: current
---

# Development and custom local setup

Start with the [README quick start](../README.md#quick-start) for the recommended
Compose development stack. This guide covers image preparation, alternatives,
and running application processes directly. Commands are from the repository
root unless noted otherwise.

## Host setup

The Ansible [host bootstrap](../scripts/bootstrap/README.md) currently provides
Arch-family targets only. Prefer `arch-docker --verify` for the primary rootful
Docker/gVisor setup; `arch-docker-podman` configures both rootful engines.
Use `--configure-only --verify` when the required packages are already installed.
Read the bootstrap guide for AUR authorization and check-mode prerequisites.

The bootstrap configures container engines/runtimes, not application packages,
model credentials, Connector keys, or workload images. Rootful Docker commands
need authorized access to the system daemon; the bootstrap does not grant Docker
group membership. Confirm your ordinary account can use the intended daemon
before running stack commands. Docker socket access is privileged host access.

On other Linux distributions, install Python 3, Docker/Compose 2.24+ and configure
gVisor `runsc` for the sandbox provider manually, following the
[sandbox runbook](operations/opensandbox.md#rootful-dockergvisor--first-target).
The bootstrap guide also describes rootless Docker/Podman alternatives and their
qualification limits. For Podman, set `CONTAINER_ENGINE=podman` in the root `.env`
and install a compatible Compose provider. Do not assume a rootless engine has
the same runtime enforcement as the primary profile.

The primary Docker setup uses the system daemon for both stack and workload
images. If your CLI selects a different context, explicitly select it:

```bash
export DOCKER_HOST=unix:///var/run/docker.sock
export OPENSHIP_SANDBOX_ENGINE_SOCKET=/var/run/docker.sock
```

## Sandbox images for dev

`init-sandbox` generates provider configuration and gateway authentication files;
it does **not** select the Compose images, build a workload, or turn on application
execution. The `--sandbox` flag starts provider infrastructure. Prepare these
additional values in the root `.env` before using the README startup commands:

1. Read the non-secret pinned image references:

   ```bash
   python3 - <<'PY'
   import json
   from pathlib import Path

   images = json.loads(Path("deploy/sandbox/images.json").read_text())
   print("OPENSHIP_SANDBOX_SERVER_IMAGE=" + images["server_image"])
   print("OPENSHIP_SANDBOX_GATEWAY_IMAGE=" + images["gateway_image"])
   PY
   ```

   Add those two assignments to `.env`. Keep pins together with the versioned
   provider configuration; do not substitute arbitrary `latest` images.

2. Build a managed workload profile with the wrapper, on the **same engine the
   provider uses**. The wrapper resolves the socket explicitly; Docker defaults
   to `/var/run/docker.sock`, Podman to the rootless manager socket:

   ```bash
   scripts/openship-stack -e dev sandbox-images list
   scripts/openship-stack -e dev sandbox-images build logs-data   # or: build --all
   scripts/openship-stack -e dev sandbox-images smoke logs-data
   ```

   `build --all` builds `standard`, `logs-data`, `iac-validation` and
   `devops-shell`. Each successful build prints three settings; copy all three
   verbatim into `.env`:

   ```dotenv
   SANDBOX_IMAGE=openship-sandbox-logs-data:<printed recipe identity>
   SANDBOX_PROFILE_ID=logs-data
   SANDBOX_DEPENDENCY_IDENTITY=sha256:<printed recipe identity>
   ```

   An explicit `smoke` is advisory preparation evidence; managed startup
   requires recorded smoke for the current image.

3. Set the application runtime values in `.env`:

   ```dotenv
   SANDBOX_ENABLED=true
   ```

   Use a stable, installation-specific `SANDBOX_DEPLOYMENT_ID`. Keep the supplied
   Compose endpoint and secret-file path for this stack. A normal
   `--sandbox start` preflights the selected managed image and **never builds**;
   `--sandbox start --build-sandbox` (dev only) builds only the selected profile
   when its local image is missing or stale. Application services must be
   recreated to receive changed environment values; a code reload or Compose
   `restart` does not reread `.env`.

   Legacy alternative: build the standard-library image with
   `deploy/sandbox/Dockerfile.python` and select
   `SANDBOX_IMAGE=openship-python:local`, `SANDBOX_PROFILE_ID=python311`,
   `SANDBOX_DEPENDENCY_IDENTITY=stdlib`; charts additionally need the separate
   [plotting image and matching runtime identity](operations/opensandbox.md#plotting-layer-optional-general-python-execution).
   The managed `logs-data` profile already includes Matplotlib.

Return to [initialization and startup](../README.md#3-initialize-once-then-start).
Run `init-sandbox` before `init-artifacts` so it creates the private `secrets/`
directory before Docker mounts it.
Do not put the sandbox API key in `.env`: `init-sandbox` creates protected files
under `secrets/`, which the wrapper supplies automatically. These files must be
readable by the application container identity; see the
[UID and mount requirements](operations/opensandbox.md#scripted-dev-preparation-init-sandbox)
if your host user is not UID 1000 or you use rootless Podman.

For a chat-only development stack, you can skip sandbox image preparation and
`init-sandbox`. First create a private, user-owned mount directory with
`mkdir -m 700 -p secrets` before `init-artifacts` or `start`, so Docker does not
create it as root. Leave `SANDBOX_ENABLED=false` and use
`./scripts/openship-stack -e dev start` without `--sandbox` consistently for
start/status/stop. Messaging and artifact initialization still apply.

## Optional Connector setup

Plain model chat does not require Connectors. Global tool management requires
`CONNECTOR_OPERATOR_USERNAME` to match the exact registered account; Connector
provisioning additionally requires a stable `CONNECTOR_INSTALLATION_ID` UUID and
both host keyrings. Set these values in the root `.env` for Compose, or in
`apps/api/.env` for native processes.

The [keyring initialization command](operations/managed-connectors.md#initialize-keys)
runs on the host, unlike the container-only chat quick start. Install **Python
3.11+** and [uv](https://docs.astral.sh/uv/), then install the API dependencies:

```bash
cd apps/api
uv sync --extra dev
```

Run the keyring command from that same directory. It writes into the private
repository-root `secrets/` directory shared by API and worker containers; do not
store the keys in `.env` or Git. Restart the stack after changing its environment.
Register/sign in with the configured operator username, add provider credentials
on the Connectors page, and enable the relevant tools in Global Settings.

## Configuration beyond the quick start

- **Compose application settings:** copy the root `.env.example` to `.env`.
  PostgreSQL/NATS service addresses, role credential mounts, and the shared
  artifact root are wired by Compose. Do not copy native host paths into them.
- **Native application settings:** copy `apps/api/.env.example` to `apps/api/.env`.
  It is read from the API working directory, not from the repository root.
- **Model configuration:** declare profiles and their credential bindings using
  [provider support](provider-support.md). Missing keys leave profiles unavailable;
  listing a profile does not qualify a model or endpoint.
- **Connectors and tools:** choose a stable `CONNECTOR_INSTALLATION_ID` UUID,
  set `CONNECTOR_OPERATOR_USERNAME` to an exact registered username, and
  [initialize both host keyrings](operations/managed-connectors.md#initialize-keys).
  Add your exact browser origin to `CONNECTOR_BROWSER_ORIGINS` if it differs from
  localhost:5173. Configure provider credentials on the Connectors page, then
  enable tools in Global Settings. `BRAVE_SEARCH_API_KEY`/`TAVILY_API_KEY` in the
  environment are legacy bindings, not the new Connector setup path. See
  [tool availability](operations/tool-availability.md) before setting a deployment ceiling.
- **LAN and port changes:** the dev stack defaults to all host interfaces for API
  and web. Use `DEV_BIND_ADDRESS=127.0.0.1` for workstation-only access. If changing
  the web host/port, update Connector browser origins as well.
- **Operations:** use the [messaging runbook](operations/messaging.md) for startup
  confirmations, NATS NUI (dev port 31312, local port 31311), queue migration,
  recovery, and production requirements. Use the [artifact storage
  runbook](operations/artifact-storage.md) for volume identities and restores.

Do not run separate worktrees against the same stack/database/volumes without
an explicit decision to share them. Preserve installation IDs and keys; never
regenerate them merely to bypass an initialization failure.

## Native / custom local development

Use this path when you want to run the API, worker, relay, and web client in
separate terminals rather than in Compose.

### Prerequisites

- Python **3.11+** and [uv](https://docs.astral.sh/uv/).
- Node.js **22.12+** and [pnpm](https://pnpm.io/installation).
- PostgreSQL **15+**, with a database and login you control.
- NATS at the version pinned in [`deploy/versions.env`](../deploy/versions.env),
  either native or started by the dependency Compose manifest.

### Install and configure

```bash
cd apps/api
uv sync --extra dev
cp .env.example .env
```

Edit `apps/api/.env`: set `DATABASE_URL` to your PostgreSQL connection, replace
`JWT_SECRET`, choose a new `ARTIFACT_STORAGE_ID` UUID, and configure model
profiles/keys. Keep `ARTIFACT_ROOT` persistent and shared by the API and worker.
Configure Connectors separately if needed. The native sandbox endpoint/key paths
must refer to a provider reachable from the host; the example's Compose service
DNS and `/run/secrets` paths do not work unchanged for native processes.

Create your database (for example, `createdb -U postgres openship` with an
appropriately authorized local account), then migrate it:

```bash
# From apps/api
uv run alembic upgrade head
```

### Initialize artifacts and messaging

Back at the repository root:

```bash
./scripts/openship-stack -e local init-messaging
./scripts/openship-stack -e local init-artifacts
./scripts/openship-stack -e local up-dependencies --only nats,nats-nui
./scripts/openship-stack -e local bootstrap --apply
```

Keep `NATS_SERVERS=nats://127.0.0.1:4222` in `apps/api/.env` for this default
broker port. To use Compose PostgreSQL too, start dependencies with no `--only`
selector **before** migrating, and set `DATABASE_URL` to the published port and
credentials in [`docker-compose.dependencies.yml`](../docker-compose.dependencies.yml).

Bootstrap creates broker resources; it does not activate an inactive messaging
installation. For an inactive installation, follow the
[drained-cutover procedure](operations/messaging.md#existing-installation-drained-cutover-proposed)
around native process startup: stop writers, preview/apply, start the new
processes with gates closed, then verify and open the gates in order. Do not
bypass blocked previews.

### Run application processes

Use separate terminals, each starting from `apps/api`:

```bash
NATS_AUTH_FILE=../../.openship/messaging/local/api.json \
  uv run uvicorn src.openship.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
NATS_AUTH_FILE=../../.openship/messaging/local/worker.json \
  uv run python -m src.openship.runs.worker
```

```bash
NATS_AUTH_FILE=../../.openship/messaging/local/relay.json \
  uv run python -m src.openship.messaging.relay
```

For worker/relay automatic reload commands, see the
[messaging runbook](operations/messaging.md#developer-machine-startup). Start the browser
client in another terminal:

```bash
cd apps/web
pnpm install
pnpm dev
```

Open [http://localhost:5173](http://localhost:5173); Vite proxies API calls to
port 8000. API docs are at [http://localhost:8000/docs](http://localhost:8000/docs).
The dependency command also starts local NATS NUI at
[http://localhost:31311](http://localhost:31311). Its imported operator profile
can modify broker state; keep it on a trusted workstation.

## Tests and contributor checks

Install dependencies with `uv sync --extra dev` in `apps/api` and `pnpm install`
in `apps/web`. Ordinary regression checks do not require live model credentials:

```bash
# From apps/api
uv run ruff check .
uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'
```

```bash
# From apps/web
pnpm typecheck
pnpm test:run
pnpm build
```

PostgreSQL/full-stack tests need an explicitly disposable `TEST_POSTGRES_URL`;
JetStream tests need the real broker qualification launcher. Live-provider tests
are opt-in and may incur charges. See [Phase 4 acceptance](testing/phase4-acceptance.md),
[provider support](provider-support.md), and [messaging qualification](operations/messaging.md#qualified-transport-versions).

The always-run prototype test hook also needs its own dev dependencies; otherwise
`uv run pytest` can pick up a host pytest outside the prototype environment.
Install those dependencies and the hooks for each checkout/worktree, from the
repository root:

```bash
uv sync --project poc/agent --extra dev
uvx pre-commit install
uvx pre-commit run --all-files
```

The [hook configuration](../.pre-commit-config.yaml) includes prototype tests,
frontend typecheck/tests, API lint/tests when API files change, and a staged
secret scan. Never commit `.env`, provider credentials, or generated key files.
