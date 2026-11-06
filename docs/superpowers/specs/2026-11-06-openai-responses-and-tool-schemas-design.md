---
type: design
summary: Add pinned OpenAI Responses support and preserve dynamic tool maps without regressing Luna.
date: 2026-11-06
status: draft-awaiting-review
---

# OpenAI Responses and faithful tool schemas

## 1. Intent and approval boundary

Enable `gpt-6.1-sol` and `gpt-6.1-terra` to participate in OpenShip's bounded,
durable tool loop, including Python execution and retained chart artifacts.
Preserve the qualified `gpt-6-luna` Chat Completions path and existing
authorization, execution, budget, cancellation, and artifact guarantees.

The user approved the additive direction on 2026-11-06 after reviewing the
[research and live-probe findings](../../reviews/2026-11-06-openai-model-protocol-research.md).
That approves writing this specification, not implementation. This written
specification requires review before the executable implementation plan.

### Scope

- Explicit OpenAI Chat Completions/Responses protocol selection in pinned
  profiles, not inference from model names.
- Faithful dynamic-map function schemas and canonical argument validation.
- Streamed Responses translation into internal contracts.
- Durable stateless continuation, including phases and encrypted reasoning
  when returned, across tool calls and worker restart/resume.
- Protocol-aware tests, live qualification, example profiles, and safe errors.

### Non-goals

No OpenAI-hosted tools, Code Interpreter, provider-managed Conversations,
background responses, parallel tool execution, automatic endpoint fallback,
reasoning summaries, multi-agent mode, model-family state sharing, or SDK-wide
kwargs passthrough. No change to sandbox dependency installation, output
directories, artifact storage volumes, or application container ownership.
No automatic increase of token/cost/deadline budgets or unqualified token
counter. Do not edit local `.env`, restart services, push, or deploy as part
of implementing the adapter without separate authorization.

## 2. Evidence driving the design

Official OpenAI documentation states Sol tool calling requires Responses;
live requests confirmed that neither `none` nor supported reasoning settings
make Sol's Chat Completions tool calls work. Streamed Responses echo-tool
calls and stateless result continuation succeeded for Luna, Terra, and Sol.
Terra's model page requires Responses even though a live Chat Completions
probe with `none` succeeded. Preserve that distinction in documentation.

The installed OpenAI SDK already supports Responses. An upgrade is not a
prerequisite unless implementation identifies a specific missing feature.

Separately, the current strict-schema conversion closes arbitrary dictionary
fields without declaring their keys, and argument normalization rejects
those keys. This prevents nonempty Python input/output/parameter maps.
Direct Responses probes with canonical schemas and explicit `strict=False`
preserved those maps for all three models. Output-path descriptions alone
do not repair this defect.

These probes did not qualify emitted reasoning histories, cancellation,
full application integration, or real-model sandbox execution. Those remain
acceptance work, not assumed successes.

## 3. Architecture and boundaries

Keep `adapter="openai"`. Its factory dispatches by the pinned `api_version`
to the existing Chat Completions implementation or a new
`providers/openai_responses.py` implementation. Share only protocol-independent
credential resolution, safe SDK error classification, and faithful schema
utilities where this removes actual duplication. Do not rewrite the existing
adapter into a generic protocol framework.

The runner continues to own attempts, deadlines, cancellation, and fallback;
the graph owns authorized tool sequencing; the gateway owns tool admission
and committed publication; the transcript owns durable model context.
Adapters translate transport data and never execute tools themselves.

SDK objects stay inside provider modules. Internal continuation data is
versioned, serializable, bounded, and private; public DTOs remain separate.

## 4. Profile and pinning contract

For OpenAI profiles:

| Field | Chat Completions | Responses |
| --- | --- | --- |
| `api_version` | `chat-completions` | `responses` |
| `adapter_options.adapter` | `openai` | `openai` |
| `output_limit_parameter` | Existing `max_tokens` or `max_completion_tokens` | `max_output_tokens` |
| `tool_call_reasoning_effort` | Existing optional `none` | Forbidden |
| New `reasoning_effort` | Forbidden | Required; initially `none`, `low`, `medium`, `high`, or `xhigh` |

