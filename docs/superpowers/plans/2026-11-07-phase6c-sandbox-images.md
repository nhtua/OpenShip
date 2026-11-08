---
type: plan
summary: Build four locally maintained sandbox images with locked dependencies, offline smoke checks and opt-in stack build integration.
date: 2026-11-07
status: in-progress
---

# Phase 6C Sandbox Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow `git-workflow` for isolated work and verified, file-message commits. Do not implement until the user reviews this plan and selects an execution method.

**Goal:** Supply four pinned, locally buildable sandbox profiles and explicit image-management commands without changing workload access or execution APIs.

**Architecture:** Keep Phase 6B's Python execution path and legacy identities. Add a shared Python workload base, per-profile Dockerfiles and dependency locks, a small host-side image helper, and a thin integration in `scripts/openship-stack`. Build and inspect images on the sandbox manager's actual engine/socket; keep local build records separate from committed dependency sources.

**Tech Stack:** Python standard-library operator tooling, Bash, Docker/Podman, pinned Python 3.12+ images, uv-generated hash locks, pinned CLI archives, Debian package snapshots, pytest, existing OpenSandbox and full-stack test lanes.

**Spec:** [Approved Phase 6C design](../specs/2026-11-07-phase6c-sandbox-images-design.md), approved by the user on 2026-11-07 after the local-build refinement.

## Global Constraints

- Four managed profile IDs: `logs-data`, `iac-validation`, `standard`, `devops-shell`.
- "Every new image retains the `python` entrypoint expected by 6B" and includes Python 3.12+.
- "Preserve both legacy Dockerfiles and identities": `python311` / `stdlib` and `python311-plot` / `matplotlib-3.9.2-numpy-2.1.0`.
- "Directory mounting, network access and connected infrastructure execution are **deferred, not rejected**."
- "Preserve all existing network, filesystem, credential, resource and lifecycle settings." UID/GID 1000 remains the workload user. The existing client still requests 1 CPU, 512 MiB, 30-second commands and a 1,800-second sandbox lifetime.
- "Builds and startup consume committed pins; neither upgrades dependencies implicitly."
- "A recipe fingerprint is not an image digest"; record the actual local image ID separately. Do not invent a registry digest for a local build.
- "Ordinary startup does not build or update images"; `start --build-sandbox` is explicit opt-in, dev-only and requires `--sandbox`.
- "Do not rewrite `.env`, rotate secrets or modify protected provider configuration when building." Leave the running dev stack untouched during preparation.
- No registry account, new database schema, runtime profile router, shell execution API, credential handoff, network changes or directory-mount feature.
- Initial real-build target: `linux/amd64`. Reject other platforms before downloading/building until their pins and smoke evidence are added explicitly. Unit-test both engine adapters; label real evidence by the engine actually exercised.
- Update verified progress under each task during execution. Keep real builds, static/unit checks, live-provider checks and user acceptance distinct.

## Review Focus

1. Rootless provider socket differs from the application's default engine: build/inspect/run must use the manager's image store, or fail before startup. Tasks 1, 6 and 7 test this.
2. A tag exists but its image ID changed, or another build interrupted local state: reject stale/corrupt evidence; serialize same-profile builds and atomically save records. Tasks 1 and 7 test this.
3. Quoted `.env` settings, process overrides, or conflicting image/profile identities: resolve supported literals predictably, reject ambiguous interpolation, never print unrelated secrets. Task 6 tests this.
4. A required input contains a traversal/symlink or a download has the wrong hash: fail before installation; secrets/config files must not enter the build context. Tasks 1, 2 and 5 test this.
5. Tools expect writable homes, network or more resources than 6B supplies: offline UID-1000 smoke checks must prove useful operations or report the incompatibility, not silently relax policy. Tasks 2–5 and 7 test this.

---

## File Structure and Ownership

