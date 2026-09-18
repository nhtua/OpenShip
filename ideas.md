# OpenShip — Ideas

Recorded ideas, features, and improvements for OpenShip.

---

## Prime Principle: Co-Pilot, Not Replacement

OpenShip is designed as a **co-pilot and enhanced automation tool** for DevOps and platform engineers — **not as a replacement** for them.

**Implications:**

- Engineers remain in control throughout the workflow.
- The entry point is a requirements document because engineers understand their systems and can articulate what they need.
- Automation augments human expertise; it does not substitute for it.
- All design decisions should flow from this principle.

This is the foundational decision that guides all subsequent application design choices.

## Project Naming & Vision

**Name:** OpenShip

**Inspiration:** The name draws from the ocean/nautical theme, aligning with the broader DevOps and container ecosystem (Docker, Kubernetes, etc.). Just as ships carry cargo across oceans, OpenShip is designed to carry development and platform teams through their engineering workflows.

**Target Audience:** Developer engineers and platform engineers — OpenShip aims to function as a collaborative "team" or co-pilot for both roles.

**Multiple Meanings:** The name carries layered significance:

- **Ocean theme:** Connects to the DevOps/container ecosystem (Docker, Kubernetes).
- **"Ship" as delivery:** In software, "ship" means to release or deliver a product. OpenShip helps teams ship products to production.
- **"Open":** Signifies the project is open source, inviting community contribution and collaboration.

---

## Core Functionality: Agentic Infrastructure Automation

OpenShip is an agentic tool designed to assist DevOps and platform engineers through a structured workflow:

1. **Requirements Capture:** Engineers write a document describing the expected system — its architecture, components, and desired behavior.
2. **Design/Plan Generation:** OpenShip's agentic AI processes the requirements and generates a detailed design or implementation plan.
3. **Validation:** The generated design/plan is validated for correctness, feasibility, and alignment with the original requirements.
4. **Implementation:** Once the engineer reviews and approves the plan, OpenShip executes it to build the exact infrastructure or system described.

**Key Principle:** Human-in-the-loop — engineers retain control at critical decision points (especially plan approval), while OpenShip handles the heavy lifting of design generation and execution.

---

## Interface & Data Flow: Document-Driven, Visual Workflow

OpenShip uses a document-driven approach as its primary interface, with chat/conversation as a secondary, complementary feature:

**Entry Point — Markdown Document:**

- Engineers start by creating/writing a markdown document describing their requirements, architecture ideas, and system expectations.
- This mocked-out document serves as the starting point for the entire workflow.

**Visualization via Diagrams:**

- Instead of chat, OpenShip generates diagrams to visualize the expected system design.
- Diagrams show relationships between components: Terraform modules, automation components, cloud resources, and deployment topology.
- Engineers use diagrams to validate component relationships and infrastructure structure.

**Three-Document Workflow:**

1. **Markdown:** Records engineer requirements and design ideas.
2. **Diagrams:** Visualize component relationships and deployment architecture.
3. **Generated Code:** Terraform, Python scripts, and other automation artifacts.

**Validation Loop:** The markdown document and diagrams are used together to generate and verify the correctness of the Terraform and Python code before execution.

**Chat/Conversation Interface:**

- Chat is included but is not the primary workflow.
- Purpose: discussion, brainstorming, and adjusting ideas on the document.
- Rationale: Users are accustomed to AI conversation interfaces, so chat provides a familiar way to interact and refine requirements.
- Positioning: Secondary to the document-driven workflow, but essential for iterative refinement.

---

## Platform-Agnostic, API-Driven Automation

OpenShip is designed to work across all major cloud platforms and services:

**Supported Targets:**

- **Cloud infrastructure:** AWS, GCP, Azure, and other Terraform-supported providers.
- **Container orchestration:** Kubernetes, AWS ECS (Elastic Container Service).
- **CI/CD:** GitHub Actions, and other pipeline systems.
- **Any API-accessible platform:** Where direct API calls can be made.

**How It Works:**

