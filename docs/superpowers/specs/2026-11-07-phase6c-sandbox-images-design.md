---
type: spec
summary: Four prebuilt offline sandbox images extending Phase 6B without changing execution, mounts, networking or credential authority.
status: scope-agreed-design-awaiting-review
date: 2026-11-07
---

# Phase 6C — Prebuilt Sandbox Images

## Intent and agreed scope

OpenShip's generated-code sandbox cannot download dependencies at runtime.
Provide four maintained, prebuilt workload images with useful libraries and
CLI tools, selected through the existing operator configuration. The user
named this increment **Phase 6C** and explicitly limited it to new images.
The user also approved local builds through `scripts/openship-stack`, explicit
build/smoke commands and an opt-in build-on-start flag. A registry account is
not required.

Directory mounting, network access and connected infrastructure execution are
**deferred, not rejected**. This phase neither implements them nor rules out
their later addition. The image contents must not be confused with execution
permissions or a new shell tool.

This document awaits review. No new image build, runtime verification or
dev-stack acceptance is claimed by its existence.

## Existing foundation

Phase 6B is merged in PR #32 at `60f0917`; the user confirmed completion.
The repository defines `python311` / `stdlib` and the separate
`python311-plot` / `matplotlib-3.9.2-numpy-2.1.0` workload identities.
The latter includes NumPy and Matplotlib's supporting packages, not a full
data-analysis environment. Preserve both legacy Dockerfiles and identities.

`SANDBOX_IMAGE`, `SANDBOX_PROFILE_ID` and `SANDBOX_DEPENDENCY_IDENTITY`
configure a single operator-selected runtime. Existing runtime requirements
bind sandbox compatibility to that identity. The execution client invokes
Python as UID/GID 1000, stages selected artifact bytes into a fresh directory,
and publishes only declared, validated outputs. No interface changes are
needed to supply additional prebuilt images.

## Packaging approach

Use a small shared Python 3.12+ workload base with the existing workload user
and filesystem layout, then build four explicit image variants. This avoids
duplicating execution prerequisites while keeping task-specific dependencies
separate. Every new image retains the `python` entrypoint expected by 6B.

Use one shared base Dockerfile and a separate Dockerfile for each profile:

```text
deploy/sandbox/
  Dockerfile.base
  profiles/
    logs-data/
      Dockerfile
      requirements.in
      requirements.lock
    iac-validation/
      Dockerfile
      requirements.in
      requirements.lock
    standard/
      Dockerfile
      requirements.in
      requirements.lock
    devops-shell/
      Dockerfile
      requirements.in
      requirements.lock
  images.json
```

Keep the existing legacy Dockerfiles in place. Shared setup belongs in the
base, not in four copied sets of user/directory instructions. Each profile's
Dockerfile owns its specialized installation steps.

Alternatives considered:

- One all-tools image simplifies selection but forces every task to carry
  the cloud CLIs and their larger dependency/security footprint.
- Four unrelated upstream images reduce some installation work but duplicate
  workload setup and make Python compatibility and dependency isolation harder.

The shared base plus separate variants is recommended. Do not introduce a
runtime plugin registry, automatic dependency installation or profile router.

## Image inventory

| Profile | Required contents | Representative offline verification |
| --- | --- | --- |
| `logs-data` | Python 3.12+; standard `csv`, `json`, `math`, `decimal`, `uuid`, `re`; Pandas, NumPy, Matplotlib, `regex`, pygrok, Drain3; required transitive libraries. | Read CSV/JSON, aggregate data, extract metadata with grok/regex, cluster repeated log messages, render PNG using headless Agg. |
| `iac-validation` | Python 3.12+; jsonschema, PyYAML, Checkov, TFLint; Graphviz executable and Python bindings; required transitive libraries. | Accept/reject small schema fixtures, load YAML, scan local IaC without remote integrations, run TFLint's bundled functionality, render a diagram. |
| `standard` | Python 3.12+; Node.js runtime; SymPy and `regex`; standard math/utility modules. | Exercise symbolic math and standard utilities; run a local Node.js expression without package installation. |
| `devops-shell` | Python 3.12+ for current execution compatibility; Bash, Git, AWS CLI, Azure CLI, Google Cloud CLI, Terraform, Terragrunt. | Report tool versions; run a local shell script and Git fixture; format a local Terraform fixture without providers, credentials or network. |

Node.js does not imply npm packages are preinstalled. The cloud image does not
promise every optional cloud CLI component or extension. Inventory bundled
components explicitly. Isolate CLI-specific Python environments where needed
so their dependencies cannot overwrite the shared workload environment.

The standard library is part of Python; do not install separate packages named
after `csv`, `json`, `math`, `decimal` or `uuid`.

## Pins, identity and build tooling

Extend `deploy/sandbox/` and its carried image inventory additively:

- Record each new profile's Dockerfile, Python/runtime versions, dependency
  pins and dependency identity, without rewriting historical build records.
- Resolve actual compatible versions during implementation; commit exact
  Python dependency closures/locks, including transitive packages and hashes.
  Maintain direct requirements in `requirements.in`; generate
  `requirements.lock` with a pinned `uv` tool version for the target Python
  and supported platform. Image builds install the committed lock with hash
  verification and must not resolve newer versions. Isolated CLI Python
  environments need their own complete lock when installed from Python
  packages. Do not fabricate versions, digests or build results.
- Pin base images by digest. Pin downloaded CLI archives and verify their
  published checksums or signatures before installation. Avoid floating
  `latest` tags and unverified download-to-shell installation.
- Pin OS package versions against a recorded repository snapshot so old
  versions remain available and package dependency resolution is bounded.
  Record the installed package inventory as build evidence. Pinning inputs
  does not by itself promise byte-identical image builds.
- Keep each version in its authoritative dependency lock or tool manifest;
  the inventory references those sources rather than maintaining duplicate
  hand-edited version lists in Dockerfiles and `images.json`.
- Provide explicit build/list/smoke tooling usable with Docker or Podman.
  Builds must target the engine used by the sandbox manager; an image in a
  different engine's local storage is not available to that manager.
- Fail clearly on unsupported platform/tool combinations or missing build
  prerequisites. Record the platform actually built and tested; do not claim
  a multi-platform support matrix without corresponding evidence.
- Compute a build-input fingerprint from the shared base and selected profile
  recipes, dependency/tool/OS pins, installation helpers and target platform.
  Use it in a local tag such as `openship-sandbox-logs-data:<fingerprint>`.
  A recipe fingerprint is not an image digest: inspect and record the actual
  local image ID separately, along with platform and dependency identity.
  Local builds need not have a registry `RepoDigest`; do not invent one or
  assume that a local image ID is a pullable registry manifest digest.
- Store engine-specific build records in gitignored local state, not in the
  committed release inventory. Verify the recorded image ID and fingerprint
  against the selected engine before reuse. Preserve existing legacy identity
  fields and historical build records.

Dependency updates are reviewed source changes: edit direct requirements or
tool/base/OS pins, regenerate the affected locks, review the version/hash diff,
rebuild and run offline smoke checks. Builds and startup consume committed
pins; neither upgrades dependencies implicitly. Keep prior built identities
available for explicit operator rollback rather than deleting them on update.

Build-time package downloads are allowed; execution-time package downloads are
not. Images and build contexts must contain no credentials, operator secrets,
host caches or private repository checkouts. Confirm the build context excludes
such material rather than relying only on selective `COPY` instructions.

## Local build and startup interface

Add these commands to `scripts/openship-stack` for the dev environment:

```sh
scripts/openship-stack -e dev sandbox-images list
scripts/openship-stack -e dev sandbox-images build logs-data
scripts/openship-stack -e dev sandbox-images build --all
scripts/openship-stack -e dev sandbox-images smoke logs-data
scripts/openship-stack -e dev --sandbox start --build-sandbox
```

These are the specified 6C interface, not commands already implemented.

- **List:** show all four managed profiles, their required fingerprint and
  platform, and local status (missing, stale or matching). Report build and
  smoke evidence separately; an image existing does not mean its checks passed.
- **Build:** build the shared base and requested profile, or all four with
  `--all`. Reuse matching images and normal build cache; do not rebuild every
  profile for a single selected workload. Successful output includes the
  actual image ID and matching values for the existing three runtime settings.
- **Smoke:** check an already built, matching image using network-disabled,
  unprivileged local fixtures. Fail on missing/stale images or failed checks;
  do not implicitly build, install packages or acquire cloud credentials.
- **Normal sandbox start:** preflight the selected 6C managed image on the
  sandbox manager's engine. If missing, stale or inconsistent with the
  configured profile/dependency identity, stop before application startup
  side effects and print the corrective build/selection commands. Matching
  images incur no build. Keep legacy operator-managed profiles on their
  existing configuration path; do not require nonexistent 6C records for them.
- **Opt-in build-on-start:** `start --build-sandbox` permits building the
  selected managed profile if missing/stale before continuing normal sandbox
  startup. It requires the sandbox option and dev environment, never builds
  all profiles implicitly, and must not silently substitute a different
  profile or accept conflicting configured runtime identities. Reject misuse
  with a clear error. Build failures stop startup; no stale-image fallback.

Use the actual engine endpoint serving OpenSandbox, including the relevant
rootful/rootless socket, rather than assuming an executable named `docker` or
`podman` selects the correct image store. Fail clearly on an engine mismatch.
Verify that the provider can use the preloaded local image without publishing
it or attempting a private registry pull.

Registry publishing is optional future distribution, not a prerequisite for
building, testing or selecting these images. Base/package downloads still
need build-time connectivity unless already cached. No builds or downloads run
as a side effect of an agent tool invocation.

