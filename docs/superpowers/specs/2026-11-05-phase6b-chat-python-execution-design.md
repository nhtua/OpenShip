---
type: spec
summary: General Python execution and bounded URL downloads in agentic chat, with PNG/JPEG artifact previews.
status: approved-awaiting-implementation
date: 2026-11-05
---

# Task 6 extension: chat-directed Python execution

## Intent and approval boundary

The user wants to ask in a conversation to analyze data at a URL. The agent
chooses to fetch the file, write Python, execute it with the downloaded data in
OpenSandbox, and return a chart in chat. The execution tool must be **general
Python execution**, not an analysis- or chart-specific capability. Charting is
the demonstration, not a required workflow or tool output.

The user approved this written design on 2026-11-05, correcting the earlier
analysis-specific proposal and requesting implementation. Demonstration
acceptance and passing Task 6 evidence remain separate and pending.

This extends the [approved Phase 6B scope](2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md).
That scope deliberately limited output to text/JSON and the workload to the
standard library. This proposal adds a plotting runtime, PNG/JPEG outputs/previews,
URL downloading, and model-selected Python execution. Preserve the existing
analysis capability, persisted runs, and explicit resume behavior; do not rename
old contracts in place or treat their earlier verification as evidence for the
new chat path.

## Architecture and alternatives

**Recommended:** reuse the existing bounded chat model/tool loop with two
capabilities: `download_file` and `python_execute` (proposed public names).
The model chooses their order and writes code. Downloading, immutable source
capture, sandbox execution, output validation, and UI rendering remain separate
implementation responsibilities. No separate reasoning engine, generic planner,
or saved-workflow compiler is required.

Alternatives considered:

- A fixed URL-to-chart graph simplifies this one demonstration but couples a
  general executor to analysis and does not demonstrate agent-selected steps.
- A separate source-writing tool could support independent source preparation,
  but adds an unnecessary model-visible step for this slice. The executor can
  capture submitted code before dispatch while keeping capture separate from
  the underlying provider client.

The existing model/tool loop is in `runs/agent_graph.py`. The current
`sandbox_analysis` definition is explicitly not model-selectable; its source
generation is in the fixed analysis graph. Merely changing that selection flag
does not integrate source preparation, artifact authority, or resumability into
chat.

## General execution contract

- Accept exactly one of bounded Python source text or an existing source
  artifact reference, optional input alias-to-artifact mappings, JSON
  parameters, and an explicit output declaration.
- Source text is written by the run's pinned chat model and captured as an
  immutable source artifact before provider execution. The worker verifies
  stored source and inputs and transfers their exact bytes; no host execution.
- Permit computations with no input files and no output files. Return bounded
  stdout/stderr and explicit completion status as well as retained artifact
  references. Neither a chart nor a JSON report is mandatory.
- Reuse existing fixed Python invocation, request-specific directories,
  provider transport, conversation-scoped handles, gateway fencing,
  cancellation, operation idempotency, and artifact publication.
- Runtime image/profile selection is operator-controlled, never a model-supplied
  image, shell command, credential, or host path. General Python does not mean
  unrestricted host access or arbitrary dependency installation.
- Keep current limits: 256 KiB source; 8 inputs, 8 MiB each/16 MiB total;
  8 outputs, 4 MiB each/8 MiB total; 256 KiB combined diagnostics; 16 KiB
  parameters; 30-second command timeout; 1 CPU; 512 MiB; 1,800-second lifetime.
- The model may consume results and select another authorized operation within
  existing budgets. An uncertain provider command never triggers an automatic
  retry or speculative sandbox replacement.

## Download and artifact handoff

The first supported download slice is public, unauthenticated HTTPS CSV/JSON.
No browser automation, authentication cookies, custom authorization headers,
archives, or spreadsheet parsing is included. This bounds the downloader, not
the general Python execution contract.

The download capability streams into staged artifact storage, with an 8 MiB
actual-body bound, a 30-second overall deadline, and at most three redirects.
Reject unsupported schemes, embedded credentials, private/reserved/local
addresses, and destinations that resolve to disallowed addresses. Validate
every redirect and bind each connection to a vetted resolved destination while
preserving TLS hostname verification; a preflight DNS lookup alone is not an
SSRF defense. Disable inherited proxy configuration unless a separately
approved egress policy supplies it. Bound decoded bytes as well as transferred
bytes; do not trust Content-Length or MIME labels alone.

Publish only a complete validated file through normal owned artifact storage.
Return its exact reference, safe display name, format/size, and a bounded data
sample. Treat downloaded content as untrusted data, not model instructions.
Use generated storage keys rather than URL paths; do not log sensitive URL
query strings or expose raw upstream errors.

