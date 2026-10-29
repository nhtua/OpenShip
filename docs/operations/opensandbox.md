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
config path, and `OPENSHIP_SANDBOX_API_KEY` as a secret. If using a
Docker-compatible Podman socket, set `OPENSHIP_SANDBOX_ENGINE_SOCKET` to that
socket's absolute path; Podman remains unqualified until its actual controls
pass the tests in the spec. Do not reuse application credentials.

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