- Engineers describe what they want to build in their markdown document.
- OpenShip's agentic system generates the appropriate code (Terraform, YAML, shell scripts, etc.) for the target platform.
- The agent applies the generated code, deploying the infrastructure or service.

**Permission Model:**

- The agent operates with permissions equivalent to the engineer — it acts as the engineer's proxy to the cloud platforms and APIs.
- Engineers retain responsibility for granting appropriate access to the agent.

---

## Abstraction Layer: Standardized Infrastructure Objects

To support wide platform coverage, OpenShip requires an abstraction layer that standardizes common infrastructure concepts:

**Standardized Objects:**

- Job
- Load balancer
- Deployment
- Storage
- (And other common infrastructure primitives)

**How It Works:**

- Define shared, platform-agnostic object types and contracts.
- Each object type has multiple possible implementations (Terraform, Python SDK, Node.js, direct API calls).
- The abstraction enables tools, workflows, and agents to understand and verify each other regardless of the underlying platform.

**Benefits:**

- **Visualization:** Components can be represented consistently in diagrams.
- **Traceability:** Abstract components connect directly to their implementation code (Terraform, Python, etc.).
- **Interoperability:** All tools and agents share a common understanding, enabling seamless collaboration across platforms.

**Goal:** A universal contract/definition that all tools, workflows, and agents understand — no matter which cloud platform or implementation technology is used.

---

## Resource Identity Tracking

When the agent creates concrete cloud resources from abstract objects, it must maintain a persistent mapping between the two so engineers can later reference, modify, or delete specific resources.

**Core Concept:** Every abstract object (job, deployment, load balancer) created by the agent is assigned a unique OpenShip identifier. When the corresponding concrete resource is provisioned, the mapping is recorded: `openship://job-abc123 → aws_ecs_service.my-service`.

**Key Capabilities:**
- **Traceability:** Engineers can see which cloud resources were created by which OpenShip objects
- **Targeted operations:** The agent can find and modify specific resources without ambiguity
- **Cross-platform consistency:** Same tracking mechanism works whether resources are on AWS, GCP, Azure, or elsewhere
- **Versioned history:** Mappings are tracked in Git, enabling rollback and change history

**Implementation:** See [architects.md](./architects.md) for detailed architecture including state management, tagging conventions, and execution context handling.

---

## Terminology: Workflow vs. Job

