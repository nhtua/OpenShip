---
type: reference
summary: How provider behavior is tested at each state - the shared conformance suite, the durable lanes, and the opt-in live qualification - and what the current record is.
status: proposed
date: 2026-10-13
---

# Provider Conformance Testing

Provider behavior is qualified in three places, and each one proves a
different thing. Reading them in order is how a tuple moves from
`fixture-tested` to `live-qualified` to `accepted` in
[provider-support.md](./provider-support.md).

## The shared suite

Every adapter runs the **same** mandatory cases: text, stream, error,
cancel, and usage. That sameness is the point — an adapter that passes its
own cases but not the shared ones has not been qualified, it has been
described.

| Suite | What it proves |
| --- | --- |
| `tests/providers/test_contracts.py` | The provider-neutral vocabulary the adapters, runner, worker, and browser share: frozen, serializable, secret-free. |
| `tests/providers/test_configuration.py` | Operator profile declaration, endpoint rules, registry lookup, and per-run pinning — including that an endpoint cannot smuggle a secret and a fallback cannot be invented. |
| `tests/providers/test_conformance.py` | The mandatory cases every adapter must pass, run against a synthetic transport. |
| `tests/providers/test_openai.py` | The OpenAI adapter's own obligations: no SDK retries, the output parameter that belongs to the profile, usage asked for when the provider reports it only when asked. |
| `tests/providers/test_runner.py` | One shared budget across initial call, retries, and fallback: bounded attempts, deadline enforcement, reservation before request, and cancellation that never starts another request. |

The transport in `tests/providers/conftest.py` stands in for the SDK at its
outermost edge: it records the request the adapter actually made and whether
the adapter closed the stream it opened. Everything else in a case — the
adapter, the contracts, the control — is the real component under test. That
is what lets the whole suite run with no vendor API key and no network.

```bash
cd apps/api && uv run pytest tests/providers -q
```

## The durable lanes

The promises that only hold where real locks, real JSON serialization, and
real checkpoint writes exist are run against PostgreSQL 15, not SQLite. The
lane names its own database inside whatever the service provides, so the two
modules never wait for each other's locks.

```bash
cd apps/api && TEST_POSTGRES_URL=<a disposable postgresgresql-15 url> \
  uv run pytest -q -m postgres \
  tests/integration/test_provider_pinning_migration.py \
  tests/integration/test_provider_worker_postgres.py
```

What they prove, by name: a queued snapshot survives a worker restart and a
configuration change; a started request is not blindly replayed after a crash;
a cancel fences a late result and closes the stream; a stale worker cannot
publish an attempt or a message; two workers do not duplicate dispatch; the
terminal commit is atomic; a snapshot after retention still preserves provider
usage; and the checkpoint's own tables carry no credential value.

The unit lane is the same suite minus those lanes, and it has to pass with no
PostgreSQL and no vendor credentials — that is what lets an ordinary PR be
reviewed at all:

```bash
cd apps/api && uv run pytest tests/ -q -m "not provider_live and not postgres"
```

One lane is not of the code but of the packaging: `tests/test_packaging.py`
reads `docker-compose.yml` and `.env.example` and asserts what an operator
would actually get. That the API and the worker are configured with the same
thing, so a run one queues is executed under the profile the other offered;
that a credential is named and never carried, and never made required — an
installation without a key still starts, and says which profiles it cannot
serve; that every binding the packaging interpolates is one the documented
environment names, so following the docs does not leave a service
half-configured; and that the opt-in an operator sets is the name the live
test reads and a registered lane, so a qualification that never happened
cannot look like a pass. The check reads those files as text and asserts
only their shape, which is why validating the packaging cannot put a secret
in a log.

## The browser

The browser's provider journey is walked end to end against local fixtures:
read what an installation offers, choose, send, watch the record, reload, and
see the same thing. The fixtures answer the endpoints the app actually calls,
including the event stream's URL with its cursor query.

```bash
cd apps/web && pnpm test:e2e provider-portability.spec.ts
```

Nine cases: an unavailable profile is stated with its reason before anything
is sent — two reasons, a key this installation does not configure and an
adapter this build does not ship, stated the same way; choosing another
profile names it and the submitted payload asks for it; a chosen profile
survives a reload because the run read states it; a different adapter at a
local destination reads the same way and reports no counts, so the card says
"Not reported" rather than estimating; a fallback is offered only where the
chosen profile allows it; a bounded outage is reported with the copy the API
settled and no answer behind it; a cancellation the record carries leaves its
own end; a run that is still going can be stopped from the browser, which
asks about that run and learns the end from the run read rather than
assuming the ask was enough; and a completed turn leaves exactly one answer.

## Live qualification

One real turn, opt-in, for one installed profile an operator names. This is
the only test that contacts a provider, and it can incur charges — which is
why it is opt-in rather than part of a suite a bot runs.

```bash
cd apps/api && OPENSHIP_LIVE_PROFILE_ID=<an installed profile id> \
  uv run pytest tests/live/test_provider_smoke.py -m provider_live -q
```

It proves a narrow thing, deliberately: that the transport works at the
declared destination and a turn completes. It asks a model for one word, so
**it says nothing about whether an answer is useful** — that is what a person
judges, and it is why `live-qualified` is not `accepted`. The fuller
demonstration the roadmap asks for is the ten-turn scenario in
[roadmap.md](../docs/roadmap.md), not this test.

Its behaviour without a real provider is the honest part:

| Configuration | What happens |
| --- | --- |
| `OPENSHIP_LIVE_PROFILE_ID` unset | Both cases skip. Nothing is claimed. |
| Names a profile this installation does not declare | Both **fail**, with the registry's reason. A selection that invents a profile is a bug, not a skip. |
| Declared, credential absent | Both **fail** with `missing_credentials` and an explanation that names the binding, never a value. |
| Declared and servable | One request, at the declared destination, and the turn must end `stop` with an answer. |

## Current record

Verified 2026-10-13, on this checkout:

| Lane | Result |
| --- | --- |
| API lint (`ruff check .` over `apps/api`) | no findings |
| API unit (`-m "not provider_live and not postgres"`) | 203 passed, 15 deselected |
| API PostgreSQL durable lane | 13 passed, 5 deselected |
| Web typecheck / unit / build | clean, 122 passed, clean |
| Browser portability scenario | 9 passed |
| Live qualification | **pending** — no credential binding is configured, so the test skips |

No tuple is `live-qualified` or `accepted`. The states after
`fixture-tested` stay pending until a person runs the live test against a
configured profile and chooses accept.

## What a new adapter has to add

An adapter is not shipped until it can answer these; the list is the same for
every one of them.

1. Its module registers itself, so the registry never invents an adapter and
   no other module imports an SDK.
2. Its adapter-discriminated `adapter_options` are declared and the contract
   refuses anything else.
3. The shared conformance cases pass against the synthetic transport,
   including the cancel case closing the stream it opened.
4. Its error vocabulary maps onto the shared one (`rate_limited`,
   `server_error`, `invalid_response`, `unsupported_capability`, …) so the
   browser's copy is the same for every adapter.
5. Usage is taken only from what the provider reports; missing stays missing.
6. A live tuple is qualified and then accepted, and the matrix row is filled
   from that tuple's evidence — not from documentation about the model.
