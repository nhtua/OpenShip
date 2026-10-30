---
type: operations
summary: Preparation and qualification for the optional OpenSandbox Compose service.
status: implementation-in-progress
date: 2026-10-28
---

# OpenSandbox Compose preparation

The optional `docker-compose.sandbox.yml` overlay adds the upstream standalone
OpenSandbox server and a private control network. Only this server mounts the
container-engine socket. The existing worker remains on the application network
and can reach the server over the control network. The server and sandbox
containers use a Compose-managed internal `openship-sandbox-data` bridge with no
external default route.

This overlay is infrastructure preparation. Generated-code execution must stay
disabled until the isolation and recovery checks in the
[Phase 6B specification](../superpowers/specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md)
pass on the actual installed runtime. An internal Docker network does not deny
traffic between peers or guarantee that containers cannot reach host services;
configure and test host-level egress controls before running untrusted programs.
OpenSandbox's gVisor backend does
not support its egress sidecar policy in the documented configuration.

The operator must install and register gVisor `runsc` with the engine, copy
`deploy/sandbox/config.example.toml` to a protected location and replace the
execd image placeholder with a compatible digest-pinned upstream image. The
profile uses OpenSandbox's `execd_run_as_init` mode for child reaping, and only
publishes sandbox host ports on loopback; the server uses internal sandbox IPs
for proxy traffic. Compose creates the internal data network when the sandbox
profile starts. Supply
`OPENSHIP_SANDBOX_SERVER_IMAGE` as a
compatible digest-pinned server image, `OPENSHIP_SANDBOX_CONFIG` as the absolute
config path, and `OPENSHIP_SANDBOX_API_KEY` as a secret. Rootless Podman uses
the dedicated socket described below; its isolation and resource controls
remain subject to qualification. Do not reuse application credentials.

The overlay has no public port mapping. The upstream server/proxy routing,
server image healthcheck, non-root program execution, output limits, storage
quotas, deadline behavior during server failure, and network isolation require
real-host qualification; this file does not claim they already work. Pin the
server and execd images together and record their digests in qualification
evidence. Keep the API key out of committed files and application logs.

Run `scripts/check_opensandbox_config.py <protected-config.toml>` before
starting the service. It checks static configuration only; OpenSandbox must
also start successfully and its engine must prove that every created sandbox
uses `runsc`. The Python image is built from a separately supplied
digest-pinned `PYTHON_BASE_IMAGE`; record the resulting image digest and set it
as `OPENSHIP_SANDBOX_PYTHON_IMAGE`. The current Docker backend has no documented
per-sandbox writable-layer size limit, so do not enable execution until host
storage enforcement or a qualified bounded writable mount is in place.

After preparing the settings above, opt in to the dev overlay with
`scripts/openship-stack -e dev --sandbox start`. Use `--sandbox` with `status`
and `stop` as well. Omitting the option leaves ordinary stack startup unchanged.
Production and native-process packaging need separate qualification.

## Rootless Podman development

OpenSandbox has no `rootless` TOML setting. Keep the example's Docker API
backend and `secure_runtime.docker_runtime = "runsc"`; connect the manager
to a rootless Podman API using `DOCKER_HOST`. The Compose overlay mounts the
host socket at `/var/run/docker.sock` inside the manager.

Separate CLI and sandbox API profiles are needed on the tested Podman 6.1.1 installation:
its Docker-compatible create endpoint ignores `HostConfig.Runtime`, so sending
`Runtime: runsc` to the ordinary socket creates a `runc` container. The supplied
user units start a second socket-activated API with `--runtime=runsc` scoped to
that service. Ordinary CLI commands retain `runc`; the standard `podman.socket`
is optional and can stay disabled. The CLI and sandbox API share the same user's
container storage; the dedicated socket is a runtime selection mechanism,
not an access-control boundary between engines.

On Arch Linux or a derivative, the repeatable setup entry point is:

```sh
scripts/bootstrap/bootstrap.sh arch-podman --check
scripts/bootstrap/bootstrap.sh arch-podman
```

Use `--configure-only` when dependencies are already installed. This manages
only the sandbox socket and leaves the standard Podman API unmanaged. See
the [Ansible bootstrap guide](../../scripts/bootstrap/README.md) for package,
upgrade, prerequisite and verification behavior. The equivalent manual steps
follow.