| File(s) | Responsibility |
| --- | --- |
| `deploy/sandbox/images.json` | Additive managed-profile inventory; retain legacy/provider fields and historical evidence. |
| `deploy/sandbox/pins.json` | New base/uv image digests, supported platform and Debian snapshot identity. |
| `deploy/sandbox/tools.lock.json` | CLI release URLs, exact versions, platform mapping and SHA-256 checksums; no credentials. |
| `deploy/sandbox/Dockerfile.base` | Shared Python runtime, workload identity, directories and pinned build prerequisites. |
| `deploy/sandbox/build-requirements.in`, `build-requirements.lock` | Explicit hash-locked Python build dependencies for packages distributed as source. |
| `deploy/sandbox/profiles/<id>/Dockerfile` | One profile's installation and runtime defaults. |
| `deploy/sandbox/profiles/<id>/requirements.in`, `requirements.lock`, `os-packages.lock` | Direct Python requirements, generated complete Python closure, exact OS package requests. |
| `deploy/sandbox/profiles/<id>/smoke.py` | Trusted, installed offline functional checks; creates fixtures in temporary workload-owned space. |
| `deploy/sandbox/profiles/devops-shell/azure-cli.in`, `azure-cli.lock` | Azure CLI's separate Python environment/closure. |
| `scripts/sandbox_images.py` | Argument parsing and list/build/smoke/internal-preflight entrypoint; no API imports. |
| `scripts/sandbox_image_lib/{__init__,catalog,engine,builds,config}.py` | Focused modules for input identity, engine calls, build records and selected stack settings. |
| `scripts/lock_sandbox_dependencies.py` | Explicit maintainer lock regeneration using pinned tooling; never invoked by build/start. |
| `.openship/sandbox/images/<engine-key>/<platform>/<profile>.json` | Gitignored local image and smoke evidence; existing `/.openship/` ignore rule already covers this. |
| `apps/api/tests/test_sandbox_image_{catalog,engine,commands,stack}.py` | Host-tooling and wrapper regressions; fake external engine only at the subprocess boundary. |
| `apps/api/tests/test_opensandbox_packaging.py` | Carry forward existing deployment/legacy-identity checks; extend only where needed. |
| `apps/api/tests/live/test_sandbox_profiles.py` | Explicit real-provider checks for preloaded managed images; not part of the ordinary unit lane. |
| `apps/api/tests/full_stack/test_sandbox_python_chat.py` | Existing real chart journey, parameterized to identify the selected managed runtime honestly. |
| `docs/operations/opensandbox.md`, `docs/development.md`, `.env.example` | Operator inventory, maintenance/build instructions and stack recreation handoff. |
| `docs/validations.md`, this plan, `docs/roadmap.md` | Actual evidence, remaining work and user acceptance state. |

Host tooling remains small and standard-library-only. Tests can add `<repo-root>/scripts` to `sys.path` through a test fixture; production application code must not import these operator modules.

Inventory additions use `managed_base` with `dockerfile`, `pins` and `build_requirements_lock` paths, and `managed_profiles` keyed by the exact profile ID. A profile entry has `dockerfile`, `requirements_lock`, `os_packages_lock`, `smoke`, `extra_inputs` (additional lock/installation-file paths) and `tools` (archive keys). `pins.json` holds `python_base_image`, `uv_image`, `python_version`, `supported_platforms` and `debian_snapshot`; the base's exact OS requests live with that snapshot record. `tools.lock.json` maps tool/platform to `version`, `url`, `sha256` and verification provenance. The build helper converts selected archive entries to uppercase `<TOOL>_URL`/`<TOOL>_SHA256` build arguments; Dockerfiles do not duplicate their values. Azure's Python-distributed version is authoritative in its Python requirement/lock rather than repeated as an archive tool pin.

## Shared Interfaces

Implement these exact seams; avoid adding a framework around them:

- `catalog.load_profile(root: Path, profile_id: str) -> ProfileSpec`, where the frozen dataclass contains `id: str`, `dockerfile: Path`, `requirements_lock: Path`, `os_packages_lock: Path`, `smoke: Path`, `extra_inputs: tuple[Path, ...]`.
- `catalog.resolve_recipe(root: Path, profile_id: str, platform: str) -> Recipe`, with frozen fields `profile: ProfileSpec`, `platform: str`, `fingerprint: str`, `base_fingerprint: str`, `image_tag: str`, `base_tag: str`, `dependency_identity: str`, `inputs: tuple[Path, ...]`.
- Tags: `openship-sandbox-<id>:<full-sha256-fingerprint>` and `openship-sandbox-base:<base-fingerprint>`; dependency identity: `sha256:<full-recipe-fingerprint>` (71 characters, within the existing 128-character bound).
- `engine.Engine(cli: str, endpoint: str, platform: str)`; `identity: str` identifies the normalized CLI/socket/platform namespace; `inspect_image(reference: str) -> dict | None`; `build(dockerfile: Path, context: Path, tag: str, build_args: dict[str, str], labels: dict[str, str]) -> str` returns an inspected image ID; `smoke(image_id: str) -> subprocess.CompletedProcess[str]`. Failures/timeouts raise `ImageError` after exact-owned-test-container cleanup.
- `builds.ensure_image(root: Path, recipe: Recipe, engine: Engine) -> dict`; `builds.inspect_status(root: Path, recipe: Recipe, engine: Engine) -> dict`; `builds.run_smoke(root: Path, recipe: Recipe, engine: Engine) -> dict`; `builds.runtime_values(recipe: Recipe) -> dict[str, str]` returns exactly the three existing `SANDBOX_IMAGE`, `SANDBOX_PROFILE_ID`, `SANDBOX_DEPENDENCY_IDENTITY` assignments.
- `config.read_settings(root: Path, environ: Mapping[str, str]) -> dict[str, str]`; `config.resolve_endpoint(cli: str, settings: Mapping[str, str]) -> str`; `config.validate_selection(recipe: Recipe, settings: Mapping[str, str]) -> None`.
- `scripts/sandbox_images.py:main(argv: Sequence[str] | None = None) -> int`. Arguments: `--root PATH --engine docker|podman`, then `list`, `build PROFILE|--all`, `smoke PROFILE`, or internal `preflight [--build]`. Invalid input exits 2; build/smoke/provider failure exits 1; successful operations exit 0. Progress goes to stderr; stdout contains only safe profile/build/status/runtime-setting output.

