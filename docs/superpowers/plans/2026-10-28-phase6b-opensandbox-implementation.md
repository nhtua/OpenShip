# Phase 6B OpenSandbox Implementation Plan

**Goal:** Execute a reviewed Python artifact through OpenSandbox with gVisor and publish bounded results.

**Architecture:** OpenShip retains durable admission and fencing. A standalone OpenSandbox Server owns sandbox lifecycle; the worker uses its lifecycle and execd APIs. Generated code has no application credentials or engine socket.

**Tech stack:** Python, PostgreSQL, NATS JetStream, OpenSandbox, gVisor, Compose.

**Spec:** [Phase 6B design](../specs/2026-10-28-phase6b-sandbox-and-controlled-artifacts-design.md).

## Implementation slices

1. Add a fail-closed OpenSandbox configuration preflight, with tests for missing authentication and insecure runtime configuration. Verify against the actual engine before enabling execution.
2. Qualify the selected upstream server/SDK and Compose topology, including runsc, endpoint authentication, network denial, bounded storage and deadline enforcement. Do not enable execution if any required control cannot be demonstrated.
3. Implement immutable captured file versions, expected-base edits, pruning, and quota handling with PostgreSQL concurrency tests.
4. Add digest-bound Python execution admission and a fenced OpenSandbox adapter that creates once, transfers selected inputs, waits for results, captures outputs and destroys the sandbox. Test uncertain dispatch, cancellation and recovery.
5. Add workspace inspection and explicit execution UI; qualify the complete parser journey and existing Phase 1–6A paths.

Each slice requires focused tests and real-service qualification where the spec calls for it. The runtime remains disabled until isolation and cleanup gates pass.
