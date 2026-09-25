type: story
summary: Research and comparison of AI agent frameworks and technologies for building OpenShip's agentic DevOps platform.
status: in-progress
date: 2026-09-22
---

# Technology Candidates for OpenShip

Research and comparison of AI agent frameworks and technologies for building OpenShip's agentic DevOps platform.

## Requirements

OpenShip requires a framework that supports:

1. **Durability:** Long-running workflows, crash recovery, resume at checkpoints
2. **Tool Routing:** Dynamic tool/skill selection without loading entire registry
3. **Hook Events:** before/afterTool, before/afterThought, onError, etc.
4. **Sandbox Execution:** Isolated environments for terraform, gcloud, aws cli, etc.
5. **Authentication:** Credential delegation, scoped access
6. **Human-in-the-Loop:** Plan approval, multi-channel notifications

## Framework Comparison

| Framework | Architecture | Durability | HITL | Tool Routing | Sandbox | Multi-language | Maturity | OpenShip Fit |
| ----------- | ------------- | ----------- | ------ | ------------- | --------- | --------------- | ---------- | -------------- |
| **LangGraph** | Explicit directed graphs (Pregel) | ⭐⭐⭐⭐⭐ Built-in checkpointing every superstep | ⭐⭐⭐⭐⭐ First-class `interrupt()` | ⭐⭐⭐⭐⭐ Conditional edges, subgraphs | None | Python/TS/Go(dev) | Production (Uber, LinkedIn) | Excellent |
| **MS Semantic Kernel** | Graph-based orchestration | ⭐⭐⭐⭐ Checkpointing (in-memory/file/Cosmos) | ⭐⭐⭐⭐ RequestPort pattern | ⭐⭐⭐⭐ Agent delegation, tool routing | None | Python/C#/Java/Go | Production (Microsoft) | Very Good |
| **Google ADK** | Code-first, event-driven | ⭐⭐⭐ Session persistence + ResumabilityConfig | ⭐⭐⭐⭐ Built-in tool confirmation + graph HITL nodes | ⭐⭐⭐ LLM-driven (not deterministic) | ⭐⭐⭐⭐⭐ gVisor, Cloud Run built-in | Python/TS/Go/Java | Young (April 2025, 20k stars) | Good |
| **CrewAI** | Role-based teams + event-driven flows | ⭐⭐⭐ Event-driven checkpointing (not durable execution) | ⭐⭐⭐⭐ `@human_feedback` decorator | ⭐⭐⭐ LLM-driven tool selection | None | Python (AMP: JS) | 57k stars, active | Good |
| **OpenAI Agents SDK** | Minimal primitives (Agent/Tool/Guardrail/Handoff) | ⭐⭐ Manual checkpointing only | ⭐⭐⭐⭐ `needs_approval` + state serialization | ⭐⭐⭐⭐⭐ Best-in-class (MCP, namespaces, shell) | ⭐⭐⭐⭐ Built-in SandboxAgent | Python/TS(lags) | 29.6k stars, 0.x versioning | Good (execution engine) |
| **Temporal** | Durable execution engine (not AI-specific) | ⭐⭐⭐⭐⭐ Event-history replay, weeks/months | ⭐⭐⭐ Signals/Queries | N/A | None | 8 languages | Production (Coinbase, Uber) | Complementary |

## Detailed Analysis

### LangGraph — Top Recommendation

**Best for:** OpenShip's document-driven, workflow-template architecture

- **Architecture alignment:** Explicit graph nodes map naturally to workflow stages (requirements → design → code → validation → execution)
- **Durability:** Checkpoints at every superstep boundary; Postgres-backed for production; crash recovery is first-class
- **HITL:** `interrupt()` is designed for this — pause workflow, persist state, resume with human decision
- **Workflow templates:** Supports linear, iterative (validation loops), and non-linear (investigation with parallel hypothesis testing)
- **Learning curve:** Steep (Pregel model, graph theory), but worth it for production reliability
- **Ecosystem:** LangChain integration, LangSmith observability, 42k+ stars, production-proven

**Pros:**

- Excellent durability — crash recovery is first-class
- First-class HITL support
- Explicit state schema maps well to document-driven artifacts
- Production proven (Uber, LinkedIn, Klarna)
- Flexible graph model supports all workflow templates
- Strong middleware system for hooks
- Subgraph support for specialized agents

**Cons:**

- Steep learning curve
- Low-level — more boilerplate than higher-level frameworks
- Go support still in development
- No built-in sandbox or secret management
- Checkpoint history grows over time

### Microsoft Semantic Kernel — Strong Alternative

**Best for:** Enterprise DevOps environments with Go/C# teams