Use a small `ImageError` exception in `catalog.py` for safe operator-facing failures. Error messages name a setting/profile/action without echoing arbitrary engine output or secret-bearing configuration.

## Task 1: Deterministic Catalog, Engine Adapter and Local Build Commands

Progress: committed `9a7b3e7`; 17 targeted tests passed. Final hardening/review remains part of the branch-wide gate.

**Files:** Create `scripts/sandbox_images.py`, `scripts/sandbox_image_lib/{__init__,catalog,engine,builds}.py`; create the corresponding catalog/engine/commands tests above. Existing release inventory stays readable; managed entries are added as profiles land.

**Interfaces:** Consumes existing inventory and explicit CLI/socket selection. Produces the `ProfileSpec`, `Recipe`, `ImageError`, `Engine` and `builds.*` seams above. Tests use complete temporary catalogs and trusted fixture recipes; they do not need cloud credentials or live containers.

- [ ] **1. Write failing behavior tests** for deterministic recipes and engine command dispatch. Required assertions include:

  ```python
  assert recipe.image_tag == f"openship-sandbox-logs-data:{recipe.fingerprint}"
  assert values["SANDBOX_PROFILE_ID"] == "logs-data"
  assert values["SANDBOX_DEPENDENCY_IDENTITY"] == f"sha256:{recipe.fingerprint}"
  assert status["status"] == "stale"  # tag now points to a different image ID
  ```

  Test names: `test_recipe_changes_for_base_lock_helper_smoke_and_platform_inputs`, `test_recipe_ignores_unrelated_repo_files`, `test_unknown_profile_and_symlink_escape_fail_before_engine_calls`, `test_each_engine_operation_targets_the_explicit_socket`, `test_local_image_without_repo_digest_is_valid`, `test_failed_build_never_commits_a_record`, `test_concurrent_same_profile_builds_leave_one_valid_record`, `test_smoke_failure_does_not_become_passed_evidence`.
- [ ] **2. Run red:** from `apps/api`, `uv run pytest -q tests/test_sandbox_image_catalog.py tests/test_sandbox_image_engine.py tests/test_sandbox_image_commands.py`. Expect missing implementation failures, then the specific incorrect-behavior assertions as seams are introduced.
- [ ] **3. Implement catalog and recipe resolution.** Hash a canonical ordered sequence of relative input paths and bytes plus platform; include all consumed manifests, base/profile recipes, image-installation helpers, locks and smoke files. Project shared tool manifests to the entries actually consumed by the profile before hashing/staging; hashing the whole all-tools manifest would invalidate unrelated profiles. Reject missing/nonregular inputs and symlinks escaping the declared build inputs. A profile-specific change must not invalidate unrelated profiles; a common-base change must. Legacy catalog keys remain untouched. Until Task 6 adds `.env` handling, the CLI resolves the endpoint from the explicit process socket setting or documented engine default; it never consults a different implicit connection.
- [ ] **4. Implement engine/build commands.** Docker prefix is `docker --host <unix-endpoint>`; Podman prefix is `podman --remote --url <unix-endpoint>`. Apply it to info/inspect/build/run, not just one call. Detect platform through the selected engine, enforce `linux/amd64`, inspect after build to ensure output reached that store, and normalize Podman's optional `localhost/` image-name prefix without confusing it with a registry digest.

  Stage build contexts from the manifest-listed inputs only, preserving their relative paths; never send the repository root, `.env`, `secrets/`, rendered provider config or local records. Build base first, verify its local identity, and pass its tag via `WORKLOAD_BASE_IMAGE`. Install smoke scripts as `/opt/openship/smoke.py`. Run smoke by inspected image ID with `--rm --network none --user 1000:1000 --cpus 1 --memory 512m --pids-limit 128 --cap-drop ALL --security-opt no-new-privileges`, no host volume/env inheritance, and a unique `openship-image-smoke-<uuid>` name. Execute `timeout --signal=TERM --kill-after=2 30s python /opt/openship/smoke.py` with a 35-second host deadline; ensure coreutils' `timeout` is present in the base. On timeout/failure remove only that exact owned test container and fail the check. This trusted test-container cleanup does not change OpenSandbox lifecycle ownership. Test timeout cleanup and prove it cannot target another container.

  Save records under the stated engine/platform path using a per-profile file lock and atomic replacement; serialize shared-base builds separately so concurrent different profiles cannot race the base tag. Fields: `schema_version`, `profile_id`, `platform`, `engine_identity`, `fingerprint`, `image_tag`, `image_id`, `dependency_identity`, `built_at`, `smoke`. Smoke evidence binds `image_id`/fingerprint plus `passed`/`failed` and timestamp. Reinspect before reuse; corruption, unsafe state-path symlinks, changed image IDs and missing images cannot count as matching. Reuse matching images; do not delete older builds or auto-build during list/smoke. Do not persist raw build/run output.
