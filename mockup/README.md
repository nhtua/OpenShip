# OpenShip UI Mockups

Static HTML mockups demonstrating the OpenShip agentic DevOps platform UI. Uses Tailwind CSS and Lucide icons following the shadcn-vue design language.

## How to View

Run a local HTTP server in this directory:

```bash
python -m http.server 8000
```

Then open <http://localhost:8000/homepage.html> in your browser.

## Mockups

| Mockup | File | Description |
| ------ | ------ | ------------- |
| **Home** | [homepage.html](homepage.html) | Chat-first entry with workflow template cards and recent projects |
| **Agent Workspace** | [agent-workspace.html](agent-workspace.html) | Three-zone layout with chat stream, reasoning blocks, tool call cards, and approval cards |
| **Artifact Inspector** | [artifact-inspector.html](artifact-inspector.html) | Code editor with tabs for code, diagram, and terminal output |
| **Workflow builder** | [workflow-builder.html](workflow-builder.html) | Agent conversation that helps justify the requirements, explain the process and discover tools to build new workflow. Offer buttons [Save] [Run in project] and [Export workflow file] after finished |
| **Tool Registry** | [tool-registry.html](tool-registry.html) | Browse available tools, connectors tools (MCP), and custom scripts |
| **Connectors** | [connectors.html](connectors.html) | Browse available connectors, allow user to manage the authentication to 3rd platform |
| New Project | [new-project.html](new-project.html) | Two entry points: text descriptwe |
| Infrastructure Setup | [infrastructure-workflow.html](infrastructure-workflow.html) | Infrastructure - built-in workflow: repo connection, write infrastructure specification, generate components/connections diagrams, generating Terraform code, apply to create cloud resources |
| CI/CD Setup | [cicd-workflow.html](cicd-workflow.html) | CI/CD - built-in workflow: repo connection, workflow generation, run management |
| Investigate & Debug | [debug-workflow.html](debug-workflow.html) | Debug - built-in workflow: symptom description, telemetry gathering, hypothesis testing, fix proposal |

## Design Principles

- **Chat-First:** Conversation is the primary interaction layer
- **Embedded Artifacts:** Documents, diagrams, and code appear as cards in chat
- **Three-Way Sync:** Document ↔ Diagram ↔ Conversation
- **Human-in-the-Loop:** Approval cards at critical decision points
- **Zero-Distraction Minimalism:** Clean, functional admin shell
