# OpenShip

An agentic DevOps workflow platform where everything is a workflow. OpenShip orchestrates multi-step automation tasks through AI-driven agents — from infrastructure provisioning and debugging to CI/CD and custom business processes. Describe what you need, collaborate with the agent through conversation, and watch it plan, execute, and report — with you in control at every step.

## Philosophy

OpenShip is built on a simple principle: **AI as co-pilot, not replacement**. You articulate the requirements, the agent handles the heavy lifting of design, validation, and execution. You retain control at critical decision points through human-in-the-loop approval cards.

Built by DevOps and platform engineers for software engineers — practical automation tools from people who understand the daily challenges.

**Self-hosted by design.** You own your data, you control the environment, you choose the AI model provider. No vendor lock-in, no data sent to third-party services unless you explicitly configure it.

## How It Works

In OpenShip, every automation task is expressed as a workflow — a sequence of steps executed by AI agents using tools and connectors.

**Workflow Execution Model:**

1. **Describe** — Define your task through conversation or a requirements document
2. **Plan** — The agent builds an execution plan, selecting appropriate tools and steps
3. **Validate** — Review the plan, approve critical decisions through human-in-the-loop cards
4. **Execute** — The agent runs the workflow, handling errors and adapting as needed
5. **Report** — Receive structured results and conversational summaries

**Built-in workflow templates** cover common DevOps patterns:

- **Provision/Build** — Infrastructure from requirements to deployment
- **Investigation/Debug** — Telemetry gathering, hypothesis testing, remediation
- **CI/CD Setup** — Pipeline generation and deployment management
- **Alert Response** — Automated reaction to monitoring alarms
- **Scheduled Reporting** — Periodic data collection and dashboard updates
- **Cost Optimization** — Resource usage analysis and right-sizing recommendations

**Extensible by design:** Build custom workflows for your specific needs, compose existing workflows, or import community-shared ones from the workflow registry. The same execution model powers built-in features and user-created automations — everything is a workflow.

## Where to Start

**Read the ideas first.** Understand the project's philosophy, design principles, and roadmap by reading [`docs/ideas.md`](docs/ideas.md). This document captures the core vision, workflow patterns, and architectural decisions.

**Explore the Agent PoC.** Try our working proof-of-concept that demonstrates the core workflow compilation engine:

```bash
cd poc/agent
pip install -r requirements.txt  # or use uv
python -m src.cli examples/freeform-greeting.md
```

The PoC takes a free-form markdown description, uses an LLM to generate an executable plan, presents it for your approval, and executes it. See [`poc/agent/examples/`](poc/agent/examples/) for workflow examples.

**Explore the mockups.** See the intended user experience through our interactive mockups:

```bash
cd mockup
pnpm install
pnpm dev
```

Open `http://localhost:5173` to see the chat-first interface, agent workspace, and workflow builder designs. These are static demonstrations — no functionality yet.

## Open Source Roadmap

OpenShip is developed in transparent phases:

### 🔴 Current Phase: Addressing Problems & Brainstorming Solutions

We're actively defining the problem space and exploring solution approaches. This phase includes:

- Collecting and validating ideas from real DevOps pain points
- Designing the agent architecture and workflow orchestration
- Building UI/UX mockups to demonstrate concepts
- Selecting the technology stack and building core components

**What's available now:** Draft HTML mockups in the [`mockup/`](mockup/) directory that demonstrate the intended UI/UX and interaction patterns. These are static demonstrations — no functionality yet.

### Upcoming Phases (Planned)

- **Phase 2:** Core agent implementation with basic workflow execution
- **Phase 3:** Built-in workflow templates and tool registry
- **Phase 4:** Expanded tool/connector ecosystem and production deployment
- **Phase 5:** Community workflow sharing and ecosystem growth

## Contributing

OpenShip is in its early days and we'd love your help shaping its future. Here's how to get involved:

- **⭐ Star the repo** — Let us know you're following the development
- **💬 Share feedback** — Join discussions on the project's design and features
- **🐛 Report issues** — Found a bug or have an improvement suggestion? Open an issue
- **📝 Contribute ideas** — Add your thoughts to [`docs/ideas.md`](docs/ideas.md)
- **🔨 Submit pull requests** — Help build features, fix bugs, or improve documentation

All contributions are welcome, no matter your experience level. We're building this together.

## Repository Layout

```
openship/
├── poc/
│   └── agent/              # Agent workflow compilation PoC (Python)
├── apps/
│   ├── web/                # Frontend app (Vue.js) — planned
│   ├── api/                # API service (FastAPI) — planned
│   └── agent/              # Agent service — after PoC graduation
├── packages/
│   ├── shared/             # Shared Python modules — planned
│   └── ui/                 # Shared frontend components — planned
├── docs/                   # Documentation, ideas, and design specs
├── mockup/                 # UI/UX prototypes (static HTML)
└── README.md
```

## License

MIT License — see [LICENSE](LICENSE) for details.
