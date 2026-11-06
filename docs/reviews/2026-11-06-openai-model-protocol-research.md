---
type: research
summary: OpenAI protocol findings for Luna, Terra and Sol, with a proposed adapter repair roadmap.
date: 2026-11-06
status: research-complete-direction-approved
---

# OpenAI Luna, Terra and Sol: protocol research and proposed repair

## Scope and status

Research requested after the localhost dev worker received HTTP 400 for
`gpt-6.1-sol`. This report combines official OpenAI documentation, inspection
of the current adapter/contracts, and bounded live probes using harmless
synthetic input. The user approved the additive direction on 2026-11-06;
the [written specification](../superpowers/specs/2026-11-06-openai-responses-and-tool-schemas-design.md)
awaits review. This roadmap is **not an implemented fix**. No model profiles,
application code, sandbox files, database records, or artifact volumes were
changed during this research.

Research checklist:

- [x] Review official model and API documentation.
- [x] Check the installed SDK and existing adapter/transcript boundaries.
- [x] Compare Chat Completions tool requests for all three models.
- [x] Verify streamed Responses tool calls and stateless result continuation.
- [x] Reproduce the dynamic-map schema problem and probe a faithful wire schema.
- [x] Record findings, limitations, alternatives, and a proposed repair sequence.
- [x] Approve the additive direction and write the implementation specification.
- [ ] Review/approve the written specification, then create/review the executable
  plan before implementation.

## Sources

Official pages retrieved on 2026-11-06:

1. [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
2. [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra)
3. [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
4. [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)
5. [Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)
6. [Function calling](https://developers.openai.com/api/docs/guides/function-calling)

## Confirmed model/API distinctions

| Model | Documented reasoning efforts | Documented function-calling route | Live Chat Completions with tools and `none` |
| --- | --- | --- | --- |
| `gpt-6-luna` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | Responses; Chat Completions also works with `none` | Accepted; returned a function call |
| `gpt-6.1-terra` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | Model page says Responses required | Accepted; returned a function call |
| `gpt-6.1-sol` | `low`, `medium`, `high`, `xhigh`, `max` | Responses required | Rejected: unsupported reasoning effort |

All three model pages list both endpoints as supported. **Endpoint support
does not mean every feature works on that endpoint.**

Two discrepancies must remain explicit:

- Terra's live Chat Completions behavior is less restrictive than its model
  page. Do not break the currently accepted path, but prefer Responses for
  newly qualified Terra tool profiles rather than relying on this discrepancy.
- Sol's live error lists `low`, `medium`, `high`, and `xhigh`; its model page
  also lists `max`. This research tested `low` on Responses, not `max`.

Earlier Sol probes also established that a basic Chat Completions request
succeeds, while supplying tools with `low` or omitting effort returns:

> Function tools with reasoning_effort are not supported for gpt-6.1-sol in
> /v1/chat/completions. To use function tools, use /v1/responses or set
> reasoning_effort to 'none'.

The suggested `none` workaround is inapplicable to Sol: the API separately
rejects it. This confirms a protocol mismatch, not a sandbox/network failure
or an invalid model ID.

## Live Responses qualification probe

The worker's installed OpenAI Python SDK is **3.24.0**, with native Responses
support. No SDK installation or upgrade was needed for these probes.

For each model, the probe:

1. Used `store=False`, `reasoning={"effort":"low"}`, a 1,024-token output
   ceiling, SDK retries disabled, and a 45-second request timeout.
2. Requested one strict `diagnostic_echo(token: string)` function call with
   `parallel_tool_calls=False` and streamed the response.
3. Checked the terminal response, exactly one function call, and the expected
   synthetic arguments.
4. Replayed all returned output items in memory, appended a
   `function_call_output` linked using the returned **`call_id`**, and requested
   a final response with tools disabled. No `previous_response_id` was used.

| Model | Streamed function call | Stateless tool-result continuation |
| --- | --- | --- |
| Luna | Completed; expected arguments | Completed; exact echo verified |
| Terra | Completed; expected arguments | Completed; exact echo verified |
| Sol | Completed; expected arguments | Completed; exact echo verified |

Observed events included `response.output_item.added`,
`response.function_call_arguments.delta`,
`response.function_call_arguments.done`, `response.output_item.done`, and
`response.completed`. Final assistant messages carried `phase=final_answer`.

These simple forced calls returned only a function-call output item, with no
reasoning item. Therefore they prove the basic round trip, **not** preservation
of emitted encrypted reasoning or intermediate commentary. Those cases remain
mandatory implementation/qualification coverage.

No real download, Python execution, or sandbox action occurred. A synthetic
echo result was supplied locally. Credentials, cookies, raw provider state,
and private conversation data were not printed or saved in this report.

## API schema changes the adapter must handle

| Chat Completions | Responses |
| --- | --- |
| `messages` | `input` typed items |
| Nested `tools[].function` | Flat function tools: `type`, `name`, `description`, `parameters`, `strict` |
| `max_completion_tokens` | `max_output_tokens` |
| `reasoning_effort` | `reasoning: {effort: ...}` |
| Assistant message `tool_calls` | Separate `function_call` output items |
| Tool message with `tool_call_id` | `function_call_output` with `call_id` |
| `choices[].delta` and finish reason | Typed stream events and terminal response status |
| `prompt_tokens` / `completion_tokens` | `input_tokens` / `output_tokens`, with detailed cached/reasoning counts |

Both protocols support disabling provider-side response storage; the new
Responses path should explicitly set `store=False`. Replay must preserve
ordered output items, including message `phase` and any opaque encrypted
reasoning returned. Do not expose opaque state as chat text or log it. OpenAI
documents encrypted reasoning for stateless continuation; an explicit
`include=["reasoning.encrypted_content"]` remains accepted and was used here.

`max_output_tokens` includes reasoning tokens, so a response can become
`incomplete` before visible text exists. Do not treat this as an empty success
or raise token budgets silently. Distinguish output-limit, refusal, failed
response, and interrupted transport outcomes.

## Separate confirmed defect: dynamic dictionary schemas

`providers/openai.py::_strict_schema()` changes every object to
`additionalProperties=False`. Python's `input_ids`, `output_media`, and
`parameters` are dynamic maps, not fixed-property records. The conversion
currently changes them to closed objects with **zero allowed properties**.

The local reproduction found:

- Canonical `input_ids` permits UUID-valued keys; wire schema permits none.
- Canonical `output_media` permits string-valued keys; wire schema permits none.
- Canonical `parameters` permits arbitrary JSON-valued keys; wire schema permits
  none.
- `_normalize_arguments()` rejects the canonical valid mapping
  `{"output_media":{"chart":"image/png"}}` with
  `tool arguments contain undeclared properties`.

This explains why adding output-path descriptions alone is insufficient:
the wire schema still prevents a model from declaring a chart alias. The same
conversion problem exists independently of the model/protocol selection.

OpenAI strict function schemas require closed objects at every object level.
For a minimal faithful repair, retain strict mode for representable fixed
schemas, but explicitly send the canonical schema with `strict=False` for
tools containing dynamic maps. Keep canonical server-side validation before
admission/dispatch; non-strict generation must not relax tool authority,
file/media limits, UUID checks, or rejection of unknown top-level fields.

A second live Responses probe used the unmodified canonical Python schema
with explicit `strict=False`. All three models returned arguments accepted
by `PythonArguments`, preserving both `output_media={"chart":"image/png"}`
and `parameters={"label":"probe"}`. The generated source was **not executed**.
This validates the proposed wire strategy, not its integration into OpenShip.

## Existing code boundaries and gaps

- `providers/openai.py`: always uses Chat Completions; API selection is not
  dispatched from the pinned `api_version` today.
- `providers/contracts.py::AdapterOptions`: supports only Chat Completions
  output-limit names and only `none` for the tool-call effort setting.
- `ChatMessage`, `ProviderEvent`, and `ProviderTurnResult`: have no place for
  ordered protocol continuation items or assistant message phases.
- `providers/runner.py::_drain()`: assumes any event outside its existing
  cases is terminal; adding a continuation event requires explicit handling.
- `runs/agent_graph.py`: reconstructs assistant messages from text/tool calls
  only, then saves them before tool dispatch. Extend this durable boundary,
  not an adapter-local memory cache.
- `runs/transcript.py`: replay equivalence currently has special handling for
  renamed tool-call IDs. New continuation state must not bypass those checks
  or get silently lost on resume.
- `tests/live/qualification.py`: currently requires Chat Completions, so it
  needs explicit protocol-aware qualification rather than simply broadening
  the accepted model names.

## Alternatives and recommendation

1. **Recommended: additive Responses support with explicit profile routing.**
   Keep the existing `openai` adapter name and use its already-pinned
   `api_version` to select `chat-completions` or `responses`, with a separate
   Responses implementation module. Retain Luna's working path and qualify
   new Sol/Terra Responses profiles. Repair dynamic-map handling for both
   paths.
2. **Switch every OpenAI profile to Responses immediately.** Simpler eventual
   protocol surface, but unnecessarily changes Luna's qualified behavior and
   all in-flight/pinned runs at once. Defer until separately qualified.
3. **Only change reasoning options.** Cannot fix Sol tool calls; ruled out by
   documentation and live requests. Disabling tools is not an acceptable fix
   for the Python journey.

## Proposed implementation sequence (written-spec review pending)

Each item starts with regression tests; none is marked implemented here.

1. **Establish the baseline and isolate existing work.** Investigate the three
   failures from the earlier full API hook. Preserve the staged Python schema
   description change and do not hide failures or bundle unexplained fixes.
2. **Repair dictionary schemas first.** Add tests for nested typed/untyped
   maps, `$defs`/references, optional/null defaults, fixed records, and invalid
   keys/values. Introduce a faithful explicit strict/non-strict wire strategy
   and canonical-aware argument normalization for both protocols. This also
   repairs Luna's inability to declare Python output aliases.
3. **Define pinned protocol options.** Validate the two OpenAI `api_version`
   values and their permitted options. Add Responses `max_output_tokens` and
   explicit reasoning effort; retain legacy Chat Completions settings and
   digest/reload compatibility. Reject invalid combinations before sending a
   request. Do not infer protocols from model names or silently retry a 400
   on another endpoint.
4. **Define durable continuation before adding transport.** Specify versioned,
   bounded, allowlisted continuation data, provenance/model compatibility,
   output ordering, and phase preservation. Carry it through provider events,
   turn results, assistant transcript persistence, and resume. Store complete
   state under the same fence before any tool dispatch. Account for it in
   context-byte limits; never send opaque state to unrelated fallback models
   or the UI. Maintain compatibility with old transcripts that lack it.
5. **Implement the Responses stream adapter.** Map typed request/output items,
   correlate function `call_id` separately from item IDs, retain the one-call
   policy, enforce bounded arguments and allowed-tool validation, preserve
   usage/model identity, and handle cancellation, refusal, incomplete/failed
   responses, duplicate/malformed events, and dropped streams. Use
   `store=False`; do not depend on provider-stored conversation state. Keep
   SDK retries disabled and existing run budgets authoritative. Token counting
   remains unqualified unless separately implemented/tested.
6. **Qualify through the actual application.** Add deterministic stream/runner/
   transcript tests and opt-in live lanes that fail when explicitly enabled
   prerequisites are absent. For all three models verify text, tool/result/
   final-answer, nonempty dynamic maps, reasoning/phase continuation, tool
   disablement, usage, cancellation, and output-limit handling. Run a real
   download-to-Python-to-PNG journey with preview, download, durable artifact
   digest, worker restart, and resume with no duplicate tool execution. Use
   disposable test storage/database, not the user's artifact volume.
7. **Document and roll out explicitly.** Add non-secret example profiles and
   safe actionable protocol diagnostics without logging arbitrary provider
   bodies. Propose new Sol/Terra Responses profiles and optionally a separate
   Luna Responses profile; retain Luna Chat Completions initially. Change dev
   `.env` and recreate API/worker only with approval. Existing pinned runs
   keep their original protocol; new profiles affect newly submitted runs.

### Acceptance criteria

- Sol streams a real application tool call/result/final-answer exchange using
  Responses; Luna Chat Completions does not regress.
- Terra's documented/live discrepancy is documented rather than encoded as
  an assumption that all endpoints behave identically.
- A Python call can carry nonempty input/output/parameter maps, publish a
  validated PNG to durable storage, and display it in chat.
- Reasoning/phase state survives worker restart and resume without depending
  on an in-memory SDK client or provider-side response storage.
- Invalid/partial calls cannot execute, committed tool operations are not
  repeated, and explicit live lanes do not silently skip prerequisites.
- Relevant full unit, integration, browser, lint, and typecheck lanes pass;
  existing failing tests are explained/resolved rather than bypassed.

## Limitations

These probes qualify small direct SDK exchanges, not the proposed adapter or
the real-model sandbox journey. They do not qualify `max` reasoning, complex
encrypted reasoning histories, long-context behavior, token counting, model
fallback, or pricing enforcement. No claim is made that OpenShip already
supports Responses. Production changes must follow approved written design
and implementation planning.
