# OpenShip Proofs of Concept

These experiments preserve earlier designs. Current development targets the API server and runtime worker in `apps/api` and browser client in `apps/web`. Start with the [root README](../README.md), [architecture](../docs/architects.md), and [roadmap](../docs/roadmap.md).

## Agent CLI

The [workflow CLI](agent/README.md) explores plan review, compilation, caching, and execution. It was never graduated into the application; its direct subprocess executor is not the production execution boundary.

```bash
cd poc/agent
uv sync --extra dev
uv run python -m src.cli build examples/my-workflow.md
uv run python -m src.cli execute examples/my-workflow.md
```

## UI Mockups

The [Vue mockups](mockup/README.md) demonstrate interaction concepts without the application backend.

```bash
cd poc/mockup
pnpm install
pnpm dev
```

Open http://localhost:5173. Run the application client and mockups separately to avoid using the same port.
