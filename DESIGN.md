# DESIGN.md: DevOps Agentic Workflow Platform

> **Target Implementation Template:** [`Whbbit1999/shadcn-vue-admin`](https://github.com/Whbbit1999/shadcn-vue-admin?utm_source=gemini)
>
> **Tech Stack:** Vue 3.5+ (`<script setup lang="ts">`), Tailwind CSS v3/v4, `shadcn-vue` (Reka UI / Radix Vue), `lucide-vue-next`, Pinia, Vue Router v4.

## 1. Project Vision & Core Design Principles

The **DevOps Agentic Workflow Platform** is an autonomous, chat-centric admin dashboard that bridges natural language conversation with functional UI artifacts. The user describes desired infrastructure or deployment tasks; the agent searches its tool registry, constructs an execution plan, generates required custom Python/Bash scripts or YAML manifests, and executes them with human-in-the-loop approvals.

### Core Principles

1. **Chat-First Entry Point:** Conversation is the primary control surface. All actions originate from natural language or contextual commands.

2. **Seamless UI Transitions:** Effortless flow across Conversational UI $\rightarrow$ Real-time Reasoning/Status Updates $\rightarrow$ Human Approval $\rightarrow$ Rendered Artifacts.

3. **Contextual Artifact Inspector:** Documents, diagrams, terminal logs, and code scripts appear as embedded inline cards within chat, opening full-detail editors in a collapsible Right Inspector Panel.

4. **Three-Way Sync:** Real-time bi-directional synchronization between **Conversation Context** $\leftrightarrow$ **Artifact Documents/Code** $\leftrightarrow$ **Visual Diagrams**.

5. **Zero-Distraction Minimalism:** Clean, functional admin shell based on `shadcn-vue-admin` with low-clutter visual hierarchy, subtle borders, and dark/light contrast.

## 2. Technical Stack Specification

| Layer | Technology | Usage | 
 | ----- | ----- | ----- | 
| **Framework** | Vue 3.5+ (`<script setup lang="ts">`) | Modern Composition API single-file components | 
| **Base Admin Template** | `Whbbit1999/shadcn-vue-admin` | Layout shell, sidebar navigation, theme providers | 
| **UI Component Library** | `shadcn-vue` (Reka UI / Radix Vue) | Accessible dialogs, drawers, accordions, tooltips, cards | 
| **Icons** | `lucide-vue-next` | Standardized minimal icon set | 
| **State Management** | Pinia | Global store for active workspace, chat, tools, and artifacts | 
| **Routing** | Vue Router v4 | App views (Agent Workspace, Tool Registry, Logs, Settings) | 
| **Syntax & Rendering** | `@codemirror/lang-markdown`, Monaco/CodeMirror | Script editing and artifact rendering | 

## 3. Application Layout & Architecture Blueprint

The app extends the standard `shadcn-vue-admin` shell into a three-zone layout:

```
+---------------------------------------------------------------------------------------------------+
|  [Logo] [Collapse]   Header: Global Command Palette (Cmd+K)  |  Agent Status Indicator |  User    |
+--------------+---------------------------------------------+--------------------------------------+
| Left Sidebar | Center Stage: Active Agent Workspace        | Right Stage: Artifact Inspector      |
| Collapsible  | (Width: 50% - 60%)                          | (Width: 40% - 50%, On-Demand)        |
|              |                                             |                                      |
| • Workflows  |  +---------------------------------------+  |  [Header Bar] [Copy] [Save] [Close]  |
| • Registry   |  | Chat Stream Feed                      |  |  ----------------------------------  |
| • Tools      |  |  • User Intent Message                |  |  Tabs: [Code/YAML] [Diagram] [Logs]  |
| • Logs       |  |  • Collapsible Reasoning Block        |  |                                      |
| • Settings   |  |  • Inline Tool Call Card (`k8s.py`)   |  |  [ Active Code Editor / Preview ]    |
|              |  |  • Human Approval Card [Approve]      |  |  • Python Script / Manifest          |
|              |  +---------------------------------------+  |  • Architecture Diagram              |
|              |  | Natural Language Input Bar            |  |  • Live Terminal Stdout/Stderr       |
|              |  +---------------------------------------+  |                                      |
+--------------+---------------------------------------------+--------------------------------------+

```

### A. Global Header (`AppHeader.vue`)

* **Global Command Palette (`Cmd + K`):** Instant search bar for quick invocation of workflows, searching the tool registry, or switching agent models.

* **Agent Global Status:** Pill badge displaying real-time state: `Idle`, `Browsing Registry`, `Reasoning`, `Awaiting Approval`, `Executing`.

### B. Navigation Sidebar (`AppSidebar.vue`)

* Standard collapsible `shadcn-vue-admin` navigation linking to:

  * **Workflows:** Active and template agent workflows.

  * **Tool & Skill Registry:** Available tools, connected APIs, and custom Python script library.

  * **Execution History:** Audit logs of historical autonomous runs.

  * **Settings:** API keys, model parameters, and environment connections.

### C. Center Stage: Chat & Reasoning Stream (`AgentWorkspace.vue`)

* **Chat Stream (`ChatStream.vue`):**

  * **User Message Bubbles:** Crisp, distinct chat inputs.

  * **Reasoning Blocks (`ReasoningBlock.vue`):** Accordion dropdowns showcasing step-by-step thinking (e.g., "Browsing registry for AWS S3 deployment scripts...").

  * **Tool Call Cards (`ToolCallCard.vue`):** Inline cards rendering execution progress, status badges (Success/Failed), and primary outputs.

  * **Human-in-the-Loop Cards (`ApprovalCard.vue`):** High-priority confirmation cards requiring explicit user authorization (e.g., "Execute `terraform apply` on production?").

* **Input Composer (`ChatInput.vue`):**

  * Textarea with multi-line expand, file/log attachment chips, and quick action macros.

### D. Right Stage: Artifact Inspector Drawer (`ArtifactInspector.vue`)

* Slides out automatically when an artifact (code script, YAML spec, diagram, or execution log) is clicked or generated by the agent.

* **Tabbed Interface:**

  1. **Code & Config Tab:** Syntax-highlighted editor for generated Python scripts or Kubernetes/Terraform manifests.

  2. **Diagram Tab:** Live rendered workflow or infrastructure topology diagram.

  3. **Terminal Output Tab:** Streamed `stdout`/`stderr` terminal logs.

* **Action Header:** Quick buttons for `Run Script`, `Copy Code`, `Save to Registry`, and `Close Panel`.

## 4. Key Component Contracts

### 1. `ToolCallCard.vue` (Inline Conversational Component)

```
<script setup lang="ts">
import { LucideTerminal } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'

interface ToolCallProps {
  toolName: string
  status: 'pending' | 'running' | 'success' | 'failed'
  params: Record<string, any>
  artifactId?: string
}

defineProps<ToolCallProps>()
const emit = defineEmits<{
  (e: 'openArtifact', id: string): void
}>()
</script>

<template>
  <div class="rounded-lg border bg-card p-4 shadow-sm my-2 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <LucideTerminal class="h-5 w-5 text-muted-foreground" />
      <div>
        <div class="font-mono text-sm font-medium">{{ toolName }}</div>
        <div class="text-xs text-muted-foreground">Status: {{ status }}</div>
      </div>
    </div>
    <Button v-if="artifactId" size="sm" variant="outline" @click="emit('openArtifact', artifactId)">
      Inspect Output
    </Button>
  </div>
</template>

```

### 2. `ApprovalCard.vue` (Human-in-the-Loop Component)

```
<script setup lang="ts">
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

interface ApprovalProps {
  actionTitle: string
  commandPreview: string
  riskLevel: 'low' | 'medium' | 'high'
}

defineProps<ApprovalProps>()
const emit = defineEmits<{
  (e: 'approve'): void
  (e: 'reject'): void
}>()
</script>

<template>
  <div class="rounded-lg border border-warning/50 bg-warning/5 p-4 my-3">
    <div class="flex items-center justify-between mb-2">
      <span class="text-sm font-semibold text-warning-foreground">Approval Required</span>
      <Badge :variant="riskLevel === 'high' ? 'destructive' : 'outline'">{{ riskLevel }} risk</Badge>
    </div>
    <p class="text-sm mb-2">{{ actionTitle }}</p>
    <pre class="bg-muted p-2 rounded text-xs font-mono mb-3 overflow-x-auto">{{ commandPreview }}</pre>
    <div class="flex justify-end gap-2">
      <Button size="sm" variant="ghost" @click="emit('reject')">Reject</Button>
      <Button size="sm" variant="default" @click="emit('approve')">Approve & Run</Button>
    </div>
  </div>
</template>

```

## 5. State Management & Three-Way Sync Strategy (Pinia)

Manage state across chat, artifacts, and tools using a unified Pinia store (`useAgentStore.ts`):

```
import { defineStore } from 'pinia'

export interface Artifact {
  id: string
  title: string
  type: 'code' | 'yaml' | 'diagram' | 'log'
  content: string
  language?: string
}

export interface ChatMessage {
  id: string
  sender: 'user' | 'agent'
  content: string
  timestamp: string
  reasoningSteps?: string[]
  toolCalls?: Array<{
    id: string
    toolName: string
    status: 'pending' | 'running' | 'success' | 'failed'
    artifactId?: string
  }>
}

export const useAgentStore = defineStore('agent', {
  state: () => ({
    messages: [] as ChatMessage[],
    activeArtifact: null as Artifact | null,
    isInspectorOpen: false,
    agentState: 'idle' as 'idle' | 'reasoning' | 'awaiting_approval' | 'executing'
  }),
  actions: {
    openArtifact(artifact: Artifact) {
      this.activeArtifact = artifact
      this.isInspectorOpen = true
    },
    closeInspector() {
      this.isInspectorOpen = false
    },
    updateArtifactContent(newContent: string) {
      if (this.activeArtifact) {
        this.activeArtifact.content = newContent
        this.syncArtifactWithChat(this.activeArtifact.id, newContent)
      }
    },
    syncArtifactWithChat(artifactId: string, content: string) {
      // Logic for Three-Way Sync: Update relevant chat references & diagrams
    }
  }
})

```

## 6. Project Directory Structure

```
src/
├── components/
│   ├── agent/
│   │   ├── ChatStream.vue
│   │   ├── ChatInput.vue
│   │   ├── ReasoningBlock.vue
│   │   ├── ToolCallCard.vue
│   │   └── ApprovalCard.vue
│   ├── inspector/
│   │   ├── ArtifactInspector.vue
│   │   ├── CodeEditorTab.vue
│   │   └── TerminalLogTab.vue
│   └── layout/
│       ├── AppHeader.vue
│       └── AppSidebar.vue
├── stores/
│   └── useAgentStore.ts
└── views/
    └── AgentWorkspaceView.vue

```

## 7. Guidelines for AI Coding Agents (Vibe Coding Rules)

When implementing this application:

1. **Strictly Preserve Base UI Tokens:** Utilize `shadcn-vue` CSS variables (`--background`, `--foreground`, `--card`, `--border`, `--accent`) to maintain seamless light/dark mode support.

2. **Non-Blocking UI:** Never allow agent execution or streaming text to freeze the UI. All long-running tasks must render progressive loading states or spinners.

3. **Keyboard Accessibility:** Ensure global hotkeys (`Cmd+K` for search, `Esc` to close the inspector drawer, `Enter` to submit chat) are registered globally.

4. **Follow Vue 3 Best Practices:** Always use standard `<script setup lang="ts">` Single File Components with typed props and events.