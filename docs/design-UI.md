type: design
summary:
status: in-progress
date: 2026-09-24
---

# Frontend UI (webapp)

## Vision

Prototyping the idea via webapp is quite fast and responsive to changes in the early phase. I will work on addressing the problems and imagine the practical solution at once. So I decided to first build the mockup UI, with no real functionality, but only what UI/UX looks like:

- Should be agentic application which mixes UI components and conversational agent style, that allows the agent to go through input/reasoning/feedback loop that fine-tunes the output
- Seemsless transition between functional UI components - Conversational UI - status update - and output. Every single component must be logically engineered to placed at the most practical place, that enable/speedup user. Not force user to learn.
- Must be easy to find/navigate things
- The main "vibe" of this app is "autonomous workflow", the agent allows and support user to build the workflow they want. That mean user will need to describe what they want. the agent browses the registry of available tools/skills/workflows to generate a workflow for them. Missing tools can be suggestion to install, or replace by custom tools (generated Python scripts)
- So that also mean some built-in wokflows has better support by tools

## Features and designs

### Design Approach: Chat-Centric with Embedded Artifacts

OpenShip follows a chat-centric UI where the conversation is the primary interaction layer. Documents, diagrams, and code artifacts appear as embedded cards within the chat stream. Full-screen editors open on demand for artifact refinement.

**Key principles:**

- Chat-first entry point with natural language input
- Artifacts embedded as cards in conversation flow
- Document/diagram editors open as overlays
- Inline selection context for targeted chat
- Three-way sync: Document ↔ Diagram ↔ Conversation
- Modern dark mode IDE aesthetic (GitHub-style dark theme)

### Mockup Pages

All mockups are static HTML files in `mockup/` using Tailwind CSS and Font Awesome icons. They are interlinked to demonstrate the full user journey.

| Page | File | Description |
| ------ | ------ | ------------- |
| Home | [mockup/homepage.html](mockup/homepage.html) | Chat-first entry with workflow template cards and recent projects |
| New Project | [mockup/new-project.html](mockup/new-project.html) | Two entry points: text description or diagram |
| Document Editor | [mockup/document-editor.html](mockup/document-editor.html) | Requirements editor with agent suggestions |
| Diagram Editor | [mockup/diagram-editor.html](mockup/diagram-editor.html) | Mermaid diagram-as-code with live preview |
| Workflow Session | [mockup/workflow-session.html](mockup/workflow-session.html) | Chat stream with embedded Terraform artifact cards |
| CI/CD Setup | [mockup/cicd-workflow.html](mockup/cicd-workflow.html) | CI/CD workflow: repo connection, workflow generation, run management |
| Investigate & Debug | [mockup/debug-workflow.html](mockup/debug-workflow.html) | Debug workflow: symptom description, telemetry gathering, hypothesis testing, fix proposal |

### User Flow

1. User opens OpenShip → Home page with chat input
2. User types natural language task or selects workflow template
3. Home page evolves into workflow session interface
4. Agent generates artifacts (documents, diagrams, code)
5. Artifacts appear as embedded cards in chat stream
6. User clicks cards to open full editors for refinement
7. Agent proposes changes as inline diffs
8. User approves/rejects changes inline
9. Agent executes approved plan

### CI/CD Setup Workflow Flow

1. User selects "CI/CD Setup" workflow from home page
2. Agent asks for GitHub repository connection (URL + auth)
3. User describes desired CI/CD behavior (triggers, steps, deploy targets)
4. Agent generates GitHub Actions workflow YAML files
5. User reviews and approves workflows (inline diff view)
6. Agent pushes workflows to repository
7. Agent triggers initial workflow run
8. Real-time status updates shown with in-progress indicator
9. User can view logs, open runs in GitHub, or manage from OpenShip
10. Recent runs list accessible for monitoring and debugging

### Visual Design

- **Color scheme:** GitHub-style dark (`#0d1117` bg, `#161b22` surface, `#30363d` border, `#58a6ff` primary)
- **Typography:** System sans-serif for UI, monospace for code
- **Icons:** Font Awesome
- **Cards:** Gradient accent backgrounds with hover effects
- **Buttons:** Rounded, primary accent color (`#238636`) for actions

### Implementation Notes

- Use React or Vue for actual implementation with component-based architecture
- Implement real Mermaid.js rendering for diagram preview
- WebSocket for real-time chat and agent status updates
- Git integration for versioning documents and diagrams
- LangGraph for workflow orchestration backend

### Detailed Design Doc

See [docs/superpowers/specs/2026-09-24-ui-ux-design.md](superpowers/specs/2026-09-24-ui-ux-design.md) for complete specification.
