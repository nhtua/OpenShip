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
  Python dependency closures/locks, including transitive packages, and record
  system package versions and repository/base provenance. Do not fabricate
  versions, digests or build results.
- Pin base images by digest. Pin downloaded CLI archives and verify their
  published checksums or signatures before installation. Avoid floating
  `latest` tags and unverified download-to-shell installation.
- Provide explicit build/list/smoke tooling usable with Docker or Podman.
  Builds must target the engine used by the sandbox manager; an image in a
  different engine's local storage is not available to that manager.
- Fail clearly on unsupported platform/tool combinations or missing build
  prerequisites. Record the platform actually built and tested; do not claim
  a multi-platform support matrix without corresponding evidence.
- Record resulting immutable image identities and a dependency identity
  covering the packaged closure. Preserve existing legacy identity fields.

Build-time package downloads are allowed; execution-time package downloads are
not. Images and build contexts must contain no credentials, operator secrets,
host caches or private repository checkouts. Confirm the build context excludes
such material rather than relying only on selective `COPY` instructions.

New image builds are explicit operator actions, not an automatic side effect
of starting OpenShip. Do not rotate secrets, modify protected configuration or
change the running dev stack while preparing them.

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
   engine/platform, including resulting immutable image identities.
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
- [x] Design self-reviewed for scope, identity compatibility and evidence claims.
- [ ] User reviews this written design.
- [ ] Implementation plan reviewed and execution method selected.
- [ ] Images implemented, built and verified; dev-stack handoff delivered.
