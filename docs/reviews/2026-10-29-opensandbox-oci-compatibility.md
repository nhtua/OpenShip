---
type: research
summary: OCI compatibility findings, portable protocol fixes and rootless runsc probe evidence.
date: 2026-10-29
status: protocol-verified-isolation-unqualified
---

# OpenSandbox and OCI compatibility investigation

OpenShip's boundary is the OpenSandbox HTTP API. The manager owns engine
integration; its Docker backend uses Docker-compatible lifecycle and archive
APIs before the engine invokes an OCI runtime. Supporting OCI images and
`runsc` does not establish compatibility with those higher-level APIs. Keep
engine differences in deployment profiles and qualify their actual behavior.
The adapter and trusted probe do not import engine libraries or invoke CLIs.

## Reproduced failures

Tests used rootless Podman 6.1.1, gVisor `release-20260406.0`, the dedicated
sandbox API service, and kernel `7.1.13-2-MANJARO`. Ordinary CLI defaults stayed
on `runc`. Runtime selection was checked through native inspection rather
than the Docker-compatible inspect field, which reports generic `oci` here.

| Boundary | Observation | Resolution or remaining condition |
| --- | --- | --- |
| Temporary execd helper | Unmodified OpenSandbox Server 1.2.0 started the helper, then archive reads of `/execd` returned 404 with runsc. The files existed inside the container. The same running image worked with runc; a created, unstarted runsc helper also worked. | Versioned manager patch reads image artifacts before runtime startup. It preserves runtime configuration and removes the need to execute the helper image. |
| Host-published ports | A trusted execd `/ping` probe worked with runc and reset with runsc on the tested rootless bridge. | Direct bridge access from a peer container to runsc execd worked. The live manager probe used the rootless network namespace and internal IP resolution. Native host-published routing remains unqualified. |
| Released command API | execd `v1.1.0` rejected native `argv` and completed shell commands without an explicit exit code. | Build a compatible pinned upstream revision; preserve fixed argv and explicit integer zero completion. |
| Upload metadata | A multipart text field for metadata returned `400 INVALID_FILE_METADATA`; a named metadata file uploaded successfully and downloaded identically. | Adapter sends `metadata.json` with `application/json`. |
| Command output | execd emitted JSON objects separated by blank lines with `text/event-stream`, without `data:` prefixes. | Adapter accepts these records and standard `data:` events. Missing, malformed or unsuccessful completion remains an error. |

The initial released execd image was
`docker.io/opensandbox/execd@sha256:9b856dad9c73488660522361abfaaa1ddbb9032169bcfb44c83348f46bc1cd9a`.
Its command output was diagnostic only; a completion lacking exit status
never became successful OpenShip execution.

## Verified compatibility slice

Official upstream source revision
`97031ea81fee1b853e4cd89e5490b1be9f5ead55` was checked out, and the
[extraction patch](../../deploy/sandbox/patches/0001-extract-execd-before-runtime-start.patch)
was applied. Its two regression cases failed before the patch, then passed;
the focused upstream archive lane passed **10 tests**. A wider Docker-service
test run stalled after its initial tests and was interrupted; this is not a
claim that the complete upstream suite passes.

The revision's execd binary was compiled with CGO disabled. A **diagnostic**
image replaced `/execd` and `/bootstrap.sh` in the released execd image with
those from the pinned revision. Its local image digest was
`sha256:508e8efd168c346f19b74d254e0c5794678b2f8ef0c7cfb2ad8da7b1ddeb114e`.
Other helper binaries came from the released base; this mixed diagnostic
image is not the qualified production tuple. Use the complete upstream
Dockerfiles described in the [operator guide](../operations/opensandbox.md)
for complete qualification candidates.

The Python workload image was
`docker.io/library/python@sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac`.
The patched manager ran natively inside the rootless network namespace;
the test called its ASGI lifecycle/proxy application in process, with real
engine requests and real execd HTTP traffic. This did not test the Compose
manager image or its external HTTP listener.

The actual OpenShip adapter completed:

1. Sandbox creation through OpenSandbox; native engine inspection reported
   `runsc`.
2. Lifecycle readiness and execd proxy `/ping`.
3. Source upload and SHA-256 readback verification at `/code/main.py`.
4. Fixed native Python argv; observed UID/GID `1000:1000`, expected stdout
   and explicit successful completion.