New Sol/Terra examples use Responses with `reasoning_effort="low"`. A model's
documented vocabulary is not a universal capability guarantee: Sol does not
accept `none`. Reject known incompatible combinations locally with an
actionable configuration error. Keep routing explicit; model-specific
compatibility checks must never select another endpoint or silently change
effort. `max` and `minimal` are outside this initial qualification.

Validate protocol/options for both operator profiles and decoded pinned
snapshots. Validate that the nested adapter discriminator agrees with the
profile adapter. Do not affect Anthropic, Google, or OpenAI-compatible
protocol claims as a side effect.

New options must not change legacy Chat Completions canonical hashes merely
because an unused nullable field was added. Define omission of the new option
for legacy canonicalization and test old snapshots and revision digests.
Responses profiles include their explicit protocol and effort in their
revision. A persisted run keeps its pinned options even if `.env` changes;
normal current-eligibility/credential checks still apply.

## 5. Faithful function schemas and arguments

Treat the capability's canonical input schema as the authority. Never change
the semantics of dictionaries to make a schema acceptable to OpenAI.

- Use explicit `strict=True` for supported fixed-property schemas. Preserve
  the existing wire-only optional/nullable representation and restore only
  the corresponding canonical defaults on return.
- If an object permits arbitrary or typed additional keys anywhere in its
  reachable schema, send a canonical schema copy with explicit `strict=False`.
  This decision is deterministic and tested, not a provider-error retry.
- Follow local `$defs`/references, nested objects, arrays, and union branches
  when classifying schemas. Handle repeated references without recursion
  loops; unsupported/unresolvable schemas fail before model dispatch rather
  than being silently closed or weakened.
- Non-strict arguments are validated against canonical semantics. Restore
  omitted defaults through the canonical input model; do not convert an
  explicit invalid null into an omitted field on the non-strict path.
- In normalization, reject unknown keys only where the canonical object
  forbids them; preserve permitted map keys and validate typed map values.
  Preserve JSON finiteness and existing argument-byte bounds.
- Canonical maintained-tool validation, permission checks, file/media limits,
  artifact authority, and unknown top-level rejection still run before any
  execution. Non-strict model generation is not non-strict admission.

Apply this repair to Chat Completions and Responses. Keep tool names and
canonical capability interfaces unchanged. Include the existing output-path
descriptions only after their outstanding test failures have been understood.

## 6. Responses request and event translation

Requests use `client.responses.create`, `input` items, flat function tools,
`max_output_tokens`, `reasoning={"effort": ...}`, `parallel_tool_calls=False`,
`store=False`, explicit encrypted-reasoning inclusion, and no SDK retries.
Do not send Chat Completions-only arguments. Use the pinned endpoint and
credential binding; preserve redirect refusal and supervised deadlines.

Serialize ordinary system/user messages as compatible input message items.
Responses assistant turns with saved continuation use those ordered output
items exactly once, not an additional reconstruction of their visible text
or tool calls. A canonical tool result becomes `function_call_output` linked
by its provider **`call_id`**, not its output item ID.

Streaming rules:

- Track items by output index/item ID and correlate function IDs separately.
  Assemble bounded argument fragments and require complete, consistent final
  items plus a valid terminal response before admitting a tool call.
- Translate final-answer text, normalized tool calls, terminal usage, and
  model identity into existing contracts. Extend contracts explicitly for
  continuation and phase information; update every consumer, including
  `runner._drain`, rather than relying on its catch-all terminal branch.
- Preserve assistant message phases. Commentary/preambles are not final
  answers and must not satisfy `ProviderTurnResult.completed`. Retain their
  original ordered items for replay. No new frontend commentary feature is
  required; final response text excludes commentary.
- Validate the served model against the pinned model and qualified aliases.
  Unknown executable item types, multiple function calls, calls outside the
  allowlist, and calls when tool choice is `none` fail closed.
