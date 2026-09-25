type: specification
status: approved
date: 2026-09-24
---

# OpenShip UI/UX Design Specification

## Overview

OpenShip is an agentic DevOps co-pilot for platform and developer engineers. The UI follows a **chat-centric approach with embedded artifacts** (Approach A), where the conversation is the primary interaction layer and documents/diagrams appear as embedded cards within the chat stream.

## Design Principles

- **Chat-First Entry:** Users can start immediately by typing natural language
- **Artifact-Embedded:** Documents and diagrams appear as cards within the conversation flow
- **Document Editing as Overlay:** Full-screen editors open on demand, but the chat remains the primary layer
- **Inline Selection Context:** Users select content to chat about it directly
- **Three-Way Sync:** Document ↔ Diagram ↔ Conversation always in sync
- **Modern Dark Mode IDE Aesthetic:** Familiar to DevOps engineers (VS Code, Cursor style)

## Visual Style

- **Color Scheme:** GitHub-style dark theme (`#0d1117` background, `#161b22` surfaces, `#30363d` borders, `#58a6ff` primary blue)
- **Typography:** System sans-serif for UI, monospace for code/diagram syntax
- **Icons:** Font Awesome
- **Cards:** Gradient accent backgrounds with hover effects
- **Buttons:** Rounded, subtle borders, primary accent color (`#238636`) for actions

## Page Architecture

### 1. Home Page (Chat-First Entry)

- **Chat Input:** Prominent text input with placeholder "What are you going to do?"
- **Workflow Template Cards:** Curated grid of built-in workflows (Provision & Build, Investigate & Debug, CI/CD Setup) with icons and descriptions
- **Custom Workflow Section:** User-defined workflows displayed below built-in ones
- **Project Switcher:** Dropdown in header to switch between projects
- **Recent Projects:** Quick access to active projects
- **In-Place Evolution:** Home page transforms into the selected workflow interface without redirect

### 2. New Project Page

- **Two Entry Points:**
  - **[Describe your project]** — Start with text-based requirements document
  - **[Build with Diagram]** — Start with diagram-as-code (Mermaid)
- **Low Barrier:** Simple, clear choice without overwhelming options

### 3. Document Editor

- **Full-Screen Overlay:** Opens on demand from chat or project navigation
- **Markdown Editing:** Rich text editing with toolbar (bold, italic, heading, code block)
- **Inline Chat Integration:** "Chat about selection" button in toolbar
- **Agent Suggestions:** Banner suggesting actions like generating diagrams
- **Three-Way Sync:** Changes automatically sync to related diagrams and conversation

### 4. Diagram Editor

- **Diagram-as-Code:** Mermaid syntax editor with live preview
- **Split View:** Code on left, rendered preview on right
- **Draft First, Agent Refines:** User sketches with basic shapes, agent generates polished version
- **Agent Review:** Agent suggests improvements to draft diagrams
- **Versioning:** Draft (v0) → Agent-refined (v1) → Final (v2+)

### 5. Workflow Session Page

- **Chat Stream:** Conversation between user and agent
- **Embedded Artifacts:** Documents and diagrams appear as cards within the chat
- **Approval Workflow:** Agent proposals appear with Approve/Modify buttons
- **Inline Diff View:** Agent changes shown as diffs; engineers approve/reject each change
- **Session Status:** Active/inactive indicator, session ID, timestamps
- **Export & Stop:** Session management controls

## Interaction Patterns

### Chat-Driven Workflow
1. User types natural language task
2. Agent routes to appropriate workflow template
3. Home page evolves into workflow session interface
4. Agent generates artifacts (documents, diagrams, code)
5. Artifacts appear as embedded cards in chat stream
6. User clicks cards to open full editors for refinement
7. Agent proposes changes as inline diffs
8. User approves/rejects changes inline
9. Agent executes approved plan

### Artifact Selection & Chat
1. User selects text in document or component in diagram
2. Inline selection indicator appears
3. User clicks "Chat about this" button
4. Chat panel opens with selection context
5. Agent responds with targeted suggestions
6. Changes sync across document, diagram, and conversation

### Navigation Model
- **Home Page:** Central hub, chat-first entry
- **Project Switcher:** Dropdown in header for switching projects
- **History:** Separate view for past workflow sessions
- **Artifact Cards:** Click to open full editor, close returns to chat stream

## Technology Stack (Mockup)

- **CSS Framework:** Tailwind CSS (CDN)
- **Icons:** Font Awesome 6
- **Diagrams:** Mermaid.js (for actual implementation)
- **Mockup Server:** Static HTML served via Node.js HTTP server

## Mockup Files

All mockup HTML files are located in `mockup/`:

- `homepage.html` — Home page with chat-first entry and workflow cards
- `new-project.html` — New project selection page
- `document-editor.html` — Requirements document editor
- `diagram-editor.html` — Diagram-as-code editor with preview
- `workflow-session.html` — Active workflow session with embedded artifacts
- `cicd-workflow.html` — CI/CD setup workflow with GitHub Actions
- `debug-workflow.html` — Investigate & debug workflow with telemetry and fix proposal

## Implementation Notes

- Use React or Vue for actual implementation with component-based architecture
- Implement real Mermaid.js rendering for diagram preview
- WebSocket for real-time chat and agent status updates
- Git integration for versioning documents and diagrams
- LangGraph for workflow orchestration backend

## CI/CD Setup Workflow

### User Flow
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

### UI Components
- **Repo Connection Card:** Input for repository URL with connect button
- **Workflow Generation Card:** Agent proposes workflow configuration with approve/modify buttons
- **Embedded YAML Artifact:** Generated workflow YAML displayed as embedded card
- **Run Status Card:** Real-time workflow run status with in-progress animation
- **Recent Runs List:** History of workflow runs with status indicators

## Open Questions

- Keyboard shortcuts for power users (TBD)
- Multi-project view layout (TBD)
- Mobile responsiveness (TBD)