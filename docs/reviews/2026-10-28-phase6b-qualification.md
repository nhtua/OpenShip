---
type: qualification
summary: Phase 6B protocol and hardening candidate evidence; execution remains unqualified.
date: 2026-10-29
status: in-progress
---

# Phase 6B qualification evidence

These are trusted diagnostic fixtures, not approval to run generated code.
The tested host used rootless Podman 6.1.1, gVisor `release-20260406.0`,
kernel `7.1.13-2-MANJARO`, cgroups v2 and systemd. The manager used `runc`;
native engine inspection reported `runsc` for the workload. The ordinary
Podman CLI retained its `runc` default.

## Candidate images and source

The manager and execd use upstream OpenSandbox commit
`97031ea81fee1b853e4cd89e5490b1be9f5ead55`. The experimental manager image
included all three local patches: archive extraction, capability additions,
and HTTP/WebSocket proxy authentication. The capability addition patch is
experimental and normal configuration preflight rejects its use.

| Component | Local candidate manifest digest |
| --- | --- |
| Manager | `sha256:00c9219963425b66184c5fb5ad7dfc16c3f3c8dafbb16ad30c4c018bc249a838` |
| execd | `sha256:1c54c8a76eb9af10b1fce1f9315a64f1338236a05d4b7a26c87cdc61e77b9692` |
| Python image | `sha256:12debefaa32ff708d3aeb8e764a83da4a5e76a8da67184f1311cf74a88198fe0` |
| Python hardening-probe image | `sha256:513dbf591977d97ae2129bee98ca45cecbbeee0fe14e064c5d76d51ca41a0e55` |

The manifest digests identify local images; they are not published release
artifacts. Build instructions, prior image inputs and the standard manager
patches are in the [operations guide](../operations/opensandbox.md).

## Candidate inventory and vulnerability scan

Syft **1.55.0** inventoried the exact local image IDs through the dedicated
engine service. CycloneDX inventories are recorded for the
[manager](evidence/phase6b-2026-10-29/manager.cdx.json),
[execd](evidence/phase6b-2026-10-29/execd.cdx.json), and
[Python image](evidence/phase6b-2026-10-29/python.cdx.json). They contain
177, 220 and 122 package records respectively. The daemon's exported Docker
manifest digests differ from the local OCI manifest digests above; the image
configuration IDs in the scan output match the inspected local image IDs.

Grype **0.121.0**, database built `2026-10-29T06:23:34Z`, scanned the Syft
inventories. These are preliminary package/advisory matches, not confirmed
exploitable vulnerabilities; duplicate findings can refer to the same
advisory in multiple binaries. No findings were suppressed.
The versioned [manager](evidence/phase6b-2026-10-29/manager.grype-summary.json),
[execd](evidence/phase6b-2026-10-29/execd.grype-summary.json) and
[Python](evidence/phase6b-2026-10-29/python.grype-summary.json) summaries
retain each match's advisory, package, severity and fix metadata; they omit
scanner configuration and filesystem paths from the full reports.

| Candidate | Total matches | Critical | High | Other |
| --- | ---: | ---: | ---: | ---: |
| Manager | 180 | 0 | 58 | 122 |
| execd | 169 | 11 | 106 | 52 |
| Python | 176 | 0 | 57 | 119 |

The execd scanner warned that stripped Go symbols force module-level matching
and may produce false positives. Its critical matches include Go standard
library and `golang.org/x/net` advisories, including `GO-2026-6612` and
`GO-2026-6604`. Triage, upgrade where appropriate,
rebuild, retest and rescan before selecting a release tuple. The Go builder
used by the upstream Dockerfile was `1.25.9`; see the
[Go release history and support policy](https://go.dev/doc/devel/release)
when selecting a supported builder. Results are time-bound to this database
and these diagnostic images; they do not qualify later builds.

## Authentication and protocol

Unmodified upstream single-tenant proxy routes bypass a configured API key.
An initial real test observed HTTP `200` without credentials; four other
trusted protocol cases passed. The manager patch requires the configured key
for HTTP and WebSocket proxy requests and rejects unauthorized WebSocket
handshakes. It preserves upstream's no-key mode, which OpenShip configuration
preflight does not permit.

Upstream HTTP/WebSocket authentication tests passed **13 cases**. The
WebSocket route test uses a working simulated backend and valid-key positive
control; restoring the old bypass made that test fail. The real HTTP/WS
protocol lane ran **5 tests with no skips** through the patched manager. The
real WebSocket negative probe targets a non-WebSocket endpoint, so the route
test provides the positive control. The sandbox marker is excluded from normal
unit collection; explicitly selecting it fails if manager settings are absent.

Attempt-scoped listing and lifecycle inspection were also exercised against
the real manager, including a separate attempt that returned no matches.
Offset pages are not a snapshot: concurrent changes can move entries between
pages. Neither a complete listing nor manager-reported `404` proves physical
container cleanup; recovery must use additional qualified evidence.

## Supervisor capability probes

With the intended Python image, `drop_capabilities = ["ALL"]` and
`no_new_privileges = true`, upload succeeded but execd could not switch to
UID/GID 1000: its supervisor lacked `SETUID` and `SETGID`. An experimental
upstream extension allowed just those additions; trusted Python execution and
physical deletion succeeded. In a separate CLI probe the resulting workload
had empty effective, permitted and inheritable capability sets but retained
those capabilities in its bounding set.

An additional trusted CLI test showed that `SETPCAP` lets the supervisor
drop the entire bounding set before switching to UID/GID 1000. A complete
manager/execd launcher probe with `SETUID`, `SETGID` and `SETPCAP` observed
zero inheritable, permitted, effective and bounding capability sets and
`PR_GET_NO_NEW_PRIVS = 1`; the sandbox was physically absent after deletion.

The negative control omitted `SETPCAP`: execd reported `cap_drop` as
`degraded`, but still executed the program with a nonzero bounding set.
Physical deletion was checked separately. The upstream launcher is fail-open;
this profile must not be enabled until missing or failed hardening causes
execution to fail closed. Network isolation, writable storage, resource
limits and independent deadlines remain unqualified.