1. Install the runtime flags and user units (run as your ordinary user):

   ```sh
   install -D -m 600 scripts/bootstrap/roles/rootless_podman/files/containers.rootless.conf \
     ~/.config/containers/containers.conf.d/90-openship-sandbox.conf
   install -D -m 644 scripts/bootstrap/roles/rootless_podman/files/openship-sandbox-engine.service \
     ~/.config/systemd/user/openship-sandbox-engine.service
   install -D -m 644 scripts/bootstrap/roles/rootless_podman/files/openship-sandbox-engine.socket \
     ~/.config/systemd/user/openship-sandbox-engine.socket
   systemctl --user daemon-reload
   systemctl --user enable --now openship-sandbox-engine.socket
   ```

   The installed Podman must support `[engine.runtimes_flags]`, as documented
   in its shipped `containers.conf`. `[engine.runtimes]` contains executable
   paths; flags go under `[engine.runtimes_flags]` without their leading `--`:

   ```toml
   [engine]
   runtime = "runc"
   [engine.runtimes]
   runsc = ["/usr/bin/runsc"]
   [engine.runtimes_flags]
   runsc = ["ignore-cgroups"]
   ```

   After changing flags, restart `openship-sandbox-engine.service` if active.
   Restarting this API process does not restart existing containers. Do not
   add `--network=host` to the runtime profile; retain gVisor's network stack
   and qualify workload egress separately.

2. Set `CONTAINER_ENGINE=podman` in `.env` and prepare the pinned images,
   protected server config and API key described above. The stack helper
   automatically adds `docker-compose.sandbox.rootless.yml` with `--sandbox`.
   It uses `$XDG_RUNTIME_DIR/openship-sandbox/podman.sock`; set
   `OPENSHIP_SANDBOX_ENGINE_SOCKET` only to override that dedicated socket.
   Verify that this path is a socket before starting Compose; the short bind
   syntax required by podman-compose can create a directory if it is missing.
   The manager runs with `runc`, as UID 0 **inside the rootless user namespace**,
   and disables SELinux labeling for its socket access. The worker receives
   no socket mount. Leave `proxy.resolve_internal = true` so the manager on
   `sandbox-data` reaches sandbox IPs directly. A native server instead needs
   a separately qualified endpoint routing configuration.

3. Start with `scripts/openship-stack -e dev --sandbox start`. For a native
   development server, use the same dedicated engine endpoint:

   ```sh
   export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/openship-sandbox/podman.sock"
   opensandbox-server --config /absolute/path/to/protected-config.toml
   ```

   Check local `podman info`: the default runtime must be `runc`. The dedicated
   API's `/info` must report `runsc`; no ordinary API socket is required.
   Inspect a created container with
   `podman inspect --format '{{.OCIRuntime}}' <container-id>` to verify `runsc`.
   Podman's Docker-compatible inspect reports the generic runtime `oci`, so
   that field cannot prove gVisor selection.

The direct CLI equivalent for a disposable trusted probe is:

```sh
podman run --runtime=runsc --runtime-flag=ignore-cgroups --rm alpine \
  echo "Hello from gVisor!"
```

`ignore-cgroups` prevents runsc from managing cgroups; successful startup is
not proof of the Phase 6B CPU, memory or task limits. Keep generated-code
execution disabled until effective resource, storage, network and deadline
controls pass qualification on this profile.

