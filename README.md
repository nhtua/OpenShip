# OpenShip

OpenShip is an agentic DevOps tool that helps platform and infrastructure engineers provision cloud infrastructure through a structured, human-in-the-loop workflow.

## Architecture

```
OpenShip Application Layer
├── Workflow Templates (LangGraph graphs)
├── Document Parser/Generator
├── Approval UI + Notifications
└── Credential Manager
         ↓
LangGraph Execution Engine
├── State Graphs (provision, debug, CI/CD)
├── Tool Nodes (AWS, GCP, Azure, K8s)
├── Sandbox Nodes (Firecracker/gVisor)
└── HITL Interrupts
         ↓
Infrastructure (Cloud APIs, Sandboxes, Git)
```

## Components

- **backend/** — Python backend with LangGraph agent orchestration
- **frontend/** — Vue 3 web application
- **docs/** — Project documentation and ideas

## Development

### Backend

```bash
cd backend
uv sync
uv run python -m openship.main
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## PoC Workflow

The PoC demonstrates the core workflow:

1. **Requirements** — Engineer writes a requirements document
2. **Diagram** — Agent generates an architecture diagram (Mermaid)
3. **Approve Diagram** — Human reviews and approves the diagram
4. **Terraform Code** — Agent generates Terraform code
5. **Approve Code** — Human reviews and approves the Terraform
6. **Apply** — Agent executes `terraform apply` (simulated in PoC)

## License

TBD