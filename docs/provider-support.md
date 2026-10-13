---
type: reference
summary: Which provider tuples OpenShip can claim to serve, at which state, and the evidence each state rests on.
status: proposed
date: 2026-10-13
---

# Provider Support

This is the only place OpenShip states what it can serve. A claim here is a
claim about a **tuple** — one adapter, one endpoint implementation, one
requested model — never about a family or a brand. Anything the matrix does
not state, the product does not assume.

## How to read the matrix

Three states, in order. A tuple moves forward only on evidence, and the
evidence is named next to it.

| State | What it means | What it does **not** mean |
| --- | --- | --- |
| `fixture-tested` | The shared conformance suite passes for this adapter against a synthetic transport: text, stream, error, cancel, and usage cases all behave. | Nothing about a real deployment. No model was observed, no ceiling was measured, no price was incurred. |
| `live-qualified` | One real turn ran against this tuple through `tests/live/test_provider_smoke.py` and completed at the declared destination. | That a person accepts the result. Qualification is necessary, not sufficient. |
| `accepted` | A person reviewed that live result and chose **accept**. Only then does the tuple become one the browser offers as a default. | — |

**Credentials absent keeps the later states pending.** No credential binding is
configured in this repository, so nothing here is `live-qualified` or
`accepted`. That is a fact about the checkout, not a failure: the live test
skips rather than pretending, and the matrix says so.

## The matrix