- A `response.completed` with one valid call maps to `tool_calls`; one with
  a nonempty final answer and no call maps to `stop`. A commentary-only or
  empty completion is not success. Refusal and incomplete-output limits map
  to their existing distinct outcomes. Failed responses/transport errors
  are errors, not successful empty results.
- Preserve provider-reported input/output/cached/reasoning token counts.
  Reasoning is included in output-token budgets; do not add it a second time.
  Unknown usage remains unknown, never zero.

Generated commentary, reasoning items, and function fragments must mark an
attempt as having produced output for retry safety without exposing private
state as text. An interrupted generated turn cannot silently trigger a fresh
request under the existing pre-output retry policy. Always close streams and
clients according to existing lifecycle ownership.

## 7. Durable stateless continuation

### Envelope

Add an optional internal continuation envelope to complete assistant turns,
turn results, and transcript messages. Its version-1 shape records:

- Protocol (`openai.responses`) and envelope version.
- Pinned producing profile revision, model, and non-secret destination
  identity. No API keys, request headers, cookies, or credential values.
- The ordered, JSON-serializable output items needed for stateless replay.

Initially allow function-call, assistant-message, and reasoning item types
only. Use explicit replay-field validation for IDs, arguments, message role,
content parts, phase, and opaque encrypted reasoning. Preserve allowed
semantic fields and ordering without lossy text flattening. Do not persist
raw SDK instances or arbitrary response/header dictionaries. Do not request
or store raw reasoning text or reasoning summaries; encrypted content stays
opaque. Unsupported semantic item/content types fail before dispatch.

Bound the encoded envelope to 512 KiB and 128 output items per turn, in
addition to existing tool argument limits. Entire replay history, schemas,
and continuation data count against the run's existing `max_context_bytes`.
Exceeding either limit fails with a safe bounded-context error rather than
silently dropping reasoning or raising the configured ceiling.

### Persistence and replay

The runner emits continuation only for a fully validated completed turn.
The graph commits the assistant message, normalized call, and continuation
atomically under the current run fence **before** gateway execution. A
failure to save state prevents tool dispatch. Partial stream state is never
saved as a complete transcript entry or used to authorize a tool.

Use existing `RunTranscriptRecord.message_json` for the optional envelope;
no database column migration is required. Missing envelopes decode as legacy
turns. Old workers do not understand the new field, so drain/restart relevant
workers on rollout and do not run old workers against Responses turns.
Rollback must disable new Responses admission and preserve their records;
do not delete continuation or reinterpret them as Chat Completions.

Only replay an envelope to the same pinned producing destination/model/profile
revision. Do not forward opaque state to another model family, endpoint,
protocol, or fallback profile. For this initial scope, reject a cross-profile
continuation attempt before network access; do not strip state silently.
This conservatively limits fallback after a Responses turn; legacy fallback
and first-turn fallback without private state keep existing policy.

### Worker restart and explicit resume

- Restore committed Responses turns from the database, not an adapter cache
  or `previous_response_id`. Reconstruct input from ordered saved items and
  canonical tool results.
- If the last saved assistant call has no saved tool result, resolve that
  authorized logical call through the gateway's durable operation state
  before asking the model again. Reuse a committed result; obey existing
  refusal for uncertain dispatch. Never rerun the model to recover a lost
  provider call ID or manufacture a new logical call.
- Continue model-turn numbering after recorded turns so new decisions do
  not collide with earlier ordinals. Restore already-spent budget records.
- If the copied transcript already contains a completed final answer, rebuild
  that result without another model request. A request for new work is a new
  submitted user turn, not implicit regeneration during last-step resume.
- `last_step` resume copies exact saved continuation and committed results.
  Equivalence includes continuation provenance and ordered items; renamed
  provider call IDs alone cannot authorize altered state. Prefer replay of
  the saved decision over regenerating an allegedly equivalent one.
- `from_beginning` starts without prior response state/results, retaining its
  existing explicit-new-execution semantics and pinned profile.
- Existing Chat Completions transcript shapes and resume rules remain
  supported and covered by regression tests.

## 8. Privacy and diagnostic behavior

