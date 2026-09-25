# OpenShip UI Mockups

Vue 3 mockups demonstrating the OpenShip agentic DevOps platform UI. Uses Vite, Vue Router, Tailwind CSS v4, and Lucide icons.

## How to Run

```bash
pnpm install
pnpm dev
```

Open <http://localhost:5173> in your browser.

To build for production:

```bash
pnpm build
pnpm preview
```

## Pages

| Page | Route | Description |
| ------ | ------ | ------------- |
| **Home** | `/` | Chat-first entry with workflow template cards and recent projects |
| **New Project** | `/new-project` | Two entry points: text requirements or diagram-as-code |
| **Agent Workspace** | `/agent-workspace` | Three-zone layout with chat stream, reasoning blocks, tool call cards, and approval cards |
| **Artifact Inspector** | `/artifact-inspector` | Code editor with tabs for code, diagram, and terminal output |
| **Workflow Builder** | `/workflow-builder` | Agent conversation that helps justify requirements, explain process, discover tools. Offers [Save] [Run in project] and [Export workflow file] |
| **Tool Registry** | `/tool-registry` | Browse available tools, connectors (MCP), and custom scripts |
| **Connectors** | `/connectors` | Browse available connectors and manage authentication to 3rd-party platforms |
| **Infrastructure Setup** | `/infrastructure` | Built-in workflow: repo connection, requirements doc, architecture diagram, Terraform generation, and apply |
| **CI/CD Setup** | `/cicd` | Built-in workflow: repo connection, workflow generation, run management |
| **Investigate & Debug** | `/debug` | Built-in workflow: symptom description, telemetry gathering, hypothesis testing, fix proposal |

## Design Principles

- **Chat-First:** Conversation is the primary interaction layer
- **Embedded Artifacts:** Documents, diagrams, and code appear as cards in chat
- **Three-Way Sync:** Document ↔ Diagram ↔ Conversation
- **Human-in-the-Loop:** Approval cards at critical decision points
- **Zero-Distraction Minimalism:** Clean, functional admin shell