References: [Podman API service](https://docs.podman.io/en/latest/markdown/podman-system-service.1.html),
[Podman runtime selection and flags](https://github.com/containers/podman/blob/main/libpod/oci_conmon_common.go),
[OpenSandbox configuration](https://github.com/opensandbox-group/OpenSandbox/blob/main/server/configuration.md).

## Container engine compatibility

OpenShip uses OpenSandbox's lifecycle and execd HTTP APIs. It does not invoke
an engine CLI, mount an engine socket or choose a runtime in application code.
The manager's `docker` backend requires a compatible Docker Engine API; the
engine then invokes the configured OCI runtime. OCI compliance covers that
lower boundary, not Docker Engine API behavior. A different OCI engine needs
an OpenSandbox-supported backend, such as the Docker-compatible API or the
Kubernetes backend; OCI support alone is insufficient.

The installed rootless Podman/runsc combination exposed an upstream extraction
problem: the engine archive API could not read execd image files after its
temporary helper started. Reading those files from the created helper worked.
The versioned
[upstream patch](../../deploy/sandbox/patches/0001-extract-execd-before-runtime-start.patch)
removes that unnecessary start without detecting the engine or changing the
workload runtime. It includes upstream regression tests for cache reuse and
cleanup after successful or failed reads. Its source base is
[`97031ea81fee1b853e4cd89e5490b1be9f5ead55`](https://github.com/opensandbox-group/OpenSandbox/tree/97031ea81fee1b853e4cd89e5490b1be9f5ead55).
This patch is maintained here pending an upstream fix; it has not been
submitted upstream. Remove it once a qualified release contains the fix.

The pinned upstream server also exempts single-tenant HTTP and WebSocket proxy
routes from API-key authentication. OpenShip requires the
[proxy authentication patch](../../deploy/sandbox/patches/0003-authenticate-manager-proxy.patch)
when building this revision. It authenticates both transports whenever a server
key is configured; the protected OpenShip configuration and static preflight
must also require a nonempty key. The proxy must be tested on the built image.

The separate [experimental capability patch](../../deploy/sandbox/patches/0002-experimental-capability-additions.patch)
was tested only with trusted fixtures. It lets the supervisor switch UID/GID
when all capabilities are otherwise dropped, but its grants apply to the
container and the workload retained those capabilities in its bounding set.
It is not part of the supported build instructions or a qualified profile.
The static preflight rejects capability additions while the remaining
supervisor, workload and isolation controls are investigated.

execd `v1.1.0` rejects native `argv` and omits the exit status needed by
OpenShip. Build manager and execd from the pinned revision above for further
qualification. Do not substitute shell execution or infer successful exit
from a completion event without an integer zero exit status. OpenShip accepts
both `data:` events and execd's blank-line-delimited JSON objects, and sends
upload metadata as a named multipart file as required by execd.

For an operator-controlled upstream checkout, apply and test the patch before
building. Set these paths to your checkouts; use a clean upstream checkout.

```sh
OPENSHIP_SOURCE=/absolute/path/to/openship
OPENSANDBOX_SOURCE=/absolute/path/to/OpenSandbox
CONTAINER_ENGINE=podman # or another builder with the same build interface
git -C "$OPENSANDBOX_SOURCE" checkout --detach 97031ea81fee1b853e4cd89e5490b1be9f5ead55
git -C "$OPENSANDBOX_SOURCE" apply --check "$OPENSHIP_SOURCE/deploy/sandbox/patches/0001-extract-execd-before-runtime-start.patch"
git -C "$OPENSANDBOX_SOURCE" apply "$OPENSHIP_SOURCE/deploy/sandbox/patches/0001-extract-execd-before-runtime-start.patch"
git -C "$OPENSANDBOX_SOURCE" apply --check "$OPENSHIP_SOURCE/deploy/sandbox/patches/0003-authenticate-manager-proxy.patch"
git -C "$OPENSANDBOX_SOURCE" apply "$OPENSHIP_SOURCE/deploy/sandbox/patches/0003-authenticate-manager-proxy.patch"
cd "$OPENSANDBOX_SOURCE/server"
uv sync --frozen
uv run pytest -q tests/test_execd_created_container.py tests/test_docker_service.py -k execd_archive
uv run pytest -q tests/test_auth_middleware.py tests/test_openship_proxy_auth.py
"$CONTAINER_ENGINE" build -f "$OPENSANDBOX_SOURCE/server/Dockerfile" \
  -t openship-opensandbox-server:compat-97031ea "$OPENSANDBOX_SOURCE/server"
"$CONTAINER_ENGINE" build -f "$OPENSANDBOX_SOURCE/components/execd/Dockerfile" \
  -t openship-execd:compat-97031ea "$OPENSANDBOX_SOURCE"
```

These are source-build instructions, not a qualified image tuple. The upstream
Dockerfiles contain mutable base tags. Record builder/base digests, source
revision, patch and build options; resolve and pin the resulting manager,
execd and Python image digests in the protected deployment configuration.
Keep execd's legacy SSE compatibility setting disabled so it retains exit codes.
Qualify each deployment profile against its actual engine, runtime and network.

## Trusted protocol probe

With a test manager running, run the same trusted fixture against any backend:

```sh
cd apps/api
# Supply these through the environment, using a dedicated test API key:
# OPENSHIP_SANDBOX_SERVER_URL, OPENSHIP_SANDBOX_API_KEY,
# OPENSHIP_SANDBOX_PYTHON_IMAGE (digest pinned).
uv run python -m scripts.qualify_opensandbox --trusted-probe
```

The probe creates one sandbox, waits for execd, uploads a fixed trusted Python
program, verifies the downloaded source digest, executes the fixed native argv,
checks UID/GID `1000:1000`, stdout, stderr and explicit successful completion,
then requests deletion and waits for lifecycle `404`. It never retries create
or command POSTs. It prints an attempt UUID before creation for reconciliation
if the response is lost. A failure exits nonzero; reconcile an uncertain
attempt before repeating the probe. The receipt's
`manager_deletion_confirmed` describes the manager response; independently
inspect engine resources to prove physical cleanup and runtime selection.

The probe requires no engine-specific imports, CLI or socket. Run it from a
process that can reach the manager. The manager must also be able to reach
execd: use the shared internal data bridge and `proxy.resolve_internal = true`
in the Compose topology. A native host process may need a different network
namespace or separately qualified route. Host-published runsc ports failed on
the tested rootless profile despite working internal bridge connections.

Success is HTTP protocol compatibility evidence and always reports
`isolation_qualified: false`. The fixture does not establish workload resource,
filesystem, storage, network or supervisor isolation. See the
[compatibility investigation](../reviews/2026-10-29-opensandbox-oci-compatibility.md)
for the tested versions, diagnostic build limitations and remaining checks.