- [ ] **5. Run green:** repeat the Task 1 tests; `list/build/smoke` behavior is tested against the fixture engine, including unknown profile, failed downloads/builds and absent images. No real-build claim yet.
- [ ] **6. Commit** only Task 1 code/tests after review. Record red/green output and any remaining real-engine limitation here.

## Task 2: Pinned Shared Base and Standard Profile

Progress: initial commit `d464114`; corrected during Task 3. Actual uv resolves complete closures, hash verification is mandatory, and the uv release archive SHA is not presented as an OCI digest. Existing Docker daemon build and offline runsc smoke passed for standard (image ID `sha256:9282fddc6fda633e77adb076e13b27a75b5ac728e2add4f15c7d6251220f7f4a`, Python 3.12.16). Historical Podman evidence is not the active qualification.

**Files:** Create `deploy/sandbox/{pins.json,tools.lock.json,Dockerfile.base,build-requirements.in,build-requirements.lock}`; create `profiles/standard/{Dockerfile,requirements.in,requirements.lock,os-packages.lock,smoke.py}`; create `scripts/lock_sandbox_dependencies.py`; extend inventory and catalog/command tests.

**Interfaces:** Consumes Task 1 catalog/build CLI. Produces the shared base, lock-generation command and buildable `standard` profile. The new `managed_base` inventory entry points to `pins.json`; `managed_profiles.standard` lists its recipe/lock/smoke files. Register later profiles only when their files exist.

- [ ] **1. Add failing tests:** `test_standard_smoke_requires_python_312_node_and_sympy`, `test_lock_update_is_explicit_and_hash_mismatch_stops_installation`, `test_build_context_omits_secret_and_rendered_config_sentinels`, `test_base_update_invalidates_each_registered_profile`, `test_legacy_inventory_values_remain_unchanged`. Exercise actual helper behavior and fixture lock validation; do not make tests that merely grep Dockerfile lines.
- [ ] **2. Run red:** `uv run pytest -q tests/test_sandbox_image_catalog.py tests/test_sandbox_image_commands.py tests/test_opensandbox_packaging.py` from `apps/api`.
- [ ] **3. Resolve real pins and generate locks.** Inspect upstream release metadata and compatibility, then commit real Python/uv image digests, a Debian snapshot timestamp, exact requested OS packages, Node.js release archive/checksum, and Python direct requirements for SymPy/regex. Do not insert guessed versions or dummy hashes. The lock helper interface is `python3 scripts/lock_sandbox_dependencies.py PROFILE [--upgrade-package NAME]`; it runs the digest-pinned uv tool on the supported build platform, generates complete hash-locked closures and changes only the selected lock(s). It is never called by image build or stack start. Record the exact resolver command/version in generated lock comments.

  Runtime locks install with hash verification. Pin build prerequisites in `build-requirements.lock`; if an older package needs a source build, install its declared build prerequisites explicitly and disable automatic build-isolation downloads. Fail for an unaccounted-for build dependency rather than resolving it dynamically. Record upstream URLs/checksum/signature provenance in the tool manifest.
- [ ] **4. Implement base and standard image.** Reproduce existing UID/GID 1000 and `/code`, `/inputs`, `/outputs` ownership/layout without changing legacy files. Preserve container-start privileges needed by execd to select the workload UID; do not introduce a blanket `USER 1000` that breaks provider initialization. Keep a usable `python` command. Install Node.js separately from Python. `standard/smoke.py` verifies Python >=3.12, `math`, `decimal`, `uuid`, `csv`, `json`, `re`, third-party regex and SymPy, and a real `node -e` computation returning 42. Use workload-owned temporary home/cache space; no package installation at smoke time.
- [ ] **5. Run green and a real local build**, using an operator-approved engine/socket on the designated development host:

  ```sh
  python3 scripts/sandbox_images.py --engine docker build standard
  python3 scripts/sandbox_images.py --engine docker smoke standard
  ```

  Substitute `podman` only when that is the configured manager engine. The helper must resolve that manager socket, not default blindly; Task 6 completes `.env`/stack integration. Until then supply the socket explicitly via the existing `OPENSHIP_SANDBOX_ENGINE_SOCKET` process setting. Expected: inspected local image ID and passed offline operations at UID 1000/512 MiB. Record the actual platform/engine and package inventory. Missing authorization/prerequisites blocks this checkbox, not the unit checks.