Continuation is private execution material, not an artifact, a user-visible
message, or a new source of instructions. Persist it only in the internal
transcript; keep it out of public projections, SSE/event payloads, attempt
summaries, browser traces, and ordinary logs. Internal model representations
must not leak it through `repr` or broad serialization. Test each public
boundary explicitly.

Classify known protocol/option errors with fixed actionable copy naming the
profile/protocol, not arbitrary upstream messages. A request ID, HTTP status,
and allowlisted error code/parameter can aid diagnostics; do not print raw
provider bodies, prompts, SDK kwargs, or secret values. HTTP 400 is not a
transient retry and should not instruct users to rephrase an otherwise valid
request when a known profile mismatch is the cause.

## 9. Verification and acceptance

Before implementation, identify the three full-suite failures reported by
the earlier schema-guidance commit hook. Record a baseline; do not bypass
hooks or report targeted tests as proof the whole suite passes.

Required test layers:

1. **Schema/unit:** nonempty typed/untyped maps, nested/reference/union/array
   shapes, defaults versus explicit null, invalid values/keys, duplicate or
   malformed JSON, legacy strict fixed records, and bounds.
2. **Profiles:** explicit protocol dispatch, invalid combinations before
   network access, old snapshot/digest round trips, unrelated adapters
   unchanged, and current eligibility with immutable pinned options.
3. **Adapter/runner:** real-shaped deterministic SSE fixtures for interleaved
   items, phases, encrypted reasoning, fragments, usage, terminal failure,
   cancellation, output exhaustion, model mismatch, and retry/fallback rules.
   Only complete valid calls may reach a gateway stub.
4. **Persistence:** fenced atomic continuation save, stale-worker refusal,
   restart after assistant save and after tool publication, transcript/result
   reconstruction, privacy projections, bounds, legacy decoding, and both
   resume modes. Verify committed tools execute once despite restart/resume.
5. **Live models:** explicit opt-in lanes for Luna Chat Completions and all
   three Responses profiles. Verify text and function/result/final-answer,
   nonempty maps, usage, and stateless continuation. Include a case returning
   actual reasoning/phase items; a function-only echo is not sufficient proof
   of reasoning preservation. Explicitly enabled missing prerequisites fail,
   not skip. Use synthetic prompts and bounded budgets.
6. **Real application journey:** download a fixture CSV/JSON through the real
   maintained tool, let a real selected model use its artifact ref in Python,
   declare/write a PNG, and verify collected bytes/digest, ready artifact row,
   chat preview/download, worker restart, and last-step reuse without another
   download/Python dispatch. Use disposable database/artifact storage, never
   rewrite or delete the user's dev artifact volume. Retain deterministic
   full-stack/browser lanes as regression coverage.

Acceptance requires the relevant full API, integration, browser, lint, and
typecheck lanes to pass, not only direct SDK probes. Evidence must distinguish
mocked adapters, real model calls, real sandbox transport, and browser checks.
No success claim for an unavailable/unrun lane.

## 10. Delivery and rollout

Implement through a reviewed TDD plan with independently verifiable changes:
baseline, faithful schemas, protocol/pinning, durable state, Responses
transport, qualification, documentation. Preserve existing staged work;
review it separately before including it in a code commit.

Document additive Sol/Terra Responses examples and optional Luna Responses
alongside the unchanged Luna Chat Completions example. Start with `low`
reasoning and existing operator budgets; do not treat probes as pricing or
large-context qualification. Document Terra's live/docs discrepancy.

With explicit rollout approval, update dev profiles and recreate API/worker
so both load the same configuration. Existing pinned runs do not switch
protocol. Use a new run to test a new profile; a newly submitted request is
not a silent repair of the previous rejected run.

## 11. Design-stage progress

- [x] Research official schemas and inspect current implementation.
- [x] Verify small live tool/result round trips for all three models.
- [x] Obtain approval for the additive direction.
- [x] Write the specification and review its scope and consistency.
- [ ] User reviews/approves this written specification.
- [ ] Write/review implementation plan and select execution method.
- [ ] Implement, qualify, and separately approve rollout.
