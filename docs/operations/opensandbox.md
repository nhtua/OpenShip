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

Two engine API profiles are needed on the tested Podman 6.1.1 installation:
its Docker-compatible create endpoint ignores `HostConfig.Runtime`, so sending
`Runtime: runsc` to the ordinary socket creates a `runc` container. The supplied
user units start a second socket-activated API with `--runtime=runsc` scoped to
that service. The ordinary socket and CLI retain `runc`. Both APIs share the
same user's container storage; the second socket is a runtime selection
mechanism, not an access-control boundary between engines.

1. Install the runtime flags and user units (run as your ordinary user):

   ```sh
   install -D -m 600 deploy/sandbox/containers.rootless.conf \
     ~/.config/containers/containers.conf.d/90-openship-sandbox.conf
   install -D -m 644 deploy/sandbox/openship-sandbox-engine.service \
     ~/.config/systemd/user/openship-sandbox-engine.service
   install -D -m 644 deploy/sandbox/openship-sandbox-engine.socket \
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

   Check the engine's `/info`: the ordinary socket must report `runc`, and
   the dedicated socket must report `runsc`. Inspect a created container with
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