New image builds are explicit operator actions, either through the build
command or the opt-in startup flag. Ordinary startup does not build or update
images. Do not rewrite `.env`, rotate secrets or modify protected provider
configuration when building. The preparation task must leave the running dev
stack unchanged; the user owns the final startup/recreation action.

## Execution and access boundaries

Preserve all existing network, filesystem, credential, resource and lifecycle
settings. Retain the workload UID/GID and headless plotting configuration;
provide only the temporary writable cache/configuration paths needed by
offline tools. Do not mount host home directories or CLI credential stores.

The existing tool accepts Python source. Python can invoke a packaged CLI
through `subprocess`, subject to the same execution limits; 6C does not add
shell-source arguments, workflow checkout steps or a shell execution API.
Do not advertise arbitrary directory inputs as supported: current inputs are
selected artifacts, not mounted repositories.

Installing tools is not proof that all their operations work offline:

- Terraform validation requires the referenced providers and modules to be
  installed. Arbitrary provider/module mirrors are not bundled in this phase.
- TFLint external plugins, Checkov external integrations and optional cloud
  CLI components may require separately packaged dependencies. Do not trigger
  remote downloads during smoke checks.
- Cloud CLI calls and general Terraform/Terragrunt planning/apply remain
  unavailable without future network/credential/backend integration.
- Existing execution limits remain unchanged. Do not raise memory or timeout
  limits silently to accommodate a tool; report any measured incompatibility.

## Verification and failure behavior

Use the existing API packaging tests for inventory, Dockerfile and tooling
contracts. Add tests first, demonstrate expected failures, and then implement
the packaging changes. Include failure checks for unknown profiles, missing
images and unsupported platforms where the build tooling handles those cases.
Exercise build-command dispatch, single-profile versus all-profile selection,
cache reuse, changed-input fingerprints, local image-ID drift and engine
selection. Verify normal startup does not build; explicit build-on-start
builds only the selected missing/stale profile, rejects invalid combinations,
and cannot continue after a build failure or conflicting runtime selection.

Run image smoke checks with networking disabled and UID/GID 1000. Use only
trusted local fixtures, no cloud credentials or actual infrastructure targets.
Check operations and exit statuses, not imports alone: analysis must produce
a chart and cluster logs, validators must distinguish valid/invalid fixtures,
and the CLI image must exercise local commands as well as version reporting.
Capture tool versions and image identities with results. A missing image or
failed operation must fail the explicitly requested check rather than skip it.

Smoke checks establish image functionality under those conditions, not
OpenSandbox/gVisor certification or production isolation. Preserve the existing
credential-free provider policy; do not claim unchanged controls were newly
qualified by adding Dockerfiles.

Before implementation starts, the implementation plan will name the exact
build/smoke commands and verification sequence. During implementation, record
actual build/test outcomes and any blockers; do not mark build acceptance
complete based only on static packaging tests.

## Operator experience and acceptance

Update `docs/operations/opensandbox.md` with:

1. A profile/library/tool inventory and explicitly documented offline limits.
2. Exact build, preload and network-disabled smoke commands for the supported
   engine/platform, including resulting immutable image identities. Local
   builds need no registry login; include list and opt-in startup examples.
3. Examples selecting each profile through the existing three runtime settings.
   Keep both legacy profiles and the existing defaults unchanged.
4. A clear dev-stack handoff: rebuild/preload workload images on the correct
   engine, update the matching runtime identity, and recreate relevant
   application services. A process reload alone does not rebuild images or
   necessarily pick up changed container environment settings.
5. A user demonstration selecting `logs-data` and producing a retained chart
   through the existing Python execution path after stack recreation.

Tell the user when preparation is ready and provide the concrete commands;
leave restarting the running stack to them. Full-stack demonstration and user
acceptance remain pending until actually performed and confirmed.

## Future options remain open

The fourth image prepares binaries for later DevOps workflows. Repository
checkout, isolated directory staging/mounting, mutable workflow workspaces,
first-class shell execution, per-task profile selection and UI controls remain
separate design work. Network-enabled execution and scoped cloud credentials
can be added through the existing authorization/connector boundaries; real
Terraform planning and exact-plan apply connect to roadmap Phases 16 and 17.
No blanket prohibition or implicit authorization is introduced here.

## Design review checklist

- [x] Existing image definitions and execution/configuration path reviewed.
- [x] Four-image scope and deferral of mount/network changes confirmed by user.
- [x] Local builds, dependency pinning and opt-in build-on-start approach
  approved in conversation; incorporated into this written design.
- [x] Design self-reviewed for scope, identity compatibility and evidence claims.
- [ ] User reviews this written design.
- [ ] Implementation plan reviewed and execution method selected.
- [ ] Images implemented, built and verified; dev-stack handoff delivered.
