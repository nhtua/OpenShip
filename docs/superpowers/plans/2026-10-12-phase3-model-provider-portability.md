# Phase 3 Model-Provider Portability Implementation Plan

> Current implementation reference (updated 2026-10-14): chat uses explicitly
> declared profiles and the durable run/event API. Phase 1 POST chat streams,
> direct OpenAI chat services, and implicit single-provider profiles are retired.
> Do not restore those paths when implementing later phases.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, or superpowers:subagent-driven-development if the user selects delegation, to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A signed-in user can continue the same durable conversation using an explicitly selected, supported model provider, with consistent cancellation, errors, usage, and execution authority.

**Architecture:** Introduce a small provider boundary in the existing FastAPI worker runtime, using maintained provider SDKs behind normalized contracts. Snapshot non-secret configuration when a run is submitted; execute that snapshot through a common bounded runner and retain the Phase 2 queue, fencing, checkpoints, messages, and SSE contracts. Ship the shared foundation with OpenAI first, then accept Anthropic, Google, and each configured OpenAI-compatible endpoint separately.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy/Alembic, PostgreSQL 15, LangGraph and its PostgreSQL saver; Vue 3, TypeScript, Pinia, Vitest, Playwright; existing `openai`, followed by `anthropic` and `google-genai`, pinned through `apps/api/uv.lock`.