- [ ] **6. Commit** the base, standard profile, real pins and passing lock/packaging tests; keep local build records untracked.

## Task 3: Logs and Data Analysis Profile

Progress: Docker build and offline runsc smoke passed (image ID `sha256:b00de5136b81416603dec0d93913d4d9abd39a1578d76b39e674347e1c0a8a65`): CSV groups api=5/worker=7, two Drain3 clusters, decoded Agg PNG. 38 targeted tests passed. pygrok 1.0.0 emits an upstream invalid-escape SyntaxWarning under Python 3.12; functional checks pass. No stack restart or chat demonstration performed.

**Files:** Create `deploy/sandbox/profiles/logs-data/{Dockerfile,requirements.in,requirements.lock,os-packages.lock,smoke.py}`; extend `images.json` and catalog/command tests.

**Interfaces:** Consumes Tasks 1–2 base/build/lock interfaces. Produces `managed_profiles.logs-data`; packaged Pandas, NumPy, Matplotlib, regex, pygrok and Drain3 with their complete closure.

- [ ] **1. Add failing fixture assertions** in the smoke program and its test invocation: a three-row CSV groups to `{"api": 5, "worker": 7}`, JSON round-trips, grok extracts an IP/status field, regex extracts metadata, and two repeated log shapes become two Drain3 clusters rather than one per message. A nonempty Agg PNG must have PNG magic and decode successfully; do not assert unstable font-rendered pixel hashes.
- [ ] **2. Run red:** `uv run pytest -q tests/test_sandbox_image_catalog.py tests/test_sandbox_image_commands.py` from `apps/api`; an explicitly attempted pre-implementation profile build/smoke must fail for absent packaging, not be counted as passed or skipped.
- [ ] **3. Generate compatible locks** with the Task 2 helper, handling pygrok/Drain3 source-build prerequisites through the pinned builder closure. Implement the profile with headless `MPLBACKEND=Agg` and writable workload-owned Matplotlib cache. Inputs/outputs still use the existing execution paths.
- [ ] **4. Build and smoke:** `python3 scripts/sandbox_images.py --engine docker build logs-data`, then `python3 scripts/sandbox_images.py --engine docker smoke logs-data` (use the selected manager engine). Expect the stated CSV/grok/clustering assertions and a decoded chart to pass offline with the existing resource limit.
- [ ] **5. Run regression tests** for the legacy plotting profile and Task 1 catalog/build behavior. Verify changing only this profile's lock changes its recipe identity but not `standard`.
- [ ] **6. Commit** the verified profile and record exact build/smoke evidence; do not claim the chat journey has run yet.

## Task 4: IaC and Schema Validation Profile

Progress: Docker build and separate runsc smoke passed for image `sha256:ba0afd5350a87f7d0764c5d3a28e07d4dd5465964f916156c29346ae0489a9e9`, recipe `ae1496c82d849fb224a057c7f1ce4d262d658b5940d4a68d36e2fd2478bb9840`. Checkov 3.4.3 and bundled TFLint 0.65.0 distinguish the invalid/corrected Terraform fixtures; JSON Schema accepts/rejects, YAML loads, Graphviz PNG decodes. Debian graphviz=2.42.4-3 is from the signed 20261101T000000Z snapshot. Limits unchanged. 39 targeted tests passed; branch-wide unit gate remains in progress.

**Files:** Create `deploy/sandbox/profiles/iac-validation/{Dockerfile,requirements.in,requirements.lock,os-packages.lock,smoke.py}`; extend inventory/tool manifest and catalog/command tests.

**Interfaces:** Consumes Tasks 1–2. Produces jsonschema, PyYAML, Checkov, TFLint, Graphviz executable and Python bindings in `managed_profiles.iac-validation`.

- [ ] **1. Add failing smoke fixtures:** JSON Schema accepts an integer and rejects a string; YAML safely loads the expected mapping; a local Terraform fixture has a known Checkov violation that disappears in a corrected fixture; a bundled Terraform-language TFLint rule distinguishes bad/good local files; Graphviz renders a nonempty valid PNG. Assert expected tool exit statuses and parsed results, not only version output.
- [ ] **2. Run red:** the Task 1 catalog/command tests plus an attempted unimplemented IaC smoke. Capture failures specifically caused by the missing profile/tools.
- [ ] **3. Pin and implement.** Lock Python closures, pin Graphviz OS packages through the snapshot, and verify the TFLint archive checksum. Configure Checkov without API keys, remote policy/guideline/module downloads or integrations. Use documented flags for the pinned release, verified against its help/reference; do not invent flags. TFLint smoke uses bundled rules only and must not run plugin installation. Store only local synthetic fixtures in the smoke program.
- [ ] **4. Build and smoke:** `python3 scripts/sandbox_images.py --engine docker build iac-validation`, then the matching `smoke iac-validation` command on the manager engine. Expect all valid/invalid cases and Graphviz rendering to pass with networking disabled and UID 1000.
- [ ] **5. Verify limits and errors:** report any tool exceeding 512 MiB or depending on unavailable plugins instead of changing limits or allowing egress. Rerun Task 1 tests and legacy packaging checks.
- [ ] **6. Commit** the profile and its pins/evidence. Document that arbitrary TFLint plugins and Terraform providers/modules remain absent.