- Multi-language support is best (Python, C#, Java, Go)
- Graph-based orchestration similar to LangGraph
- Microsoft backing = long-term stability
- Comprehensive middleware/filter system
- 13.7k stars, production-ready

**Pros:**

- Robust checkpointing with multiple backends
- First-class HITL via RequestPort pattern
- Strong multi-language support
- OpenTelemetry built-in
- MCP support

**Cons:**

- Less mature ecosystem than LangGraph
- Smaller community
- Less documentation and examples

### Google ADK — Emerging Contender

**Best for:** If built-in sandbox execution is critical

- Only framework with first-class sandbox (gVisor, Cloud Run)
- Excellent credential management built-in
- 6 callback hooks + plugin system
- Python/TypeScript/Go/Java support

**Pros:**

- Excellent sandbox execution
- Built-in credential management
- Good documentation and Google backing
- Workflow templates match OpenShip's needs

**Cons:**

- Young ecosystem (launched April 2025)
- Less production-proven
- LLM-driven tool routing (less deterministic)
- Checkpointing requires manual idempotency logic
- No CLI/UI resume support

### CrewAI — Easiest to Start

**Best for:** Rapid prototyping, simpler workflows

- Role-based paradigm is intuitive
- Rich event system (100+ event types)
- 57k stars, large community
- Good for document-driven workflows

**Pros:**

- Low learning curve
- Easy to prototype
- Rich event system for hooks and notifications
- Large ecosystem of tools

**Cons:**

- Checkpointing is not true durable execution
- Tool selection is LLM-driven, not deterministic
- No built-in sandboxing or credential management
- Enterprise features require paid tier
- Less production-proven for complex stateful workflows

### OpenAI Agents SDK — Execution Engine

**Best for:** Best-in-class tool routing, built-in sandbox

- Excellent tool ecosystem (MCP, namespaces, shell tools)
- Mature HITL with `needs_approval`
- Built-in SandboxAgent
- 29.6k stars, 10.3M monthly downloads

**Pros:**

- Minimal abstraction — easy to understand
- Excellent tooling and MCP support
- Mature human-in-the-loop system
- Built-in sandbox
- Provider-agnostic (use any LLM)

**Cons:**

- No durable execution — manual checkpointing only
- No built-in workflow orchestration
- No notification system
- 0.x versioning — breaking changes possible
- No UI/builder
- JS/TS SDK lags Python

### Temporal — Complementary Durable Execution

**Best for:** Ultra-reliable long-running workflows

- Durable execution engine (not AI-specific)
- Event-history replay for crash recovery
- Workflows can run for weeks/months
- Used by Coinbase, Uber, Netflix

**Pros:**

- Best-in-class durable execution
- Built-in retry/timeout policies
- Continue-as-new pattern for very long workflows
- Human-in-the-loop via signals

**Cons:**

- Not an AI agent framework — no built-in LLM/tool integration
- Requires LangGraph agents to run as Temporal activities
- Additional operational complexity

## Recommendation for OpenShip

**Primary: LangGraph** — Best overall fit for OpenShip's architecture:

- Graph model matches document-driven workflow templates perfectly
- Production-proven durable execution for long-running provisioning jobs
- First-class human-in-the-loop support
- Strong ecosystem and community

**Complementary: Temporal** — Consider pairing with LangGraph for:

- Workflows spanning days/weeks
- Multi-region distributed execution
- Ultra-reliable crash recovery with event-history replay

**Hybrid Architecture:**

```
OpenShip Application Layer
├── Workflow Templates (LangGraph graphs)
├── Document Parser/Generator
├── Approval UI + Notifications
├── Credential Manager (custom middleware)
└── Checkpoint Coordinator
        ↓
LangGraph Execution Engine
├── State Graphs (provision, debug, CI/CD)
├── Tool Nodes (AWS, GCP, Azure, K8s)
├── Sandbox Nodes (Firecracker/gVisor)
└── HITL Interrupts
        ↓
Infrastructure (Cloud APIs, Sandboxes, Git)
```

## Additional Technologies

### Sandboxed Execution

- **Firecracker MicroVMs:** Hardware-level isolation, <125ms boot, proven in AWS Lambda/Vercel/E2B
- **gVisor:** Syscall interception, works without KVM, used by Google Cloud Run
- **Docker/Podman:** Fastest startup, mature ecosystem, suitable for trusted environments

### Authentication & Credentials

- **OIDC Workload Identity Federation:** Short-lived credentials via STS/AssumeRoleWithWebIdentity
- **HashiCorp Vault:** Centralized secrets management, dynamic credential generation
- **OAuth 2.1 Token Exchange:** Fine-grained scoped credentials

### Human-in-the-Loop & Notifications

- **Apprise:** Notification aggregator supporting 100+ channels
- **LangChain HITL Middleware:** Proven interrupt/resume patterns
- **Slack Block Kit + Telegram Inline Keyboards:** Interactive approval UX

### Tool Routing

- **Tool Search Tool Pattern with ChromaDB:** Agent searches for relevant tools instead of loading full registry
- **Hierarchical Routing:** Route by domain first, then to specific tool
- **Embedding-based Matching:** Semantic tool selection via vector similarity

## References

- LangGraph Documentation: <https://langchain-ai.github.io/langgraph/>
- Microsoft Semantic Kernel: <https://learn.microsoft.com/en-us/semantic-kernel/>
- Google ADK: <https://adk.google.dev/>
- CrewAI: <https://docs.crewai.com/>
- OpenAI Agents SDK: <https://openai.github.io/openai-agents-python/>
- Temporal: <https://docs.temporal.io/>
- Agent Hooks Specification (Microsoft): <https://responsibleai.github.io/agent-hooks/>
