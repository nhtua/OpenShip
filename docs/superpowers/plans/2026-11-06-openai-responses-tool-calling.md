---
type: plan
summary: Support durable Sol and Terra tool calls via OpenAI Responses, while preserving Luna and fixing Python dictionary arguments.
date: 2026-11-06
status: done
---

# OpenAI Responses Tool Calling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Do not delegate unless the user explicitly requests it.

**Goal:** Support durable Sol and Terra tool calls via Responses, while preserving Luna and fixing Python dictionary arguments.

**Architecture:** Explicit pinned OpenAI API selection retains the existing Chat Completions path and adds a stateless Responses adapter. Canonical schemas remain authoritative; bounded private continuation is stored under the run fence before tool execution and replayed on resume.

**Tech Stack:** Python, Pydantic, OpenAI Python SDK, LangGraph, PostgreSQL, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-06-openai-responses-and-tool-schemas-design.md`

## Global Constraints

- Preserve staged `apps/api/src/openship/sandboxes/python_tool.py` work; investigate three prior API hook failures before modifying implementation.
- No general task planner, hosted tools, parallel execution, SDK retries, model-name-based protocol routing, or automatic endpoint fallback.
- Responses uses `store=False`, `parallel_tool_calls=False`, explicit encrypted-reasoning inclusion, and a pinned reasoning effort.
- Continuation is private, at most 512 KiB and 128 items per turn, and included in existing context limits.
- Retain fenced tool admission, pinned configuration, artifact authority, existing budgets, and no duplicate execution on resume.
- Never change `.env`, restart services, touch user's artifact volumes, push, or deploy without explicit permission.
- Explicitly enabled live tests fail rather than skip missing prerequisites; use disposable storage and database.
- Use git-workflow file-based scanned commit messages; never commit unrelated staged work.

## Review Focus

- A missing or invalid nested dictionary entry cannot be hidden by strict-schema conversion (Task 2).
- An older pinned profile retains its revision and valid execution route (Task 3).
- A partial or malformed streamed tool call never executes (Task 5).
- A crash after committing a call or result does not repeat an external operation (Task 6).
- Encrypted reasoning and provider metadata never appear in public events, logs, or cross-profile fallback (Tasks 4–6).

## File map

- `apps/api/src/openship/providers/openai.py`: existing Chat Completions translation and factory.
- `apps/api/src/openship/providers/openai_schemas.py`: shared faithful wire-schema selection and normalization.
- `apps/api/src/openship/providers/openai_responses.py`: Responses request/stream translation.
- `apps/api/src/openship/providers/contracts.py`, `runner.py`: pinned options, bounded continuation contracts and runner state.
- `apps/api/src/openship/runs/agent_graph.py`, `transcript.py`, `resume.py`: fenced continuation, recovery and replay.
- `apps/api/tests/providers/`, `tests/runs/`, `tests/live/`, `tests/full_stack/`: regression and qualification.
- `.env.example`, `docs/operations/`: non-secret profiles and rollout guidance.

## Task 1: Establish reproducible baseline

**Files:** Read staged sandbox diff and previous hook output; update this plan with evidence.

- [x] Record `git status --short` and inspect `git diff --cached -- apps/api/src/openship/sandboxes/python_tool.py`; do not change or unstage it.
- [x] Run `cd apps/api && uv run pytest -q -m 'not postgres and not provider_live and not full_stack and not jetstream'` with a sufficiently long timeout. Baseline: 1561 passed, 3 failed, 133 deselected; full output in the task ledger workspace.
- [x] Investigate each reproducible failure before including the staged change: `test_stack_selects_sandbox_profile_only_when_requested` (docker and podman) and `test_stack_defaults_to_docker_when_both_engines_exist` create temporary stacks without the files now required by `ensure_sandbox_files`, so preflight rejects the fixtures. The staged Python schema change is not involved.
- [x] Correct the three tests' temporary fixtures with synthetic regular files and verify the packaging lane (14 passed) and full baseline (1564 passed, 133 deselected, 97 warnings in 500.86s); preserve the preflight and staged sandbox change.

## Task 2: Preserve dynamic tool dictionaries

**Files:** Create `apps/api/src/openship/providers/openai_schemas.py`; modify `apps/api/src/openship/providers/openai.py`; test `apps/api/tests/providers/test_openai.py` and new `test_openai_schemas.py`.

**Interfaces:** `wire_schema(schema: dict) -> tuple[dict, bool]` returns the wire schema and strict flag; `normalize_arguments(schema: dict, arguments: dict, *, strict: bool) -> dict` preserves canonical semantics. Both adapters consume these functions.

- [ ] Add tests asserting Python `input_ids`, `parameters`, and `output_media` preserve nonempty maps with `strict=False`; fixed schemas remain strict; nested maps in arrays, unions, and local `$defs` are detected; broken references fail closed.
- [ ] Add tests asserting unknown top-level keys, invalid typed-map values, invalid explicit nulls, nonfinite JSON, excessive arguments, and malformed JSON cannot reach the gateway; valid default omission survives.
- [ ] Run `cd apps/api && uv run pytest -q tests/providers/test_openai_schemas.py tests/providers/test_openai.py`; verify new tests fail on current implementation.
- [ ] Implement the shared interfaces and use them in Chat Completions, retaining canonical maintained-tool admission validation.
- [ ] Re-run both files and existing sandbox contract tests; record passing counts. Commit only task files using a scanned message file.

## Task 3: Pin protocol configuration without breaking Luna

**Files:** Modify `apps/api/src/openship/providers/contracts.py`, `openai.py`, `registry.py` if needed; test `apps/api/tests/providers/test_contracts.py`, `test_openai.py`, `test_registry.py`.

**Interfaces:** `api_version` selects `chat-completions` or `responses`; Responses requires `output_limit_parameter='max_output_tokens'` and `reasoning_effort` in `none|low|medium|high|xhigh`. Factory `build_adapter(profile)` returns the pinned protocol adapter.

- [ ] Add regression tests for legacy pinned profile revision and snapshot round trip, invalid discriminator/options, Sol `none` refusal, and unchanged non-OpenAI profiles.
- [ ] Run the affected tests and confirm the new Responses options fail before the change.
- [ ] Extend validation and canonical hashing so unused nullable options do not alter legacy revisions; preserve existing Luna route. Until Task 5 supplies transport, Responses selection fails closed with an actionable unavailable error.
- [ ] Re-run affected tests and commit only this task's verified files.

## Task 4: Define private bounded continuation

**Files:** Modify `apps/api/src/openship/providers/contracts.py`, `runner.py`; test `apps/api/tests/providers/test_contracts.py`, `test_runner.py`.

**Interfaces:** Define `ResponsesContinuation` with version, protocol, profile revision, model, destination identity and ordered validated output items; optional `continuation` on internal `ChatMessage`, `ProviderTurnResult`, and an explicit provider event.

- [ ] Add tests for 512 KiB/128-item limits, invalid item types, private `repr`/public serialization, old messages lacking continuation, explicit runner handling, and reasoning/commentary fragments suppressing unsafe retry.
- [ ] Run targeted tests to verify failure; implement versioned allowlisted JSON contracts and runner propagation without exposing opaque data or changing existing event terminal handling.
- [ ] Verify context byte accounting includes continuation and cross-profile provenance fails before network; run targeted tests and commit.

## Task 5: Implement Responses streaming transport

**Verified progress:** An unregistered adapter has focused tests for text,
terminal single calls, stateless replay/provenance, commentary separation,
empty outputs, retry activity, terminal usage, SDK rate limits, and served
model mismatch. The latest focused Responses/contracts/runner run passed
48 tests. Event sequencing and limits, broader error handling, graph
persistence/recovery, and end-to-end qualification remain incomplete; the
factory still rejects Responses profiles. The latest full API suite is running.

**Files:** Create `apps/api/src/openship/providers/openai_responses.py`, `apps/api/tests/providers/test_openai_responses.py`; modify OpenAI factory and shared helpers.

**Interfaces:** `OpenAIResponsesAdapter.stream(request: ModelRequest, config, control)` yields existing events and the Task 4 continuation event; `count_input_tokens` fails closed without a qualified counter.

- [ ] Add deterministic SDK stream fixtures asserting request shapes, `store=False`, explicit `strict` flags, call_id/item_id distinction, one complete authorized call, usage, final-answer phase and saved ordered reasoning.
- [ ] Add tests for interleaved fragments, duplicate/unknown items, invalid/oversized arguments, model mismatch, refusal, incomplete/failed/empty responses, cancellation and dropped streams; assert no partial call is returned.
- [ ] Run the new test file and verify it fails; implement stream translation, close streams, safe error mapping, explicit Responses factory route and provenance check.
- [ ] Re-run `cd apps/api && uv run pytest -q tests/providers/test_openai_responses.py tests/providers/test_openai.py tests/providers/test_runner.py`; record output and commit.

## Task 6: Fence continuation and recover without redispatch

**Partial progress:** Focused graph tests cover saved continuation before tool
dispatch, final-answer reuse, recorded-result turn numbering, unresolved-call
gateway recovery, and wrong-provenance refusal. The combined focused provider
and graph run passed 77 tests. Real PostgreSQL gateway/idempotence, ambiguous
dispatch, complete envelope equivalence, and public-projection tests remain.

**Files:** Modify `apps/api/src/openship/runs/agent_graph.py`, `transcript.py`, `resume.py`, `budget.py` only as required; tests under `apps/api/tests/runs/` and relevant integration tests.

**Interfaces:** Persist `ChatMessage.continuation` in existing transcript JSON atomically with a validated assistant call; gateway logical call IDs remain `model:{turn}:call:0`.

- [ ] Add PostgreSQL-backed tests for crash after assistant save, crash after committed output, completed final answer, restored budget/turn ordinal, `last_step` versus `from_beginning`, and stale fencing. Assert saved results reused exactly once and uncertain dispatch refused.
- [ ] Add tests for exact ordered continuation replay, legacy transcript decoding, cross-profile fallback refusal, and no opaque data in public event/projection output.
- [ ] Run tests with an isolated `TEST_POSTGRES_URL` and verify failures; implement recovery and fenced persistence without modifying existing artifact volumes.
- [ ] Re-run tests and existing Chat Completions resume coverage; record results and commit.

## Task 7: Qualification, documentation, and final verification

**Files:** Modify `apps/api/tests/live/qualification.py`, add Responses live tests and disposable full-stack/browser tests; update `.env.example` and `docs/operations/`.

- [ ] Add opt-in fail-closed live qualification for Luna Chat Completions and Luna/Terra/Sol Responses: text, streamed tool/result/final, dynamic maps, actual reasoning/phase items, usage and stateless continuation. Run only with explicitly configured credentials and bounded budgets.
- [ ] Add real-model, real-sandbox disposable journey asserting download artifact ref → Python source → validated PNG → artifact digest → browser preview/download → worker restart/resume without duplicate execution. Preserve deterministic fixture coverage.
- [ ] Document explicit protocol/effort profiles, privacy, Terra documentation discrepancy, no transparent migration of pinned runs, and operator-authorized rollout.
- [ ] Run full API unit, PostgreSQL integration, full-stack/browser, frontend typecheck, lint and applicable live lanes. Record exact commands, counts, skips and blockers here; do not claim unrun lanes pass.
- [ ] Review staged changes and scan; commit only verified task files. Present results and request separate approval before any dev configuration or service restart.