## Task 5: DevOps Shell Toolchain Image

**Files:** Create `deploy/sandbox/profiles/devops-shell/{Dockerfile,requirements.in,requirements.lock,os-packages.lock,azure-cli.in,azure-cli.lock,smoke.py}`; extend tool manifest/inventory and catalog/command tests.

**Interfaces:** Consumes Tasks 1–2. Produces `managed_profiles.devops-shell` with Python, Bash, Git, AWS CLI, Azure CLI, Google Cloud CLI, Terraform and Terragrunt. No shell execution capability or credential path is added.

- [ ] **1. Add failing smoke tests** for `bash`, `git`, `aws`, `az`, `gcloud`, `terraform`, `terragrunt`: each must execute and report the pinned version without prompting or contacting a cloud. A local Bash expression returns 42; a temporary Git repository accepts local add/diff; Terraform formats a deliberately unformatted local file. Do not call Terraform init/plan/apply or Terragrunt hooks/provider downloads.
- [ ] **2. Run red:** catalog/command tests and the explicitly selected absent-tool smoke fail as expected.
- [ ] **3. Pin and install toolchains.** Use verified platform-specific release archives for AWS CLI, gcloud, Terraform and Terragrunt, exact snapshot packages for Bash/Git, and an isolated `/opt/azure-cli` Python environment installed from its hash lock. A wrapper for `az` must not replace the workload Python or expose its dependency conflicts. Retain gcloud's isolated supported interpreter as needed; disable interactive/telemetry/update behavior and record bundled components. Test a deliberately corrupted fixture download and require failure before extraction/install.
- [ ] **4. Build and smoke:** `python3 scripts/sandbox_images.py --engine docker build devops-shell`, then `smoke devops-shell`, on the manager engine. Use an empty workload-owned home/config area with no inherited host credentials. Prove CLI startup works at UID 1000 without relaxing the 512 MiB smoke limit.
- [ ] **5. Run complete profile/catalog checks:** the final managed inventory is exactly `{"logs-data", "iac-validation", "standard", "devops-shell"}`; each has real pins, a full input closure and useful smoke operations. Legacy identities/provider pins remain unchanged.
- [ ] **6. Commit** the verified toolchain image, recording limitations. Binary availability does not claim authenticated cloud operations or first-class shell execution.

## Task 6: Stack Commands, Configuration and Startup Preflight

**Files:** Create `scripts/sandbox_image_lib/config.py`, `apps/api/tests/test_sandbox_image_stack.py`; modify `scripts/sandbox_images.py`, `scripts/openship-stack:43–73,535–555,716–720,780–799` and relevant packaging tests. Do not alter database/startup-cutover semantics.

**Interfaces:** Consumes all Task 1 interfaces and the registered images. Produces `read_settings`, `resolve_endpoint`, `validate_selection` and internal `preflight [--build]`; the public interface is exactly the five commands in the approved spec.

- [ ] **1. Write failing real-wrapper tests** using temporary repo/config/secret sentinel files and an external fake engine executable, extending the existing subprocess-testing pattern. Test names/assertions:

  ```python
  assert result.returncode != 0       # missing/stale managed image
  assert not compose_mutations       # before init/migrate/up/cutover writes
  assert built_profiles == ["logs-data"]  # explicit opt-in, not all four
  assert protected_files_after == protected_files_before
  ```

  Cover `list`, one build, `build --all`, smoke, default start with a matching image, missing/stale image without opt-in, failed opt-in build, contradictory image/dependency settings, absent sandbox flag, local/prod misuse, repeated/unknown start flags, and `--cutover UUID` preservation. A documentation/test fixture may use synthetic credentials, but tests must assert they never appear in helper stdout/stderr/records.