A critical integration boundary is **same-run artifact handoff**: the download
result must become an authorized, pinned input to a later Python call in that
conversation. Do not broaden authority to arbitrary model-supplied artifact
IDs or freeze tool inputs to only the artifacts selected at chat submission.
Apply normal ownership, project/conversation, ready-state, quota, and digest
checks at execution admission. Preserve that binding during replay and resume.

## Runtime and outputs

Use a digest-pinned Python image with pinned Matplotlib dependencies and a
headless rendering backend. No pandas or runtime package installation is
required initially. The new runtime/dependency identity changes the existing
compatibility key; it must not silently reuse a standard-library-only runtime.

Add declared `image/png` and `image/jpeg` alongside existing text/JSON outputs.
Validate complete PNG/JPEG content with a maintained decoder under explicit limits: at most 4096 pixels
per dimension and 16 million pixels total, in addition to the existing byte
limits. Reject truncated/malformed images and decompression bombs; magic bytes
or a `.png`, `.jpg`, or `.jpeg` suffix alone do not qualify; decoded format must
match the declared media type. Do not enable BMP, SVG, HTML, archives, or
arbitrary binary previews. Preserve existing returned-source expectation checks;
accepted source remains inert outside a separately authorized sandbox request.

Publish validated outputs and source references through the existing artifact
store and fenced operation result. The model receives bounded references and
metadata, not base64 image payloads. Its final answer explains the outcome and
links retained evidence; image interpretation by a vision model is not required
or claimed.

## Chat presentation, failure, and durability

Show download/execution progress using existing operation cards. Render a PNG/JPEG
preview from an owned artifact referenced by the run result, using authenticated
artifact retrieval and a managed object URL, not a model-written external image
URL or active HTML. Include a download action and accessible label. Revoke
object URLs when no longer used; keep artifacts readable after page reload,
application restart, and provider expiry.

Expose actionable download, format, execution, timeout, validation, and provider
failures without credentials or raw provider bodies. A failed or unvalidated
chart is not a successful result. Existing run cancellation and late-result
fences remain authoritative.

Preserve existing A/B analysis resume contracts. For the new chat path, design
explicit continuation against the persisted transcript and committed operation
results: do not assume analysis-specific resume state works unchanged. Retain
the downloaded artifact and captured source so resuming an interrupted execution
does not silently refetch mutable URL data or regenerate completed source.
Re-execution from the beginning must be an explicit new run using the original
captured input artifacts, not a silent refetch of the URL. Fetching newer data
requires a new user request. Restart or
broker redelivery alone never resends uncertain Python commands. The execution
plan must identify the necessary additive chat resume contract and tests rather
than introduce a general workflow engine.

## Task 6 demonstration and verification

1. A user asks in chat to analyze a public CSV/JSON URL and draw a chart.
2. The model selects download, receives the retained file reference, and writes
   task-appropriate Python using the actual data structure.
3. It selects general Python execution with that exact input and a declared PNG
   output. The real worker/gateway/OpenSandbox path executes the captured source.
4. Chat displays the retained chart with a download and evidence-linked answer.
5. A second compatible execution uses changed input bytes and a fresh directory
   while reusing the compatible sandbox. Also test a non-analysis Python task
   without files, proving the tool is not chart-specific.
6. Verify blocked URL targets/redirects, download bounds, malformed/oversized
   PNGs/JPEGs, failed commands, provider unavailability, and explicit continuation
   without duplicate committed execution. Verify retained downloads after
   restart/provider absence, plus existing analysis A/B resume regressions.

Use existing pytest and Playwright runners. The service journey must include
real PostgreSQL, authenticated JetStream, artifacts, API/worker/gateway, and
OpenSandbox; transport stubs cannot establish that evidence. Clearly labeled
deterministic model fixtures may establish repeatable tool ordering; record
real-model observations separately. Do not claim deterministic fixtures prove
general model planning ability.

The user authorized using the running dev dependencies/provider. This does not
authorize resetting their database, purging broker streams, or modifying volume
ownership. Use a disposable database, isolated broker subjects/consumers with
appropriate credentials, and an isolated artifact root/storage identity. Verify
configuration before execution; ask before disruptive changes or broader access.

Task 6 remains unverified and demo acceptance pending. The earlier parser,
returned-source, reuse, expiry, durability, and resume checks remain obligations;
the implementation plan must map old and new criteria explicitly rather than
silently replacing unchecked tests with the chart happy path.

## Preparation gates

- [x] Inspect existing chat loop, sandbox contracts/source capture, and artifacts.
- [x] Record the user's general-executor correction and architecture proposal.
- [x] Draft scope, contracts, trust boundaries, compatibility, and acceptance.
- [x] User reviews and approves this written design (2026-11-05).
- [ ] Write/review the implementation plan and select execution method.
- [ ] Implement with RED/GREEN evidence and run the real-service/browser journey.
- [ ] Present the runnable demonstration for explicit accept/revise/reject.
