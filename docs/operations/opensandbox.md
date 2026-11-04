---
type: operations
summary: Carried OpenSandbox development setup; provider owns 30-minute sandbox lifecycle.
status: infrastructure-prepared-application-not-implemented
date: 2026-11-03
---

# OpenSandbox development setup

The [revised 6B spec](../superpowers/specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md)
and [plan](../superpowers/plans/2026-10-28-phase6b-opensandbox-implementation.md)
define the small client integration. This worktree carries operator setup,
**not** the abandoned application implementation or its completion claims.
No host provisioning, live provider run or new isolation qualification is
claimed by the scope reset. Do not reuse the old branch's migrated database
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
   protected worker configuration when implementing the client.
2. Copy `deploy/sandbox/config.example.toml` to a protected installation-specific
   path and replace `REPLACE_WITH_PINNED_EXECD_IMAGE`. Its maximum sandbox timeout
   is **1,800 seconds**, permitting the client's requested 30-minute lifetime.
   This maximum alone does not set a sandbox TTL: the application create request
   must supply `1800` (planned work, not implemented here).
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
development stack. It does not register/enable a generated-code capability in
this fresh worktree. The manager refuses to start without an API key; missing
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

The pre–Task 4 correction gate and full real parser journey remain in progress
in the implementation plan; current tests cover generated-source handoff and
conversation-scoped reuse. Run one reproducible real-service demonstration
after the correction gate. A dedicated qualification
launcher/runner/CI certification platform, security scans and production matrix
are deferred. Optional host probes do not certify provider isolation.