| Tuple | Adapter | Endpoint implementation / API version | Requested model | Observed model | SDK | Effective capabilities | Mandatory test results | Live qualification | User acceptance | State |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `openai` · Chat Completions | `openai` | `chat-completions` | *not stated* | *not observed* | `openai` 3.24.0 (the version this environment resolves) | streaming, usage reporting; no token counting, no tool calls, no structured output, no attachments | 11 conformance cases + 12 adapter cases green (`tests/providers/test_conformance.py`, `tests/providers/test_openai.py`) | pending — no credential configured | pending | `fixture-tested` |
| `anthropic` · Messages | `anthropic` | `messages` | *not stated* | *not observed* | `anthropic` 1.13.0 (the version this environment resolves) | streaming, usage reporting (per-field counts; this API reports no total), token counting through the provider's own counter; no tool calls, no structured output, no attachments | 10 of the 11 conformance cases (the eleventh is a shape only Chat Completions produces) + 20 adapter cases green (`tests/providers/test_conformance.py`, `tests/providers/test_anthropic.py`) | pending — no credential configured | pending | `fixture-tested` |
| `google` · Gemini Developer API | `google` | `generate-content` streaming over `v1` | *not stated* | *not observed* | `google-genai` 2.30.1 (the version this environment resolves) | streaming, usage reporting (per-field counts, including this API's own total, a cached count, and a thought count), token counting through the provider's own counter; no tool calls — a predicted call is never executed here — no structured output, no attachments | 10 of the 11 conformance cases (the eleventh is a shape only Chat Completions produces) + 24 adapter cases green (`tests/providers/test_conformance.py`, `tests/providers/test_google.py`) | pending — no credential configured | pending | `fixture-tested` |
| `openai_compatible` | `openai_compatible` | pending | pending | pending | pending | pending | pending — unit 3D | pending | pending | not yet testable |

The requested/observed model columns stay empty on a `fixture-tested` tuple
on purpose. The conformance suite runs against a fixture model at a fixture
endpoint, so no real model name belongs in that row. **Model context and
output limits, tokenizer and pricing schedules, and accepted aliases come only
from a tuple's own evidence** — a ceiling an operator declared is a
declaration, not a qualification, and it stays marked as such until a live
turn confirms it.

## Declaring a profile (redacted)

Profile configuration is non-secret. `MODEL_PROFILES_JSON` is a JSON array;
every field the adapter needs is explicit, and arbitrary SDK kwargs are not
accepted. This example names no real deployment and no real key.

```json
[
  {
    "id": "primary",
    "label": "Primary model",
    "adapter": "openai",
    "model": "<the exact model this deployment serves>",
    "endpoint": "https://api.<destination>.test/v1",
    "api_version": "chat-completions",
    "credential_ref": "PRIMARY_KEY",
    "capabilities": {
      "streaming": true,
      "usage_reporting": true,
      "context_tokens": 32768,
      "max_output_tokens": 4096
    },
    "allowed_fallback_profile_ids": ["backup"],
    "qualified_model_aliases": [],
    "adapter_options": {
      "adapter": "openai",
      "output_limit_parameter": "max_tokens"
    }
  }
]
```

`adapter_options` is discriminated by adapter, and the contract refuses a
profile that omits or invents one: `openai` needs `output_limit_parameter`
(because some models reject `max_tokens`), `anthropic` needs
`server_side_fallback`, `google` needs `native_api_version` and
`automatic_function_calling`, and `openai_compatible` needs
`endpoint_implementation`, `authentication`, `stream_usage_supported` and
`output_limit_parameter`.

## Environment mapping

| Variable | Read where | Secret? |
| --- | --- | --- |
| `MODEL_PROFILES_JSON` | at startup, once per process, when the registry is built | no |
| `MODEL_DEFAULT_PROFILE_ID` | the profile that answers when the browser chooses nothing | no |
| `MODEL_MAX_TOKENS` | the output ceiling a turn asks for, when a profile declares none | no |
| `OPENAI_MODEL`, `OPENAI_BASE_URL` | only for an installation that declares no profile (the legacy path) | no |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, and any name a profile's `credential_ref` gives | only where a client is constructed | **yes** |
| `TEST_POSTGRES_URL` | the durable-pipeline test lane | throwaway |
| `OPENSHIP_LIVE_PROFILE_ID` | the opt-in live qualification test | n/a |

A credential is named by reference and its value is read in exactly one place.
That is what lets a catalog, an event, a checkpoint, a log line, and a
browser all speak about a profile without ever carrying what it costs to
serve it.

## Keyless local compatibility

A profile may declare no credential at all — the shape is
`openai_compatible` with `authentication: "none"` pointed at a private host
(`localhost`, a loopback, a bare container name, or a private IP). Plain `http`
is accepted only for those hosts; for a public host it is refused, because an
insecure public destination is a mistake an operator cannot see in a catalog.

The contract and the endpoint rules for this are in place now; the adapter that
serves it lands in unit 3D. Until then a profile naming `openai_compatible` is
a configuration error, not a guess.

## Default and fallback behaviour

* **Default.** The default profile is named by configuration, never invented at
  runtime. An installation that declares no profile keeps working through the
  legacy settings as one synthetic profile; the moment profiles are declared,
  a default outside them is refused at startup.
* **Fallback.** A fallback is only offered where the profile that answers
  declares it in `allowed_fallback_profile_ids`. The browser shows the opt-in
  only then, the store refuses a fallback that profile does not allow, and the
  runner admits a fallback on its own merits before asking it. A fallback is
  never taken silently.
* **Unavailable profiles are shown, not hidden.** A profile this installation
  cannot serve appears in the catalog with a reason (`missing_credentials`,
  `adapter_unavailable`, `configuration_error`) and no button. The service
  boots with the reason rather than failing, so a missing key is something a
  person can act on.

## Limits

One shared budget spans the initial call, every retry, and any fallback:
`max_attempts` (default 3), `run_timeout_seconds` (120),
`request_timeout_seconds` (45), `max_context_bytes` (64 KiB), and optional
`max_total_tokens` / `max_cost_usd` ceilings. No SDK retries on its own —
`max_retries=0` on the OpenAI and Anthropic clients, one attempt in the
Gemini client's transport options — and the Gemini adapter also disables the
two SDK behaviours that would make one attempt into several requests
(`automatic_function_calling`, `automatic_continuation`). So the number of
generation requests a turn makes is the number the tests count. A provider
that reports usage only when asked is asked.

## Credential rotation and revocation

Because a credential is read where a client is built, rotating a value needs
no code change and no run rewrite: a run already executing keeps the snapshot
it pinned, and the next turn resolves whatever the binding now holds.
Revoking a value leaves the profile in the catalog as unavailable with
`missing_credentials` — the browser explains it, and an already-pinned run is
not re-pinned to something else.

## Migration and rollback constraints

Profile ids and their pinned snapshots are UUID-keyed and the id/foreign-key
columns are native `uuid` on PostgreSQL (see the alembic chain). Rolling back
a revision that introduced profiles drops the pinned snapshots of runs that
were mid-flight; the durable record of those runs stays, and their provider
claim becomes "not recorded" rather than a guess. Rollback is therefore safe
for history and lossy for in-flight runs — which is why a running worker is
reconciled rather than resumed across a schema change.

## Where provider data goes

A destination is an origin and a path, never a place to hide a secret: an
endpoint may not carry userinfo, a query string, or a fragment, and `http` is
refused for a public host. What a browser may see is a **destination label**
(host and path), never the endpoint's own shape. Provider content is stored in
the record (events, messages, attempts) and never in a checkpoint's
serialization; the checkpoint test reads the saver's own tables to prove no
credential value reaches them.

## Cancellation is not a stopped meter

Closing a stream is not the same as stopping a generation. When OpenShip
cancels, it stops consuming and closes the connection; a provider may keep
generating after that point and may bill for work that was never read. The
bounded behaviour this repo guarantees is about **local** I/O — the stream
closes within two seconds of an observed cancellation, and no late assistant
message or further retry is published. Nothing here claims the remote meter
stopped.

## Qualification record

Nothing is `live-qualified` in this checkout. The live test is opt-in and
contacts a real provider:

```bash
cd apps/api && OPENSHIP_LIVE_PROFILE_ID=<an installed profile id> \
  uv run pytest tests/live/test_provider_smoke.py -m provider_live -q
```

Run without a credential configured, it fails with the reason the
installation cannot serve that profile rather than skipping — which is the
honest state, and the reason names the binding, never a value.