5. Deletion through the manager; direct engine listing found no matching
   sandbox container.

The reusable
[trusted HTTP probe](../../apps/api/scripts/qualify_opensandbox.py) then also
passed against this manager and execd configuration, including stderr capture
and lifecycle absence after deletion. The probe preserves its attempt UUID
for uncertain-create reconciliation and reports isolation as unqualified.

## Complete source-built HTTP verification

The complete manager and execd Dockerfiles subsequently built successfully
from the same pinned revision, with the extraction patch in the manager.
The following **local Linux/amd64 candidate** manifests were tested:

| Component | Image reference |
| --- | --- |
| Manager | `localhost/openship-opensandbox-server@sha256:734c9b37b3c51ebf929ada5de7172eac0926f62c3cb0efbc4cf036d6efd31632` |
| execd | `localhost/openship-execd@sha256:1c54c8a76eb9af10b1fce1f9315a64f1338236a05d4b7a26c87cdc61e77b9692` |
| Python workload | `docker.io/library/python@sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac` |

Recorded build bases were Python `3.10-slim` manifest
`sha256:7b6bd969dda3ecfca0655524e338b57a8b8cd692a9362e3d55b5a3ef49b07256`,
Go `1.25.9` manifest
`sha256:4c22673170524e09a47a6e1a5f1ca99692f4796c9b4f2de25ec7137bcd897edd`,
and Alpine manifest
`sha256:c26612752c78b18faacd63c7d50f4592c2e7b0f25e0e20e82bd74e465c57ab0d`.
Builds used upstream defaults with the host's `runc` builder. These local
manifests are evidence, not published images or portable registry references.
Release version/build metadata retained upstream development defaults.

A disposable manager container ran with `runc`, mounting only the dedicated
sandbox engine socket and private fixture configuration/metadata. The trusted
HTTP probe reached its real TCP listener through the rootless network namespace;
the manager proxied execd traffic over the bridge. Native inspection during
creation reported sandbox runtime `runsc`. The probe passed source upload and
readback, native argv, UID/GID, stdout/stderr, explicit exit status and manager
deletion confirmation. A separate engine listing after deletion found no
matching sandbox container. The manager container and fixture metadata were
also removed. No application or standard Podman socket settings changed.

The focused OpenShip compatibility, executor, collector, packaging and
preflight lane passed **68 tests**, and API Ruff passed. Independent code
review found no critical or important issues. A minor diagnostic improvement
is deferred: when execution and cleanup both fail, the probe's CLI reports
the cleanup error; preserving both errors would help investigation.

The complete network-free API unit lane passed **1,339 tests** with **123
deselected** (`not postgres and not provider_live and not full_stack and not
jetstream`). Repository prototype tests, frontend unit tests, Vue typecheck
and staged secret scanning also passed. The first commit attempt stopped
because documentation was updated during the API hook; its test suite passed.

## Remaining qualification

Qualify the complete candidate with the protected hardened profile and actual
Compose topology, then pin installation-specific images for deployment.
Repeat on each engine profile before claiming support.
Docker was not live-tested in this investigation. Kubernetes/containerd and
other OCI engines need an appropriate OpenSandbox backend and separate tests.

The diagnostic profile used trusted code with default container security
settings on an ordinary rootless bridge. It did not establish the Phase 6B
hardened profile's UID transition with dropped capabilities, protected
supervision, workload-only network denial, writable-storage bounds, effective
CPU/memory/task limits or deadlines during manager failure. All remain release
gates. No generated-code execution capability was enabled by these probes.

Primary references:

- [Podman API service and its Docker compatibility interface](https://docs.podman.io/en/latest/markdown/podman-system-service.1.html).
- [Docker container archive API](https://docs.docker.com/reference/api/engine/version/v1.51/).
- [Pinned upstream manager extraction implementation](https://github.com/opensandbox-group/OpenSandbox/blob/97031ea81fee1b853e4cd89e5490b1be9f5ead55/server/opensandbox_server/services/docker/runtime.py).
- [Pinned execd command contract](https://github.com/opensandbox-group/OpenSandbox/blob/97031ea81fee1b853e4cd89e5490b1be9f5ead55/specs/execd-api.yaml).