**Spec:** [Roadmap Phase 3](../../roadmap.md#phase-3--model-provider-portability), [architecture §7](../../architects.md#7-conversation-harness-and-model-compatibility), and architecture §§4, 6, 10, 11, 13, 18. These documents provide the intended scope; implementation choices below are proposals for review.

**Type:** plan  
**Summary:** Staged provider portability with run pinning, conformance tests, bounded retries, explicit fallback, and independent adapter acceptance.  
**Date:** 2026-10-12  
**Status:** Implemented in the Phase 3 branch; compatibility decisions updated 2026-10-14.

## Global Constraints

- Roadmap milestone: “I can choose my model provider without changing how I use OpenShip.” Each additional adapter is independently demonstrated and accepted.
- Preserve Phase 2 durability, project ownership checks, distinct conversation/run/graph-thread identities, fenced workers, transactional commands, and authenticated event reads.
- “Provider changes do not change execution authority.” Profiles contain model settings, never project roles, connector grants, tool permissions, or approval decisions.
- “Pin model/provider configuration per run and record any authorized fallback.” Credentials remain outside snapshots, messages, events, checkpoints, Git, and browser payloads.
- “Apply bounded retries to rate limits/transient failures”; enforce one shared time/attempt/token budget across initial invocation, retries, and fallback.
- “Unsupported capabilities are explained before invocation.” Phase 3 supports text conversations. Tool calls, structured output, attachments, and parallel tool calls remain unavailable through the harness until their own contracts and tests are delivered.
- “As tools and workflows arrive, their scenarios are added to this suite.” Declare those future fields now, but do not claim they work based on provider marketing or SDK availability.
- “Not yet: Compatibility claims for arbitrary endpoints merely because they advertise an OpenAI-compatible API.” Qualify a concrete endpoint implementation/version, profile, model, and API surface.
- Use the existing Python 3.11 minimum and PostgreSQL 15 Compose target. Keep the PoC under `poc/agent/` separate; do not expose its subprocess executor or rewrite its LLM client in Phase 3.
- Keep existing HTTP paths, database status values, and event names unless a change is explicitly called out below. This phase does not introduce tools, workflows, cloud operations, a credential-management UI, or a new token-delta transport.

## Review Focus

1. Configuration changes after submission or before restart: a queued run uses its original endpoint, model, limits, and fallback choices; an unavailable snapshot fails visibly (Tasks 3, 4, 10).
2. Cancellation or lease loss during a stalled stream/backoff: close the local provider stream, stop further attempts, and reject stale publication (Tasks 2, 4, 5, 10).
3. Output followed by disconnect, refusal, truncation, or malformed completion: never persist that partial output as a completed assistant message or automatically replay it (Tasks 2, 5–8).
4. Missing/cumulative usage and hidden SDK retries: preserve unknown usage, avoid double counting, and bound actual provider requests across all attempts (Tasks 2, 5–8).
5. Fallback to another destination or an unexpected model: require explicit authorization, preserve project scope, and expose the actual selected destination/model without leaking credentials (Tasks 1, 3, 5, 9, 10).

## 1. Starting Point and Scope Decisions

### Observed implementation at `d0aa5a7`

| Existing area | Observation | Phase 3 consequence |
| --- | --- | --- |
| `chat/llm.py` | One cached OpenAI client; global key/base URL/model; returns raw SDK chunks. | Removed; the worker uses normalized provider adapters exclusively. |
| `runs/graph.py` | Reads global settings, checks `OPENAI_API_KEY`, parses OpenAI `choices`/`usage`, buffers text, and accepts empty output. | Consume normalized events and require an explicit valid finish. |
| `runs/service.py`, `runs/models.py` | Submission persists a durable run but no provider configuration. | Add immutable snapshots and include profile selection in idempotency semantics. |
| `runs/worker.py` | Invokes a synchronous graph; heartbeat shares its SQLAlchemy session; cancellation fences publication but does not directly stop a model stream. | Give supervision its own DB session and an explicit cancellation/deadline path. |
| `runs/queue.py` | Finalization commits run status itself; provider-start marker controls crash reconciliation. | Add a fenced transaction boundary for provider attempt records and final message/event publication. |
| `events/service.py`, web chat store | Snapshots already include runs/messages; browser mostly rebuilds cards from events. | Hydrate provider/usage data from authoritative run snapshots, including after event retention. |
| Compose | API and worker receive only `OPENAI_API_KEY`; model/base URL settings are not forwarded. | Share the same non-secret profile configuration and necessary secret references across services. |
| CI and tests | Python CI currently tests the PoC only. Several `_pg` tests use SQLite; checkpoint tests use `MemorySaver`. | Add API CI and genuinely PostgreSQL-backed tests; filenames alone are not evidence of durability. |

### Verified planning baseline

The new worktree's frontend typecheck, 100 unit tests, and production build passed. The API suite completed with **102 passed, 7 failed** after rerunning outside the sandbox; the sandboxed test client stalled in its thread bridge. Six failures patch the absent `src.openship.chat.routes.settings` attribute, and one expects authentication on the absent legacy `POST /api/chat/{conversation_id}/messages` route but receives 404. This is the unchanged `d0aa5a7` baseline, not a Phase 3 regression. This is a historical baseline observation. The Phase 1 adapter and its tests have since been retired; useful auth/chat coverage now exercises durable run commands. Do not restore the old endpoint.

### Recommended approach and alternatives

Use direct provider SDK adapters. This gives explicit control over stream closure, usage semantics, request parameters, errors, and retry accounting while fitting the current runtime. A universal gateway would add another service and data destination; adopting LangChain chat wrappers as the provider boundary would add another normalization/retry layer. Neither is needed for this increment. LangGraph remains the orchestrator.

Keep the synchronous worker/graph entry points, but make provider I/O asynchronous inside a bounded runner invoked by `asyncio.run()` from the model node. Async task cancellation can interrupt a blocked read or backoff; checking cancellation only between synchronous chunks cannot. API processes submit work and never invoke providers themselves.

Add a profile selector to the existing composer and provider/model details to run cards. Profile definitions and credentials are configured by the installation operator; users select only server-approved profile IDs. Conversation history remains provider-neutral text, so switching provider on the next turn preserves the workspace. No provider-side conversation/session storage is required.

Normalized streaming in this phase means adapters and the worker consume the same stream events. The existing browser still receives durable progress and the completed message through SSE. Adding an interprocess transport for live token deltas is a separate decision, not a prerequisite for adapter portability.

### Delivery and acceptance units

| Unit | Tasks | Independently runnable result |
| --- | --- | --- |
| 3A: common foundation + OpenAI | 1–5, 9, foundation portion of 10–11 | Existing OpenAI chat through normalized contracts; selector, pinning, cancellation, budgets, and explicit fallback policy. |
| 3B: Anthropic | 6 + adapter-specific 10–11 | Same workspace scenario using the Anthropic Messages API. |
| 3C: Google | 7 + adapter-specific 10–11 | Same workspace scenario using the Gemini Developer API. |
| 3D: configured compatibility profile | 8 + endpoint-specific 10–11 | One qualified OpenAI Chat Completions endpoint/model works with declared limitations. |

Complete and demonstrate 3A before 3B; add 3C and 3D one at a time. Tasks 9–11 are shared delivery work with checks repeated for each new adapter. Do not accumulate all adapters before the first usable release. At every unit, record the user's **accept, revise, or stop** decision; automated tests do not supply that decision.

## 2. File and Responsibility Map

All API paths below are relative to `apps/api/`; web paths are relative to `apps/web/`.

| Files | Responsibility |
| --- | --- |
| Create `src/openship/providers/contracts.py`, `errors.py` | Frozen serializable profiles/snapshots, capabilities, requests/events, usage, safe normalized errors. |
| Create `providers/configuration.py`, `registry.py`, `routes.py` | Operator profile parsing, runtime credential resolution, exact profile lookup, authenticated public catalog. |
| Create `providers/openai.py`, `anthropic.py`, `google.py`, `openai_compatible.py` | Provider-specific mapping and transport lifecycle; import SDKs only here. |
| Create `providers/control.py`, `runner.py`, `attempts.py` | Deadlines/cancellation, retry/fallback/budget decisions, fenced attempt persistence. |
| Modify `config.py`, `main.py`, `chat/routes.py`, `runs/graph.py`; remove `chat/llm.py` and `chat/service.py` | Settings/router integration, removal of Phase 1 execution paths, provider-neutral graph node. |
| Modify `runs/models.py`, `runs/schemas.py`, `runs/service.py`, `runs/routes.py`, `runs/queue.py`, `runs/worker.py` | Pin selection, record attempts, supervise execution, publish only while owning the run. |
| Create `alembic/versions/005_phase3_provider_configuration.py` | Snapshot/selection columns and provider-attempt table; existing migration head is `004`. |
| Modify `events/schemas.py`, `events/service.py`, `events/logging.py` | Safe provider/run projections, ordered attempt events, redacted structured metadata. |
| Create `tests/providers/conftest.py`, `test_contracts.py`, `test_configuration.py`, `test_conformance.py`, `test_runner.py`, `test_openai.py`, `test_anthropic.py`, `test_google.py`, `test_openai_compatible.py` | Network-free contract, adapter, and supervision coverage with synthetic fixtures. |
| Create `tests/test_provider_routes.py`, `test_provider_selection.py`, `test_provider_worker.py` | Authenticated catalog, submit/idempotency validation, graph/worker integration. |
| Create `tests/integration/test_provider_pinning_postgres.py`, `test_provider_worker_postgres.py`; modify `tests/conftest.py` | Genuine PostgreSQL fixtures, migrations, restart, fencing, and cancellation races. |
| Create `tests/live/test_provider_smoke.py` | Opt-in live qualification; never called automatically by normal CI. |
| Modify `src/types/index.ts`, `src/services/api.ts`, `src/stores/chat.ts`, `src/views/ChatView.vue`, `src/components/chat/ChatInput.vue`, `RunCard.vue`; create `ProviderSelector.vue` | Catalog, next-turn selection, stable retry payloads, snapshot hydration, provider/usage display. |
| Create `tests/ProviderSelector.test.ts`, `tests/e2e/provider-portability.spec.ts`; modify `ChatStore.test.ts`, `RunCard.test.ts`, e2e fixtures | Selection, replay, fallback/error display, and browser acceptance. |
| Modify `apps/api/pyproject.toml`, `uv.lock`, `.env.example`, `README.md`, root `docker-compose.yml`, root `README.md`; create `docs/provider-support.md`, `docs/testing/provider-conformance.md` | Dependencies, deployment configuration, support matrix, qualification and user demonstration. |
| Modify `.github/workflows/python-ci.yml`; modify `.pre-commit-config.yaml` when API checks are introduced | Test the API as well as the PoC; retain existing frontend checks. |

## 3. Shared Contracts and Behavioral Decisions

### Configuration, selection, and pinning

- Require `MODEL_PROFILES_JSON`, a non-empty JSON array of non-secret installation profiles, and `MODEL_DEFAULT_PROFILE_ID`, naming one declared profile. Model names and endpoints live in profiles. `MODEL_MAX_TOKENS` is the installation output ceiling. Provider credentials, including `OPENAI_API_KEY`, are resolved only through explicit profile binding references; no synthetic single-provider profile is created.
- Each profile has `id`, `label`, `adapter` (`openai`, `anthropic`, `google`, `openai_compatible`), `model`, `endpoint`, `api_version`, `credential_ref`, `capabilities`, `limits`, `allowed_fallback_profile_ids`, `qualified_model_aliases: tuple[str, ...] = ()`, typed `adapter_options`, and optional `pricing`. Require operator-supplied model IDs for new adapters; do not guess a current model or select a `latest` alias on the user's behalf.
- Define an adapter-discriminated options union: OpenAI options choose `output_limit_parameter: Literal['max_tokens', 'max_completion_tokens']` and `token_counter_id: str | None`; Anthropic options disable server-side fallback and name the qualified counting strategy; Google options pin its native API version and disable automatic function calling; compatible options add `stream_usage_supported: bool`, `authentication: Literal['bearer', 'none']`, and `endpoint_implementation: str`. Reject arbitrary SDK kwargs. Optional pricing records `revision`, `currency: Literal['USD']`, and conservative input/output rates per million tokens that cover all admitted billing categories.
- `credential_ref` references an operator-declared environment credential binding. Resolve its value only in adapter/client construction. Keep reference names and full endpoints server-side; the catalog exposes a safe destination label, not URL query strings or headers. Local compatible endpoints may explicitly declare no authentication.
- Accept HTTPS endpoints and explicitly configured local/private HTTP compatibility endpoints. Reject userinfo, fragments, query-string credentials, and redirects to another destination. Browser requests cannot supply endpoints, keys, adapter implementations, or SDK kwargs.
- `TurnSubmitRequest` adds `provider_profile_id: str | None = None` and `fallback_profile_ids: list[str] = []`. Omission uses the installation default; a supplied list must be an ordered subset of the primary profile's allowed list. Empty list means fallback disabled.
- Persist `Run.model_selection` (canonical requested profile/default marker and fallback IDs) and `Run.model_configuration` (schema-versioned immutable `RunModelConfig`). The snapshot includes complete primary/fallback non-secret settings and their configuration digests, not just IDs that later resolve to changed settings.
- Check idempotency before revalidating today's profile settings: an exact replay returns the original run even if the installation default/profile has changed. A changed explicit selection, fallback order, or content with the same request key returns 409. Reuse ownership checks before returning anything.
- Historical terminal runs keep null snapshots and display “Provider not recorded.” Historical unstarted queued runs get a snapshot under their owning worker's fence before provider dispatch, marked `legacy_bound_at_execution`; do not pretend this is submission-time pinning. Previously dispatched interrupted runs retain the Phase 2 visible failure/reconciliation behavior.
- Removing/disabling a profile or revoking its credential blocks further dispatch; it never redirects a queued run. Credential rotation under the same reference is allowed, while model/endpoint/settings stay pinned. Validate configuration/schema compatibility before execution and expose a safe error on incompatible snapshots.

### Provider-neutral interfaces

Use frozen Pydantic models with `extra='forbid'`; serialize only contracts into checkpoints. Runtime clients, cancellation events, callbacks, and credential values are injected, never state fields.

| Contract | Exact boundary |
| --- | --- |
| `ChatMessage` | `role: Literal['system', 'user', 'assistant']`, `content: str`. Preserve content/order; extract the system message according to each native API. |
| `ProviderCapabilities` | `streaming: bool`, `usage_reporting: bool`, `context_tokens: int`, `max_output_tokens: int`, `token_counting: bool`, `tool_calls: bool`, `structured_output: bool`, `attachment_types: tuple[str, ...]`, `parallel_tool_calls: bool`. Effective advanced fields remain false/empty in Phase 3. |
| `RunLimits` | `max_output_tokens: int`, `max_attempts: int`, `run_timeout_seconds: float`, `request_timeout_seconds: float`, `max_context_bytes: int`, `max_total_tokens: int | None`, `max_cost_usd: Decimal | None`. |
| `PinnedProviderConfig` | Validated profile fields above, immutable; `profile_revision` is a digest of canonical non-secret configuration. |
| `RunModelConfig` | `schema_version: Literal[1]`, `primary: PinnedProviderConfig`, `fallbacks: tuple[PinnedProviderConfig, ...]`, `limits: RunLimits`, `binding_origin: Literal['submission', 'legacy_bound_at_execution']`. |
| `ModelRequest` | `messages: tuple[ChatMessage, ...]`, `max_output_tokens: int`, `required_capabilities: frozenset[str] = frozenset({'streaming'})`. Reject unknown/unsupported capabilities before dispatch. |
| `TokenUsage` | Nullable non-negative `input_tokens`, `output_tokens`, `total_tokens`, `cached_input_tokens`, `reasoning_tokens`; `source: Literal['provider', 'unavailable']`. Preserve provider totals; never replace missing values with zero. |
| `ProviderEvent` | Tagged union: `TextDelta(text: str)`, `UsageUpdate(usage: TokenUsage)`, `StreamFinished(reason: Literal['stop', 'length', 'refusal', 'tool_calls'], actual_model: str | None)`. Usage updates are cumulative snapshots, not increments. Errors raise `ProviderError`. |
| `ProviderAdapter` | `async stream(request: ModelRequest, config: PinnedProviderConfig, control: InvocationControl) -> AsyncIterator[ProviderEvent]`; `async count_input_tokens(request: ModelRequest, config: PinnedProviderConfig, control: InvocationControl) -> int`. Unsupported counting raises a safe capability error. |
| `InvocationControl` | `remaining_seconds() -> float`, `raise_if_stopped() -> None`, and an async cancellation signal; owns no serializable secrets. |
| `ProviderError` | `code`, fixed `safe_message`, `retryable`, optional validated `retry_after_seconds`; never serialize the original exception/HTTP body. Preserve original exceptions only for internal chaining with redacted logging. |
| `ProviderTurnResult` | `text: str`, `usage: TokenUsage | None`, `actual_model: str | None`, `selected_profile_id: str`, `attempt_count: int`, `finish_reason: str`, `partial: bool`. Only `stop` plus nonempty text can publish a completed message. |
| `run_model_turn` | `async run_model_turn(request: ModelRequest, config: RunModelConfig, control: InvocationControl, recorder: AttemptRecorder) -> ProviderTurnResult`. All adapters share this runner. |
| `AttemptRecorder` | `start(profile: PinnedProviderConfig, reason: str) -> UUID`; `finish(attempt_id: UUID, outcome: str, usage: TokenUsage | None, error_code: str | None) -> None`. Production implementation checks current DB fence/status; tests use a fake recorder. |

Do not introduce a tool execution or generic plugin interface just to fill future fields. Unsupported tool results fail safely. Expand message/event schemas with tool-call IDs, server-side argument validation, and result blocks when Phase 4/7 introduces those scenarios.

Normalize error codes to `configuration_error`, `authentication_error`, `invalid_request`, `unsupported_capability`, `context_limit_exceeded`, `rate_limited`, `provider_unavailable`, `invalid_response`, `model_refused`, `output_limit_exceeded`, `unexpected_model`, `budget_exceeded`, `model_interrupted`, `cancelled`, and `lease_lost`. Only `rate_limited` and `provider_unavailable` are retryable before output. A timed-out generation request may map to `provider_unavailable` while budget remains; the shared deadline maps to `budget_exceeded` and the existing `timed_out` run status. Public messages identify a corrective action without including the raw provider exception.

### Retry, fallback, cancellation, and budgets

The following are proposed defaults, not thresholds measured in the roadmap:

| Setting | Proposed value/behavior |
| --- | --- |
| Output limit | `4000`, retaining `MODEL_MAX_TOKENS`; reject values beyond the selected model/profile limit. |
| Total attempts | `3` generation requests across primary retries and all fallback destinations; counts are separate from the queue's `Run.attempt`. |
| Total model-turn deadline | `120` seconds, including token counting, request time, retry sleeps, and fallback. |
| Per-attempt request deadline | `45` seconds, capped by remaining run time. |
| Backoff | Full jitter in `[0, min(8, 2 ** (attempt_number - 1))]` seconds; injectable clock/random in tests. |
| `Retry-After` | Honor a valid delay instead of shortening it; if it does not fit the remaining deadline, stop with a budget error. |
| Context bound | `65536` UTF-8 bytes including the system message; never silently drop conversation turns to fit. Profiles may impose a smaller bound. |
| Cancellation observation | Dedicated supervisor DB poll every `0.5` seconds; close the local stream within `2` seconds in controlled transport tests. This does not promise cancellation of provider-side billing/generation. |
| Fallback | Disabled by default. Allowed only before answer content, only for normalized rate-limit/unavailable errors, and only to the explicit pinned list. |
| Cost limit | Optional operator-set USD ceiling; only accept it when the qualified profile has a pinned conservative pricing schedule and token-counting support. Unknown pricing/usage cannot be represented as zero cost. |

Disable SDK automatic retries so the runner owns request counts. Retry only transient errors before any answer content and within limits; do not retry authentication, invalid request, unsupported capability, refusal, malformed stream, or a stream that already yielded text. Exhausting the shared budget prevents fallback too. Prioritize an authorized next fallback after an eligible failure; when none remains, retry the current destination if permitted. Never revisit an earlier destination in the same turn.

A cross-provider fallback sends conversation context to another destination: show that choice in the UI and persist it with the command. An installation allowlist makes a destination eligible; the user's explicit fallback list authorizes that turn's use. An unexpected provider-reported model fails with `unexpected_model` unless it is in the profile's explicitly qualified alias-resolution set; record requested and actual model separately.

Before generation, token-aware profiles count the actual mapped request and reserve input plus maximum output for each possible attempt against `max_total_tokens` and any cost ceiling. Counting requests also use the deadline and cancellation control. Without a qualified counter/pricing schedule, reject a requested token/cost ceiling that cannot be enforced; byte and output limits still apply. Unknown charged usage from a failed attempt consumes its reserved upper bound before any retry. Do not describe this conservative admission bound as an exact provider invoice.

`stop` means normal completion. `length` maps to `output_limit_exceeded`; refusal to `model_refused`; unexpected tool output to `unsupported_capability`; missing finish marker/empty answer to `invalid_response`; cancellation to the existing cancelled status; deadline exhaustion to `timed_out`. Partial text remains an internal result and never enters history as a completed assistant answer. Persist any known usage for failed/cancelled attempts as well as successful ones.

### Persistence and public API

Create `provider_attempts` with UUID ID/run FK, ordinal, current fence, profile ID/revision, adapter, requested/actual model, destination label, start/end times, outcome, normalized error code, and nullable usage/cost reservation. Enforce `UNIQUE(run_id, ordinal)` and access through the run's owned conversation. Do not store prompts or SDK response bodies there.

- `GET /api/model-profiles`: authenticated catalog `{default_profile_id, profiles}`. Each item exposes ID/label/adapter/model/destination label, effective capabilities, available/configuration-needed state, safe reason, and allowed fallback profile IDs. Return profile availability without secret values or internal credential references.
- Keep `POST /api/runs/{conversation_id}/turns`, `GET /api/runs/{run_id}`, and `POST /api/runs/{run_id}/cancel`. Add selection fields to submit and safe `model`, `usage`, `error`, and `provider_attempts` projections to run reads/snapshot run summaries. Use the durable run/event API exclusively. Authentication and ownership remain enforced on those endpoints, configuration errors use structured validation responses, and the model catalog reports missing installation configuration safely. The retired Phase 1 POST chat endpoint is not registered.
- Missing/unknown/unavailable requested profiles and incompatible capabilities produce actionable structured 422 errors before queuing. Revocation after queuing produces a visible run failure before dispatch. Validate ownership first so these errors cannot expose another user's run.
- Add durable `model.attempt.started`, `model.attempt.finished`, and `model.fallback.selected` events with safe metadata. Keep existing `turn_submitted`, `run.started`, `run.succeeded`, `run.failed`, and `run_cancelled` names. Include safe provider/usage/error summary fields in terminal events; snapshot reconstruction must work after those events are pruned.
- Persist each admitted attempt before network dispatch. Commit attempt updates with their events/outbox under the active fence. Finalize run, assistant message, final attempt/usage, and terminal event/outbox in one transaction; refactor the queue helper's internal commit so a successful status cannot precede a lost message.
- Preserve `Run.usage` as JSON text for compatibility, using an explicit schema version in the payload. Store nullable provider counts plus completeness; keep the existing `estimated` field for old consumers, document its legacy semantics, and never label actual partial counts as invented estimates in new UI copy.

## 4. Implementation Tasks

### Task 1: Define contracts, validated profiles, and a server-owned registry

**Files:** Create `providers/contracts.py`, `errors.py`, `configuration.py`, `registry.py`, `__init__.py`; modify `config.py`; create `tests/providers/test_contracts.py`, `test_configuration.py`, and shared test fixtures.

**Interfaces:** Produce the types in §3; `load_profiles(settings: Settings) -> ProviderRegistry`; `ProviderRegistry.get(profile_id: str) -> PinnedProviderConfig`; `pin_selection(profile_id: str | None, fallback_ids: list[str]) -> RunModelConfig`; `validate_request(request: ModelRequest, profile: PinnedProviderConfig) -> None`. Registry contains only allowlisted adapter factories and non-secret configuration.

- [ ] **Write failing tests:** `test_single_provider_settings_cannot_create_an_implicit_profile`, `test_explicit_profiles_require_a_named_default`, `test_snapshot_roundtrip_is_frozen_and_secret_free`, `test_reject_unknown_profile_duplicate_id_and_extra_fields`, `test_reject_endpoint_userinfo_and_external_redirect`, `test_unsupported_capability_fails_before_adapter_creation`, `test_fallback_requires_explicit_allowed_ids`. Assert defaults `4000/3/120/45/65536`, empty fallback list, positive limits, and false advanced capability fields.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/providers/test_contracts.py tests/providers/test_configuration.py -q`; expect missing contracts/functions.
- [ ] **Implement:** Parse profiles once per process, validate schema and adapter-specific endpoint/API settings, create immutable deep snapshots, and compute stable canonical digests. Add an operator-owned credential resolver with a redacted representation; do not use user input to select arbitrary environment variables.
- [ ] **Run green:** Repeat the targeted command; assert no test resolves a live credential or makes a network request. Add tests for cost/token ceilings without counters/pricing and explicit unauthenticated local profiles.
- [ ] **Commit:** `feat: define normalized provider profiles and contracts`.

### Task 2: Normalize OpenAI streaming and establish the conformance suite

**Files:** Create `providers/openai.py`, `control.py`, `tests/providers/test_openai.py`, `test_conformance.py`, `tests/providers/conftest.py`; remove the obsolete `chat/llm.py` and `chat/service.py` execution path.

**Interfaces:** Produce `OpenAIAdapter.stream()` and `count_input_tokens()` with the common signatures, `InvocationControl`, and reusable fixture `adapter_case`. Each case supplies a real adapter against a synthetic SDK/transport, qualified profile, event fixtures, and a cleanup assertion.

- [ ] **Write failing tests:** Shared cases `test_system_and_history_mapping`, `test_unicode_deltas_preserve_order`, `test_finish_is_required`, `test_empty_and_partial_stream_are_not_completed`, `test_usage_missing_stays_unknown`, `test_cumulative_usage_is_not_summed`, `test_error_is_safe`, `test_close_on_cancel_and_deadline`, `test_capability_rejected_before_network`. OpenAI-specific tests assert empty `choices` on the usage chunk, `stream_options.include_usage=True`, correct profile-specific output parameter, and SDK retries disabled.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/providers/test_openai.py tests/providers/test_conformance.py -q`; expect the new adapter boundary to be absent.
- [ ] **Implement:** Use `AsyncOpenAI` with explicit endpoint/timeouts and `max_retries=0`; translate SDK chunks/errors into the common contracts and close transports in `finally`. Keep Chat Completions as the qualified baseline API. Request usage explicitly. Output parameter selection belongs to the model profile; do not assume all models accept the existing `max_tokens` argument.
- [ ] **Verify counting and the provider boundary:** Implement a qualified local tokenizer strategy for supported OpenAI profiles or declare counting unavailable and reject token/cost ceilings. The worker uses the normalized runner; there is no direct chat facade or alternate execution service. Test that no graph/runtime module receives SDK objects.
- [ ] **Run green:** Repeat targeted tests plus `uv run pytest tests/test_model_step.py tests/test_usage_retention.py -q` before graph migration. Assert proper cleanup even when no chunk is ever received.
- [ ] **Commit:** `feat: add conformant OpenAI streaming adapter`.

OpenAI documents an explicit [streaming usage option](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create); its [Python SDK reference](https://developers.openai.com/api/reference/python) documents configurable retries and timeouts. This plan deliberately assigns retry control to OpenShip.

### Task 3: Persist run selection and provider attempts with migration coverage

**Files:** Modify `runs/models.py`, `schemas.py`, `service.py`, `routes.py`, `events/schemas.py`, `events/service.py`; create migration `005_phase3_provider_configuration.py`, `providers/attempts.py`, `tests/test_provider_selection.py`, `tests/integration/test_provider_pinning_postgres.py`; extend `tests/conftest.py`.

**Interfaces:** Extend `submit_turn(..., provider_profile_id: str | None = None, fallback_profile_ids: list[str] | None = None) -> Run`; produce `load_run_model_config(run: Run) -> RunModelConfig`, and `DatabaseAttemptRecorder(session_factory, run_id: UUID, fence: int)` implementing `AttemptRecorder`. Snapshot/run response serializers return only the public projection.

- [ ] **Write failing tests:** `test_upgrade_preserves_phase2_rows`, `test_terminal_legacy_provider_is_unknown`, `test_same_request_replays_original_config_after_default_change`, `test_changed_profile_or_fallback_order_conflicts`, `test_snapshot_pins_primary_and_fallback_settings`, `test_foreign_run_does_not_leak_profile`, `test_attempt_requires_current_fence`, `test_attempt_and_outbox_rollback_together`. Seed migration revision `004`, including completed, queued, and dispatched runs.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/test_provider_selection.py tests/integration/test_provider_pinning_postgres.py -q`; the PostgreSQL module must require `TEST_POSTGRES_URL` and fail clearly in its dedicated CI lane when absent, rather than substituting SQLite.
- [ ] **Implement migration:** Add nullable JSON snapshot/selection columns and the attempt table/indexes. Do not backfill historical provider names from current settings. Downgrade refuses to erase non-null Phase 3 snapshots/attempts without an explicit destructive migration decision; test an empty downgrade and the data-protection refusal.
- [ ] **Implement submission and recording:** Resolve owned conversation, detect replay, validate selection and capabilities, then pin configuration in the existing message/run/job/event transaction. Resolve today's credentials only at dispatch. Record attempt ordinal, fence, metadata, usage, and safe events atomically.
- [ ] **Run green:** Repeat the suite on PostgreSQL 15 and SQLite-supported command tests; inspect snapshot JSON/event payloads for a synthetic secret sentinel. Assert each duplicate request produces one user message, one run, and one job.
- [ ] **Commit:** `feat: pin provider configuration on durable runs`.

### Task 4: Run normalized providers under cancellation and fenced supervision

**Files:** Modify `runs/graph.py`, `worker.py`, `queue.py`; create `providers/runner.py`, `tests/test_provider_worker.py`; update `tests/test_model_step.py` and checkpoint tests.

**Interfaces:** Produce a one-attempt `run_model_turn()` initially, then Task 5 extends its policy. `model_step(state: ChatState) -> dict` consumes `model_configuration` and messages; `build_chat_graph(checkpointer=None, invocation_factory=None)` injects runtime control/recorder without serializing them. Worker supervision creates a dedicated session per thread and supplies current run/fence to the invocation factory.

- [ ] **Write failing tests:** `test_worker_uses_snapshot_not_global_settings`, `test_cancel_interrupts_blocked_read`, `test_lease_loss_stops_dispatch_and_publication`, `test_heartbeat_has_independent_session`, `test_legacy_queued_run_binds_once_under_fence`, `test_snapshot_schema_mismatch_fails_safely`, `test_completion_message_and_event_commit_together`. Use a blocking async fake transport to prove cancellation without relying on another chunk arriving.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/test_provider_worker.py tests/test_model_step.py -q`; expect OpenAI-specific parsing/global-setting assertions to fail against the new contract.
- [ ] **Implement execution:** Run provider I/O in a cancellable async task with a watchdog for run deadline and DB stop/fence status. Replace raw SDK iteration in `model_step`, preserve auto-title behavior after valid completion, and store only canonical text/config/result in graph state. Treat denied heartbeat/failed DB ownership verification as a stop signal.
- [ ] **Implement publication:** Refactor `finalize()` to support caller-owned transactions while preserving existing callers. Require fence/current running status under the same transaction as assistant message, terminal event/outbox, and usage. Mark provider dispatch immediately before each admitted generation request; do not mark token counting as generation start.
- [ ] **Run green:** Repeat targeted tests and `uv run pytest tests/integration/test_chat_checkpoints.py tests/integration/test_worker_recovery_pg.py -q`. These existing tests are regressions, not PostgreSQL qualification; Task 10 provides that evidence. Assert no secret/client/control/recorder reaches checkpoint serialization.
- [ ] **Commit:** `feat: supervise provider execution with cancellation and fencing`.

### Task 5: Enforce shared retry, fallback, and accounting budgets

**Files:** Extend `providers/runner.py`, `control.py`, `attempts.py`; create `tests/providers/test_runner.py`; modify usage/logging projections and `runs/queue.py` error/status mapping.

**Interfaces:** Complete `run_model_turn()` and `ProviderTurnResult` from §3. Inject clock, sleeper, and jitter sources into runner construction for deterministic tests. Define fixed safe error copy in `errors.py`; expose `ProviderError.code` separately from public text.

- [ ] **Write failing tests:** `test_429_then_success_is_two_attempts`, `test_sdk_retries_do_not_multiply_requests`, `test_three_attempt_limit_includes_fallback`, `test_retry_after_beyond_deadline_stops`, `test_cancel_during_backoff_stops_next_request`, `test_partial_output_never_retries_or_falls_back`, `test_auth_and_refusal_never_fall_back`, `test_unauthorized_fallback_makes_no_request`, `test_unexpected_actual_model_fails`, `test_unknown_failed_usage_consumes_reservation`, `test_cost_limit_requires_qualified_pricing`.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/providers/test_runner.py -q`; expect the one-attempt runner to fail policy cases.
- [ ] **Implement retry/fallback:** Apply the exact policy in §3, including full jitter and unshortened `Retry-After`. Persist attempt/fallback decisions before dispatch; retain one deadline and max-attempt counter across destinations. Check stop/fence status again after backoff and before every new request.
- [ ] **Implement budgets and outcomes:** Enforce context bytes, profile output limit, optional counted token/cost reservations, total time, and zero allowed tool execution. Merge cumulative usage by replacement, preserve separate provider totals and cache/reasoning fields, persist known failed-attempt usage, and expose aggregate completeness without fabricating counts.
- [ ] **Run green:** Repeat runner/conformance tests with virtual time; assert attempts `<=3`, no dispatch after deadline, and stream closure on every terminal path. Verify `timed_out` is a real DB status and cannot be displayed as completed.
- [ ] **Commit:** `feat: enforce bounded model retries and authorized fallback`.

### Task 6: Add and independently qualify the Anthropic adapter

**Files:** Create `providers/anthropic.py`, `tests/providers/test_anthropic.py`, synthetic fixtures; modify registry, `pyproject.toml`, `uv.lock`, configuration docs/support matrix.

**Interfaces:** `AnthropicAdapter` implements the unchanged adapter contract and registers as `anthropic`. Consume the same normalized request, pinned limits, control, and runner; no Anthropic branching in graph/worker/UI.

- [ ] **Write failing tests:** Native system-message extraction; ordered user/assistant history; text block deltas; ping/unknown non-content events; cumulative output usage; provider-side stream error after HTTP success; refusal/length stop reasons; secret-safe errors; stalled cancellation. Unexpected thinking/tool blocks must not become assistant text or execute anything.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/providers/test_anthropic.py tests/providers/test_conformance.py -q`; the shared suite must include Anthropic, not merely run alongside its tests.
- [ ] **Implement:** Add the maintained `anthropic` SDK, lock it, and use `AsyncAnthropic` Messages streaming with retries disabled and bounded timeouts. Map input/start and final usage correctly. Implement native input counting when qualified, or advertise counting unavailable. Keep provider/server-side fallback disabled unless its serving-model behavior is explicitly covered by the profile's qualification.
- [ ] **Run green:** Repeat native and shared tests; update normal Compose configuration and browser fixtures to expose the new configured profile. Run Task 10's provider-neutral worker scenario using Anthropic fixtures.
- [ ] **Demonstrate and record acceptance:** Execute Task 11's scenario against a concrete configured Anthropic model; record the exact model/API/SDK and result. Credentials absent means live qualification remains pending and the support matrix says so.
- [ ] **Commit:** `feat: add conformant Anthropic model adapter`.

Anthropic's [streaming contract](https://platform.claude.com/docs/en/build-with-claude/streaming) describes text blocks, cumulative usage, and stream-level error events; its [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python) permits explicit retry control.

### Task 7: Add and independently qualify the Google adapter

**Files:** Create `providers/google.py`, `tests/providers/test_google.py`, synthetic fixtures; modify registry, `pyproject.toml`, `uv.lock`, profile documentation/support matrix.

**Interfaces:** `GoogleAdapter` registers as `google`, implements the common contract, and targets the Gemini Developer API. Enterprise/Vertex credentials and separate cloud endpoints are outside this acceptance unit.

- [ ] **Write failing tests:** System instruction separated from contents; assistant role mapped to native `model`; ordered text parts; usage snapshots; no candidate/safety-blocked response; length finish; non-text/thought parts not rendered; unexpected function calls rejected; no automatic SDK function/MCP execution; cancellation during blocked read.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/providers/test_google.py tests/providers/test_conformance.py -q`; include Google in shared adapter parameterization.
- [ ] **Implement:** Add and lock `google-genai`; use its async `models.generate_content_stream` boundary for the qualified API version. Disable automatic function calling, provide no Python callable tools/MCP sessions, and normalize content, finish reasons, safety/refusal, and usage. Set SDK transport retry behavior so it cannot bypass the runner's attempt budget; prove actual wire-request counts with a mocked transport.
- [ ] **Run green:** Repeat native/shared tests and provider-neutral worker scenario. Implement supported native token counting within the same deadline or expose its absence; never substitute a character-based estimate for provider-reported usage.
- [ ] **Demonstrate and record acceptance:** Execute Task 11 against a concrete configured Gemini model; record model/API version/SDK and limitations separately from the other providers.
- [ ] **Commit:** `feat: add conformant Google model adapter`.

The maintained [Google Gen AI SDK documentation](https://googleapis.github.io/python-genai/) documents async streaming, token counting, and automatic function calling. This adapter uses model transport only; execution remains a harness responsibility.

### Task 8: Qualify configurable OpenAI-compatible profiles conservatively

**Files:** Create `providers/openai_compatible.py`, `tests/providers/test_openai_compatible.py`; modify registry, profile validation, fixtures, support matrix.

**Interfaces:** `OpenAICompatibleAdapter` reuses OpenAI transport mapping where proven and registers as `openai_compatible`. Profile options explicitly identify output-limit parameter, usage-stream option support, finish behavior, token-counter availability, authentication mode, qualified aliases, endpoint implementation/version, and API version.

- [ ] **Write failing tests:** Standard chunks; missing usage; usage-only chunk; rejected `stream_options`; malformed JSON/chunk; missing terminal marker; unknown model alias; local endpoint with no API key; redirect denied; explicit compatibility options do not change primary OpenAI behavior.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/providers/test_openai_compatible.py tests/providers/test_conformance.py -q`; compatible cases must still meet the common completion/cancellation/error requirements.
- [ ] **Implement:** Make differences declared profile options, not broad exception handling. Do not catch a rejected parameter and silently resend a changed request; the operator must configure the documented option. Defaults are conservative: advanced features false and usage unknown when absent. Never attach installation OpenAI credentials to a separately configured endpoint.
- [ ] **Run green:** Run the shared suite against a controlled endpoint fixture; verify no skipped mandatory cases. Mark unsupported optional capabilities explicitly and reject requests requiring them before network invocation.
- [ ] **Demonstrate and record acceptance:** Run Task 11 against one operator-selected endpoint/model, record implementation/version, and qualify only that tuple. A fixture pass alone proves adapter logic, not an arbitrary deployment.
- [ ] **Commit:** `feat: support qualified OpenAI-compatible model profiles`.

### Task 9: Expose provider selection and honest run details in the workspace

**Files:** Create `providers/routes.py`, `tests/test_provider_routes.py`, web `ProviderSelector.vue`, `ProviderSelector.test.ts`; modify `chat/routes.py`, API router/run/snapshot schemas and serializers, `tests/test_chat_send.py`, `tests/test_integration_auth_chat.py`, `tests/test_run_commands.py`, `tests/test_workspace.py`, web files listed in §2, `ChatStore.test.ts`, `RunCard.test.ts`.

**Interfaces:** `GET /api/model-profiles`; web `modelProfilesApi.list()`; `ProviderProfileSummary`, `RunModelSummary`, `ProviderAttemptSummary`; chat store `selectedProfileId`, `selectedFallbackIds`, `loadModelProfiles()`. `sendMessage()` captures selection and one client request ID before submission; retrying that submission reuses the captured payload.

- [ ] **Write failing API tests:** Catalog requires authentication; reveals no credentials/endpoints with secrets; unconfigured profiles return a safe reason; unknown profile rejected; run reads/snapshots include pinned metadata and nullable usage; foreign conversation/run remains 404.
- [ ] **Run red:** `cd apps/api && uv run pytest tests/test_provider_routes.py -q`; expect absent catalog/projections.
- [ ] **Implement API:** Return only installation-approved profiles and safe capability metadata. Extend serializers without dumping ORM columns or credential references. Include fallback choice and actual serving model in run projections. Give test workers explicitly declared synthetic profiles and exercise chat through the durable turn submission, run reads, history, and event replay. Assert the retired POST chat endpoint is absent and missing profiles cannot create an implicit default. Browser disconnect does not cancel a durable run.
- [ ] **Write failing browser tests:** Default selected; switch affects only next turn; disabled profile explains configuration need; pending run retains provider after changing selection; explicit fallback opt-in shows destinations; network retry keeps original payload; usage unknown displays “Not reported”; old run displays “Provider not recorded”; snapshot alone reconstructs provider/error/usage after event pruning.
- [ ] **Implement browser:** Use existing UI primitives and accessible labelled controls. Disable selection changes while the current submission is pending; after a run exists, next-turn selection is independent. Preserve an earlier choice if still eligible, otherwise show why it needs replacement. Provider errors use common copy, and run cards show actual attempts/fallback. Do not display raw SDK errors or private reasoning.
- [ ] **Run green:** `cd apps/web && pnpm typecheck` and `pnpm test:run`; run API selection/event regression suites. Verify keyboard selection, missing catalog recovery, and stale/duplicate event handling.
- [ ] **Commit:** `feat: select model profiles in the durable chat workspace`.

### Task 10: Prove PostgreSQL durability and browser behavior for each adapter

**Files:** Create `tests/integration/test_provider_worker_postgres.py`, extend pinning tests and fixtures; create web `tests/e2e/provider-portability.spec.ts`, update e2e API fixtures; modify Python CI and API pre-commit checks.

**Interfaces:** Test fixture `postgres_session_factory` requires an isolated PostgreSQL 15 database; provider fixtures use the actual adapter with controlled transport. Tests start a worker against the DB instead of only invoking `model_step()`; Playwright exercises catalog → submit → events → snapshot → cancel.

- [ ] **Write failing integration tests:** `test_queued_snapshot_survives_worker_restart_and_config_change`, `test_started_request_is_not_blindly_replayed_after_crash`, `test_cancel_fences_late_result_and_closes_stream`, `test_stale_worker_cannot_publish_attempt_or_message`, `test_two_workers_do_not_duplicate_dispatch`, `test_terminal_commit_is_atomic`, `test_snapshot_after_retention_preserves_provider_usage`, and `test_checkpoint_is_secret_free`. Parameterize adapter fixtures as each unit ships.
- [ ] **Run red:** `cd apps/api && TEST_POSTGRES_URL=<disposable-postgresql-url> uv run pytest tests/integration/test_provider_pinning_postgres.py tests/integration/test_provider_worker_postgres.py -q`. PostgreSQL-specific locks, JSON serialization, migrations, and saver writes must be exercised against PostgreSQL; no SQLite substitution.
- [ ] **Add browser scenarios:** Same existing conversation switches from profile A to B, survives reload, shows an unavailable profile before submit, reports a bounded outage, cancels a stalled provider, and shows an explicitly selected fallback. Assert one completed assistant message per successful turn and none after failed/cancelled partial output.
- [ ] **Implement CI:** Add separate API unit and PostgreSQL service jobs for Python 3.11/3.12/3.13 as supported by the lockfile; preserve the PoC jobs. Configure the unit lane to exclude PostgreSQL/live tests; the PostgreSQL job must require rather than skip its dedicated tests. Ordinary PR jobs use synthetic responses and no vendor API keys. Add API validation to pre-commit without removing existing frontend/PoC checks.
- [ ] **Run green:** API unit lane, required PostgreSQL lane, frontend typecheck/unit/build, and `cd apps/web && pnpm test:e2e tests/e2e/provider-portability.spec.ts`. Expected: no failed mandatory cases, no duplicate messages, no credential sentinel in persistence/log/event/browser capture.
- [ ] **Commit:** `test: qualify provider portability across restart and cancellation`.

### Task 11: Package, document, and demonstrate each acceptance unit

**Files:** Modify API `.env.example`, `README.md`, root `README.md`, `docker-compose.yml`; create `docs/provider-support.md`, `docs/testing/provider-conformance.md`, `tests/live/test_provider_smoke.py`; update dependencies/lockfile only in tasks that add SDKs.

**Interfaces:** Shared Compose profile configuration for API/worker; credential values only through environment bindings. Mark live tests `provider_live`; add registered `postgres` and `provider_live` pytest markers. `OPENSHIP_LIVE_PROFILE_ID` selects one explicit installed profile for opt-in qualification.

- [ ] **Write packaging checks:** Assert API/worker receive identical non-secret profile/default settings and required credential bindings; unavailable credentials leave the service bootable with a safe catalog reason. Validate Compose rendering without printing interpolated secrets. Test smoke selection cannot choose an unknown profile or contact a destination not in installation configuration.
- [ ] **Implement deployment/docs:** Provide redacted JSON examples for each adapter, exact environment-variable mapping, keyless local compatibility option, default/fallback behavior, limits, credential rotation/revocation, migration/rollback constraints, and provider data destinations. Do not embed developer paths or real keys. Explain that stream closure does not guarantee cancellation of remote generation/billing.
- [ ] **Document support matrix:** For every qualified tuple record adapter, endpoint implementation/API version, exact requested and observed model, SDK version, effective capabilities, mandatory test results, live qualification date, and user acceptance. States are `fixture-tested`, `live-qualified`, and `accepted`; credentials absent keeps the later states pending. Model context/output limits, tokenizer/pricing schedules, and accepted aliases must come from that tuple's evidence.
- [ ] **Run full release checks:** From `apps/api`, `uv run pytest tests/ -q -m 'not provider_live and not postgres'`, then the required PostgreSQL modules with `TEST_POSTGRES_URL`; from `apps/web`, `pnpm typecheck`, `pnpm test:run`, `pnpm build`, and the portability Playwright scenario. Verify existing PoC tests if changed hooks/dependencies affect them; run `git diff --check` and staged secret review before commit/push.
- [ ] **Perform live demonstration:** Follow §5 once for the current acceptance unit using `cd apps/api && OPENSHIP_LIVE_PROFILE_ID=<configured-profile-id> uv run pytest tests/live/test_provider_smoke.py -m provider_live -q`. This is opt-in and can incur provider charges; ordinary tests never run it. Record evidence and obtain **accept, revise, or stop** for that unit before claiming acceptance.
- [ ] **Commit:** `docs: document provider support and Phase 3 qualification`.

## 5. User Demonstration and Measurable Exit Gates

Use the same scenario for every accepted profile; answers need not be textually identical.

1. Start from documented Compose instructions, sign in, and open the existing workspace.
2. Select the profile and ask: “Describe three checks for a failing deployment without executing commands.” Verify a completed message, recorded requested/actual model, and reported or explicitly unknown usage.
3. Ask: “Which check would you perform first, and why?” Verify previous conversation context is preserved. Repeat to ten successful conversation turns for each acceptance unit.
4. Select a different accepted profile for the next turn in the same conversation; verify history/project/run identity stays correct and previous run metadata does not change.
5. Submit a longer response and cancel it. In controlled tests, local provider I/O closes within two seconds of observed cancellation; no late assistant message or further retry is published.
6. Disconnect/reload the browser; restart a worker before dispatch and after dispatch. The queued run keeps its snapshot; an interrupted request is visibly failed/reconciled according to Phase 2, without silent replay or fabricated completion.
7. In controlled fault tests, return a rate limit/outage, missing credentials, unsupported capability, empty/malformed/partial stream, and exhausted budget. Each produces the same normalized actionable behavior for all adapters.
8. Enable one allowed fallback explicitly, induce an eligible pre-content outage, and verify destination choice/attempts remain visible after reconnect. Repeat with fallback disabled and verify no alternate destination is contacted.

| Roadmap exit gate | Required evidence | Owner |
| --- | --- | --- |
| Same applicable conformance suite | Every adapter executes the shared mandatory text/stream/error/cancel/usage cases; optional unsupported capabilities are declared and their invocation rejected. | Tasks 2, 6–8 |
| Unsupported capabilities explained before invocation | Capability/unit tests show zero generation requests for unsupported inputs; browser explains unavailable profiles; malformed model output never executes anything. | Tasks 1, 9 |
| Rate limits/outages respect budgets | Virtual-clock tests and actual transport request counts prove `<=3` generation attempts, deadline enforcement, and bounded sleep/cancellation across fallback. | Tasks 5, 10 |
| Provider changes preserve authority | Same authenticated owner/project checks; explicit fallback destination list; no credentials/execution grants in model state; Google automatic execution disabled. | Tasks 3, 7, 9–10 |
| Independently demonstrated/accepted adapters | Ten-turn scenario plus cancel, reconnect, restart, and configuration failure; exact supported tuple and user acceptance recorded separately. | Task 11 |
| Suite grows with later tools/workflows | Documentation names the extension point and requires tool schema/argument/result and workflow/recovery cases when Phases 4/7 enable them. | Tasks 1, 11 |

## 6. Dependencies, Risks, and Implementation Readiness

- Phase 2 is merged in PR #21; the worktree was created from latest `origin/main` at `d0aa5a7`. Keep planning and later implementation on branch `task/phase3-model-provider-portability` in `.worktrees/phase3-model-provider-portability`. Refresh/rebase before implementation when safe, then verify any changed assumptions.
- Concrete model/endpoint/account access is an implementation qualification input, not a reason to invent current model IDs now. Preserve the existing configured OpenAI default; require explicit IDs for new profiles. Fixture work can complete without credentials; live qualification cannot.
- Conservative pricing/token reservations protect admission but cannot certify a provider's invoice. Unknown usage remains unknown; profiles lacking verified counters/prices cannot enable ceilings they cannot enforce.
- Async supervision is a deliberate adaptation inside the synchronous graph. Checkpoint/runtime objects must remain separate, and cancellation/lease-loss races need the PostgreSQL and stalled-transport tests before acceptance.
- Provider configuration is deployed consistently to API/worker. Drift, removal, unsupported schema/adapter versions, or revoked references cause a visible configuration error, never a different provider invocation.
- SDK and model/API behavior can change. Pin dependencies, qualify exact tuples, refresh the official references during implementation, and rerun conformance when upgrading. Do not claim arbitrary model aliases or compatible endpoints remain qualified indefinitely.
- API/worker changes expose existing Phase 2 publication/session boundaries. The scoped transaction/session corrections in Tasks 3–4 are necessary to keep provider attempt records and cancellation trustworthy; unrelated infrastructure refactors are excluded.

This plan is ready for review. Implementing it and accepting each adapter are separate actions from preparing this document.