- [ ] **2. Run red:** from `apps/api`, `uv run pytest -q tests/test_sandbox_image_stack.py tests/test_opensandbox_packaging.py`. Missing dispatcher/preflight must fail before implementation.
- [ ] **3. Implement selected-settings resolution.** Read only `SANDBOX_ENABLED`, `SANDBOX_IMAGE`, `SANDBOX_PROFILE_ID`, `SANDBOX_DEPENDENCY_IDENTITY`, `OPENSHIP_SANDBOX_ENGINE_SOCKET` and required rootless defaults; process assignments override `.env`. Support literal unquoted/single-/double-quoted values, CRLF, `export`, and trailing comments. Never source/eval `.env`. Parse the enabled/profile selector first and bypass new image/dependency validation for the existing legacy or disabled normal-start paths; do not tighten their previous configuration rules. For a managed selection, reject unsupported interpolation/multiline values in the selected fields with an actionable instruction to supply literal values or process overrides, rather than silently differing from Compose. Do not parse/log model/connector secrets. Existing `load_container_engine` remains authoritative for the chosen CLI.

  Docker socket default is `/var/run/docker.sock`; Podman default is `$XDG_RUNTIME_DIR/openship-sandbox/podman.sock`, matching the existing overlay. Explicit `OPENSHIP_SANDBOX_ENGINE_SOCKET` wins. Produce a normalized Unix endpoint, reject unsupported remote/missing/inaccessible sockets, and ignore conflicting implicit Docker/Podman default connections by passing the endpoint explicitly. Test quoted paths, process overrides, rootless sockets differing from default stores, and mismatched engine metadata. Unlike Docker Compose, Podman Compose 1.6 has no `config --format json`; do not assume that option is portable or fall back to evaluating dotenv as shell code.
- [ ] **4. Wire the wrapper.** Dispatch dev-only `sandbox-images` before any messaging/artifact/provider secret initialization. Parse `--build-sandbox` inside `start_dev_stack` alongside `--cutover`. Run managed-profile selection/preflight after read-only option/settings validation but **before** `initialize_dev_messaging_if_missing`, `initialize_dev_artifacts`, Compose mutations or gate/cutover actions. A matching managed image requires no build. Explicit opt-in calls `ensure_image` only for the selected profile; require exact current runtime settings and never rewrite `.env`. Missing/contradictory settings print the safe assignments/corrective command and stop. Normal startup for legacy profiles or disabled execution retains its previous path. Explicit build-on-start can prepare a selected managed image even if execution remains disabled, but must never enable execution; a build flag with no managed selection is an error, not permission to guess a profile. Test both disabled cases.
- [ ] **5. Run green** for Task 6/Task 1 suites and existing stack/messaging packaging regressions. Verify `bash -n scripts/openship-stack`; ensure the extra start flag does not leak into downstream cutover handling. Update usage text so opt-in build behavior and missing-image remedies are explicit.
- [ ] **6. Commit** wrapper/helper changes after review. No real `start`, `stop`, secret initialization, volume reuse or cutover is authorized by these tests.

## Task 7: Real Build Evidence, Provider Compatibility and Operator Handoff

**Files:** Create `apps/api/tests/live/test_sandbox_profiles.py`; modify `apps/api/tests/full_stack/test_sandbox_python_chat.py`, `docs/operations/opensandbox.md`, `docs/development.md`, `.env.example`, `docs/validations.md`, this plan and `docs/roadmap.md`. Update `tests/full_stack/synthetic_sandbox_chat_worker.py` only if its runtime selectors are hardcoded.

**Interfaces:** Consumes the public stack commands and image records. Produces actual build/smoke evidence, a focused live local-image test, backward-compatible full-stack runtime selectors and the user's concrete restart/recreation instructions.

- [ ] **1. Add failing focused live/runtime-selector tests.** Reuse `OpenSandboxClient` and its existing source/input/output contracts; add no production API. The new live test uses `OPENSHIP_LIVE_SANDBOX_PROFILES=1` to opt in, then requires existing protected endpoint/key-file settings plus `OPENSHIP_LIVE_OPENSANDBOX_IMAGE`, `OPENSHIP_LIVE_OPENSANDBOX_PROFILE_ID`, `OPENSHIP_LIVE_OPENSANDBOX_DEPENDENCY_IDENTITY`. Without opt-in the lane is unselected; with opt-in missing prerequisites are failures, not skips. The submitted source runs the installed `/opt/openship/smoke.py` and writes declared JSON `{"profile": "<selected-id>", "ok": true}` only after it succeeds; assert the exact result. No registry login/push or extra image pull is allowed.

  Preserve the chart journey's legacy defaults while adding `OPENSHIP_SANDBOX_TEST_PROFILE_ID` and `OPENSHIP_SANDBOX_TEST_DEPENDENCY_IDENTITY` alongside its existing `OPENSHIP_SANDBOX_TEST_IMAGE`. Test selector propagation so `logs-data` is never recorded as `python311-plot`.
- [ ] **2. Run ordinary regressions** from `apps/api`:

  ```sh
  uv run ruff check .
  uv run ruff check ../../scripts/sandbox_images.py ../../scripts/sandbox_image_lib ../../scripts/lock_sandbox_dependencies.py
  uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'
  ```

  From `apps/web`: `pnpm typecheck`, `pnpm test:run`, `pnpm build`. Expected: all selected checks pass; list any unrelated failures by name. Unit/stub tests are not build or live-provider evidence.