**Workflow (OpenShip's Process):**

- The main flow OpenShip uses to build cloud platforms for engineers.
- Includes: documentation → diagram → code generation → validation → apply → testing.
- This is OpenShip's internal orchestration process.

**Job (CI/CD Tasks):**

- Tasks like GitHub Actions, CircleCI, or similar CI/CD pipelines.
- Represents a set of work that needs to be done on the user's side.
- Users can create jobs as discrete units of automation.

This distinction avoids ambiguity between OpenShip's orchestration process and the automation tasks it generates or manages.

---

## Workflow Templates as First-Class Objects

OpenShip supports multiple workflow templates, each modeling a distinct DevOps problem pattern as a state machine. The document-driven build flow (requirements → design → plan → execute) is one template; other templates handle different problem types.

**Workflow Templates:**

- **Provision/Build:** Linear, deterministic flow — requirements → design → validate → implement. The primary template described in existing ideas.
- **Investigation/Debug:** Non-linear, exploratory — observe symptom → gather telemetry → hypothesize → test hypothesis → fix. Multiple branches and loop-backs.
- **CI/CD Creation:** Iterative with testing — define job → generate code → test run → debug → validate.
- **Additional patterns:** Optimization, migration, cost analysis, and more as needed.

**Template Selection:**

- Engineer selects or describes the task type.
- OpenShip loads the appropriate workflow template (state machine graph).
- Same agent core, same tools, same abstraction layer — different orchestration logic per problem type.

**Workflow Inputs/Outputs:**

- Each workflow template has optional inputs (parameters) and outputs (results).
- Inputs may include: target environment, scope, constraints, existing artifacts.
- Outputs may include: generated code, execution results, reports, recommendations.

**Workflow Composition:**

- Workflows can invoke other workflows as sub-agents to complete dependent tasks.
- A primary workflow delegates specific steps to specialized sub-workflows, receives results, and continues.
- Example: An investigation workflow may invoke a build workflow to test a proposed fix.
- This enables building complex automation from reusable, simpler building blocks.

**Benefits:**

- **Reusability:** Workflow templates can be shared, versioned, and reused across projects.
- **Extensibility:** New problem types are addressed by adding new templates, not rebuilding the agent.
- **Modularity:** Complex tasks decompose into manageable, testable workflow units.
- **Consistency:** All workflows operate on the same abstraction layer, ensuring interoperability.

---

## Workflow Types & Sharing Model

OpenShip workflows fall into two categories with distinct provenance, UI treatment, and lifecycle.

**Built-in Workflows:**

- Provided and maintained by the OpenShip author/team.
- Well-tested and highly integrated with the OpenShip UI.
- Accessible via dedicated main UI menu items, not just the workflow list.
- Represent core, battle-tested workflow patterns (e.g., provision/build, investigation/debug).

**Custom Workflows:**

- User-defined workflows that follow the general agent UI.
- Can be described and customized to run any automation task.
- Created by users for specific, specialized needs.
- Not integrated into the main UI menu; accessible from the workflow list.

**Workflow Sharing & Registry:**

- Custom workflows can be shared with the broader OpenShip community.
- The workflow registry is internal to the OpenShip repository.
- Shared workflows are contributed via Pull Request, delivered alongside OpenShip releases.
- Before a shared workflow is merged and available to all users, it must pass:
  - **Security scanning:** Automated checks for vulnerabilities, excessive permissions, and unsafe operations.
  - **Peer review:** Manual review by maintainers or community contributors to validate correctness, documentation, and design.
- This model ensures quality, security, and maintainability of shared workflows while keeping the submission process transparent and collaborative.

**Benefits:**

- **Trust:** Built-in workflows provide a reliable baseline for core use cases.
- **Flexibility:** Custom workflows enable users to solve unique problems.
- **Quality control:** The PR-based sharing model with security scanning and peer review maintains a high standard for shared workflows.
- **Community growth:** Users can contribute back to the ecosystem, fostering collaboration and reusability.

---

## Home Page: Chat-First Entry

When users first open OpenShip, the default home page provides a familiar, chat-first experience with workflow visibility.

**Primary Entry — Chat Input:**

- A prominent text input field with a friendly placeholder: "What are you going to do?"
- User types a natural language query describing their task.
- The query initiates agent chat and automatically routes to the appropriate workflow based on the query content.
- This provides the lowest-friction entry point — users can start immediately without navigating menus or selecting templates.

**Workflow Discovery — Template List:**

- Below the chat input, a curated list of available workflow templates is displayed.
- Users can browse and click on a workflow template to start it directly.
- This serves users who prefer explicit template selection over chat-based routing.
- The list may include built-in workflows prominently, with custom/shared workflows accessible through filtering or secondary navigation.

**Transition Model — In-Place Evolution:**

- The home page does not redirect; it evolves in-place into the selected or matched workflow interface.
- When a user submits a chat query or selects a workflow template, the page smoothly transforms into that workflow's UI.
- The chat input area transitions into the active conversation for that workflow.
- This avoids jarring navigation and maintains context — the home page becomes the workflow session.

**Relationship to Workflow Templates:**

- The home page is a router, not a workflow itself. It helps users discover and initiate the appropriate workflow.
- Once routed, the specific workflow (e.g., the document-driven infrastructure build workflow with requirements and diagrams) proceeds as defined by that template.
- If no matching workflow is found from the chat query, a general custom workflow is started.
- This does not conflict with or replace specialized workflows like the infra build flow; it simply provides the entry point.

**Design Goals:**

- **Familiarity:** The chat input mirrors the familiar AI chat interface users already know.
- **Dual entry paths:** Supports both conversational (chat) and structured (template selection) workflows.
- **Discovery:** Surfaces available workflows so users learn what OpenShip can do.
- **Immediate action:** Users can start working within seconds of opening the application.

---

## New Project UI: Simple Entry Points

The new project page offers two big, simple buttons:
- **[Describe your project]** — Start with a text-based requirements document.
- **[Build with Diagram]** — Start with a visual diagram.

**Diagram Entry Flow:**
- Users begin with basic shapes (square, diamond, circle, oval) and simple connectors (lines, arrows).
- They write component names or step descriptions inside shapes — no need to select specialized components.
- This creates a "drafting diagram" (version 0).
- User clicks **[Review]** to send the draft to the agent.
- The agent assesses the draft and regenerates a proper diagram (version 1) with correct component visualization.

**Diagram-to-Spec Sync:** While generating the polished diagram (version 1), the agent simultaneously creates or updates a description/specification document. The diagram and spec stay in sync throughout the workflow.

**Benefit:** Lowers the barrier to entry — users can quickly express their ideas without learning the diagram toolbox or selecting the correct component types.

---

## UX: Document-First with Inline Conversation

OpenShip inverts the traditional chat-bot UX pattern:

**Traditional Pattern (ChatGPT, Gemini):** Conversation first → document generated as a side artifact.

**OpenShip Pattern:** Document first → conversation as an overlay for refinement.

**Workflow:**

1. **Write first:** User creates the requirement/description document upfront (with optional templates).
2. **Select & chat:** User selects any block (or the entire document) to initiate a side conversation for adjustments.
3. **Diagram interaction:** The same pattern applies to generated diagrams — select any component or the entire diagram to chat and make adjustments.

**Three-Way Sync:**

- **Document ↔ Diagram ↔ Conversation** are always in sync.
- Any adjustment made in conversation is automatically synced and logged across all related documents.
- No document becomes stale — all three views remain consistent at all times.

**Key Principle:** The document and diagram are the source of truth; conversation is the mechanism for iterative refinement.

---

## Keyboard Shortcuts & Key Bindings

OpenShip should include comprehensive hotkeys and key bindings for efficient, low-fatigue interaction.

**Inspiration:**

- VIM motion-style navigation
- Vimium browser extension (keyboard-driven workflow)

**Purpose:**

- Enable power users to navigate, edit, and interact with OpenShip quickly.
- Reduce reliance on mouse interactions to minimize fatigue during extended sessions.
- Support a keyboard-first workflow for engineers accustomed to terminal-based tools.

---

## Feature Hierarchy (High-Level to Detailed)

The main features of OpenShip, ordered from high-level to detailed:

1. **Project** — Top-level container for all work.
2. **Documents** — Requirements, design, and specification documents.
3. **Diagram** — Visual representations of system architecture and component relationships.
4. **Plan** — Generated implementation plans from requirements and design.
5. **Progress** — Tracking of implementation status and workflow progress.
6. **Test/Validation** — Verification and validation of generated plans and implementations.
7. **Monitoring/Logging** — Observability, log management, and system health tracking.
8. **Cost Analysis** — Infrastructure cost tracking, optimization, and budget management.

*Note: This hierarchy is preliminary and subject to refinement as the design evolves.*

---

## Git Repository Integration

OpenShip requires comprehensive Git integration at multiple levels:

**Project Versioning:**
- Each OpenShip project is stored in a Git repository.
- Version tracking for all project artifacts: documentation, diagrams, agent-generated code, and configuration changes.
- Enables rollback, history inspection, and change management.

**External Repository Connectivity:**
- The OpenShip agent platform connects to and works with external Git repositories.
- Agents can check out application code to inspect, modify, or automate.
- Agents can add automation (e.g., GitHub Actions workflows) to existing projects.
- Agents can interact with applications, trigger builds, and work on existing codebases.

**Use Cases:**
- Track all changes made by the agent over time.
- Collaborate on projects with version history and branching.
- Integrate with existing development workflows and CI/CD pipelines.
- Manage multiple projects with separate version histories.