- [ ] **3. Exercise public local commands** with authorized engine access:

  ```sh
  scripts/openship-stack -e dev sandbox-images list
  scripts/openship-stack -e dev sandbox-images build --all
  scripts/openship-stack -e dev sandbox-images smoke standard
  scripts/openship-stack -e dev sandbox-images smoke logs-data
  scripts/openship-stack -e dev sandbox-images smoke iac-validation
  scripts/openship-stack -e dev sandbox-images smoke devops-shell
  scripts/openship-stack -e dev sandbox-images build logs-data
  ```

  Expect four real builds/IDs, offline smoke success and no build on the final matching-image request. Test changed recipe inputs and retagged-image rejection against disposable fixtures, not the user's configured image. Run a source-byte/hash tampering fixture and verify failure. Confirm raw build contexts/records contain no protected sentinels; do not attach secret-bearing logs to reports. Record actual package versions, engine/socket category, platform, image IDs, input fingerprints, smoke outcomes and measured resource limitations in the validation record.
- [ ] **4. Run the focused real-provider lane only with separately authorized provider settings**, from `apps/api`:

  ```sh
  OPENSHIP_LIVE_SANDBOX_PROFILES=1 uv run pytest -q -m provider_live tests/live/test_sandbox_profiles.py
  ```

  Run once per managed profile, supplying its emitted image/profile/dependency settings and protected endpoint/key-file references through the environment/files, never raw credentials in commands or reports. Confirm OpenSandbox uses each preloaded local image on the intended engine with no registry account/publishing. Do not attach to or restart the user's running stack merely to satisfy this checkbox. A missing authorized provider is a named blocker; local smoke can still be recorded independently.
- [ ] **5. Write operator instructions and handoff.** Replace the new-profile path in `docs/development.md#sandbox-images-for-dev` with local list/build/smoke and printed runtime assignments; retain legacy build instructions as an explicitly labeled alternative. Extend the operations runbook with all four package inventories, exact lock-regeneration/update procedure, snapshot/CLI pin sources, engine/socket diagnostics, local-record meaning and rollback. Update `.env.example` comments without changing defaults or secrets. Explain that `init-sandbox` prepares protected provider files, not workload images.

  Provide commands to build/smoke, copy the three emitted assignments into operator configuration, and run `scripts/openship-stack -e dev --sandbox start --build-sandbox`. Explain that application services need recreation when their environment changes; a code reload is insufficient. Do not add a new restart command merely for this phase or claim that Compose `restart` reloads environment variables. Preserve the existing cutover confirmation flow.
- [ ] **6. Stop for the user-owned dev-stack action.** Tell the user preparation is ready only when the four builds/smokes and required checks have actually passed, with any provider blocker plainly stated. After they recreate the stack and authorize the demonstration, run the existing chart journey using the managed `logs-data` selectors and a disposable database/artifact root:

  ```sh
  uv run pytest -q tests/full_stack/test_sandbox_python_chat.py
  ```

  This lane includes its existing browser qualification and synthetic model/download fixtures; it is not live-model evidence. Show the retained PNG and correct selected runtime, and request **accept / revise / reject**. Until then keep the live chart/user-acceptance checkbox pending.
- [ ] **7. Commit** the verified tooling/docs/test work and current evidence before handoff; later commit actual demonstration/acceptance results separately. Do not mark Phase 6C delivered merely because the implementation or image builds passed.

## Verification and Acceptance Ledger

- [ ] Catalog/fingerprint/state unit tests pass, including tampering/concurrency cases.
- [ ] Both engine adapters and stack preflight/option-selection tests pass.
- [ ] All four images have real pinned input closures and recorded local image IDs.
- [ ] All four offline functional smoke checks pass as UID/GID 1000 within unchanged limits.
- [ ] Legacy identities, disabled-sandbox behavior and existing messaging/cutover tests remain intact.
- [ ] Authorized OpenSandbox test proves preloaded local managed images work without a registry account.
- [ ] API lint/unit suite and web typecheck/tests/build pass; secret/context checks are clean.
- [ ] Concrete build/configuration/recreation handoff delivered; preparation changed no running stack.
- [ ] User recreated/reloaded the required services; real chart journey verified under `logs-data`.
- [ ] User explicitly accepted Phase 6C, or requested revision/stopped the increment.

## Execution Handoff

Recommended method: **Native**. The catalog, engine records and stack preflight share a small set of tightly coupled interfaces; implementing them in one session avoids repeated setup/context transfer. A fresh whole-branch reviewer should check the result before integration. Subagent-driven execution remains an option if the user prefers independent task-level reviews; profile tasks 3–5 may then be delegated after the shared base and tooling interfaces are stable, with shared manifest edits coordinated serially.

This plan has been self-reviewed for spec coverage, interface/type consistency, review-focus test ownership and scope. Implementation has not started. The next gate is the user's plan review and execution-method selection.
