---
type: story
summary: Collected ideas, features, and design principles for OpenShip as a co-pilot for DevOps and platform engineers.
status: in-progress
date: 2026-09-20
---

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

## Core Functionality: Agentic Workflow Orchestration

OpenShip is an agentic tool that orchestrates workflows for DevOps and platform engineers. The system provides a general workflow execution model that supports multiple workflow templates, each tailored to a specific DevOps task pattern.

**Example Workflow: Infrastructure Automation (Provision/Build Template)**

One of OpenShip's built-in workflow templates is the infrastructure automation workflow:

1. **Requirements Capture:** Engineers write a document describing the expected system — its architecture, components, and desired behavior.
2. **Design/Plan Generation:** OpenShip's agentic AI processes the requirements and generates a detailed design or implementation plan.
3. **Validation:** The generated design/plan is validated for correctness, feasibility, and alignment with the original requirements.
4. **Implementation:** Once the engineer reviews and approves the plan, OpenShip executes it to build the exact infrastructure or system described.

**Key Principle:** Human-in-the-loop — engineers retain control at critical decision points (especially plan approval), while OpenShip handles the heavy lifting of design generation and execution.

This is just one example workflow template. Other templates include investigation/debug, CI/CD creation, and user-defined custom workflows.

---

## Interface & Data Flow: Document-Driven, Visual Workflow (Infrastructure Template)

The infrastructure automation workflow template uses a document-driven approach as its primary interface, with chat/conversation as a secondary, complementary feature:

**Entry Point — Markdown Document:**

- Engineers start by creating/writing a markdown document describing their requirements, architecture ideas, and system expectations.
- This mocked-out document serves as the starting point for the workflow.

**Visualization via Diagrams:**

- OpenShip generates diagrams to visualize the expected system design.
- Diagrams show relationships between components: Terraform modules, automation components, cloud resources, and deployment topology.
- Engineers use diagrams to validate component relationships and infrastructure structure.

**Three-Artifact Workflow:**

1. **Markdown:** Records engineer requirements and design ideas.
2. **Diagrams:** Visualize component relationships and deployment architecture.
3. **Generated Code:** Terraform, Python scripts, and other automation artifacts.

**Validation Loop:** The markdown document and diagrams are used together to generate and verify the correctness of the Terraform and Python code before execution.

**Chat/Conversation Interface:**

- Chat is the primary interface for interacting with workflows.
- Purpose: discussion, brainstorming, input adjustment, and iterative refinement.
- Rationale: Users are accustomed to AI conversation interfaces, and chat provides a natural, low-friction way to configure and control workflow execution.
- The agent is highly chat-centric — users configure workflow inputs, adjust parameters, and provide feedback through conversation.

Other workflow templates may use different interfaces. For example, an investigation/debug workflow might primarily use chat and telemetry dashboards, while a CI/CD creation workflow might use a form-based interface for pipeline configuration.

---

## Platform-Agnostic, API-Driven Automation

OpenShip workflows interact with external platforms and services through a connector layer:

**Supported Targets:**

- **Cloud infrastructure:** AWS, GCP, Azure, and other Terraform-supported providers.
- **Container orchestration:** Kubernetes, AWS ECS (Elastic Container Service).
- **CI/CD:** GitHub Actions, and other pipeline systems.
- **Any API-accessible platform:** Where direct API calls can be made.

**How It Works:**

- Workflows reference connectors and tools that interact with external platforms.
- The agent uses these connectors to execute operations on the target platforms.
- For infrastructure workflows, the agent generates and applies the appropriate code (Terraform, YAML, shell scripts, etc.).
- For other workflows, the agent directly invokes API calls through connectors.

**Permission Model:**

- The agent operates with permissions equivalent to the engineer — it acts as the engineer's proxy to the cloud platforms and APIs.
- Engineers retain responsibility for granting appropriate access to the agent.

---

## Abstraction Layer: Standardized Infrastructure Objects

To support wide platform coverage, OpenShip provides an abstraction layer that standardizes common infrastructure concepts:

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

**Chat Integration:**

- Users and agents can mention and refer to abstract objects directly in chat conversations (e.g., "scale the web deployment to 3 instances," "show me the status of the payment service load balancer").
- The agent resolves these references to the corresponding cloud resources and executes the requested operations.
- This enables natural, conversational interaction with infrastructure without requiring users to know platform-specific resource identifiers.

**UI Integration:**

- The abstraction layer enables the OpenShip UI to display and manage resources consistently across platforms.
- Users can browse resources through dedicated UI views (e.g., Projects → Categories → Resources) or through chat conversations.
- Related resources are grouped and displayed together, making it easier to understand system topology and dependencies.

**Benefits:**

- **Visualization:** Components can be represented consistently in diagrams.
- **Traceability:** Abstract components connect directly to their implementation code (Terraform, Python, etc.).
- **Interoperability:** All tools and agents share a common understanding, enabling seamless collaboration across platforms.
- **Conversational interaction:** Users can interact with infrastructure using natural language in chat.
- **Unified resource management:** Users can browse, manage, and understand resources through a consistent UI regardless of the underlying platform.

**Goal:** A universal contract/definition that all tools, workflows, and agents understand — no matter which cloud platform or implementation technology is used.

---

## Resource Identity Tracking

When the agent creates concrete cloud resources from abstract objects, it maintains a persistent mapping between the two so engineers can later reference, modify, or delete specific resources.

**See:** [architects.md](./architects.md) for detailed architecture including the dual-state model, identity model, state management, tagging conventions, and execution context handling.

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
- **Alert Response:** Triggered by monitoring alarms — receive alert → gather context → classify severity → execute remediation or escalate → notify.
- **Scheduled Reporting:** Cron-triggered — pull metrics/data → aggregate and analyze → generate dashboard report → distribute to stakeholders.
- **Cost Optimization:** Analyze resource usage → identify waste → propose right-sizing → approve and apply changes → validate savings.
- **Capacity Planning:** Monitor growth trends → forecast demand → recommend scaling → simulate impact → implement.
- **Security Scanning:** Run vulnerability scans → correlate findings → prioritize by risk → generate remediation plan → track closure.
- **Incident Response:** Detect incident → blast radius assessment → coordinate response → implement fix → post-mortem analysis.
- **Migration:** Assess current state → plan migration steps → execute in phases → validate → decommission old systems.

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

**Workflow Execution Sessions:**

- Each workflow execution is a discrete session with its own execution context.
- The session tracks objects created, updated, and deleted during execution.
- Sessions are persisted to Git for continuity and enable immediate follow-up operations.
- A single project may have multiple concurrent or sequential workflow sessions.

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

## Project: Core Organizational Unit

A Project in OpenShip is a logical container that groups related workflow executions, associated resources, and tags together. Projects provide isolation, organization, and navigation for all OpenShip activities.

**What a Project Contains:**
- Workflow executions (runs, sessions, and their state)
- Referenced resources (cloud infrastructure, artifacts, generated code)
- Tags and metadata for organization and filtering
- Chat conversations associated with workflows
- Artifacts and outputs from workflow execution

**Purpose and Benefits:**
- **Isolation:** Separates different systems, environments, or teams' work from each other.
- **Organization:** Groups related workflows and resources for easier management.
- **Navigation:** Provides context for users and agents to find and manage related work.

**Execution Context:**
- Every workflow execution happens within a project context.
- The project provides scope for resource access, state persistence, and conversation history.
- Users and agents reference project resources by name within the project context (e.g., "deploy to staging in the web-platform project").

**Project Management:**
- Users can create, switch between, and manage multiple projects.
- Projects can be shared within teams, with appropriate access controls.
- The agent is aware of the active project and uses it as context for all operations.

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

## Project UI: Workflow-Centric Entry Points

See [Project: Core Organizational Unit](#project-core-organizational-unit) for the definition of what a Project is.

The project page offers simple entry points for starting work:

- **Chat input:** Users can type a natural language query to start a workflow or interact with the agent.
- **Workflow template list:** Users can browse and select available workflow templates.
- **Resource browser:** Users can browse resources associated with the project (workflows, executions, artifacts, etc.).

---

## UX: Chat-Centric with Browser-Based Tool Integration

OpenShip uses a chat-centric interface where conversation is the primary way to interact with workflows and the agent. The agent has access to two types of tools:

**Backend Tools (Agent Process):**

- Tools that execute in the agent's backend process (e.g., cloud API calls, code generation, file operations, sandbox management).
- These tools perform the actual work of workflow execution.

**Frontend Tools (Browser UI):**

- Tools that run in the conversation UI on the user's browser (e.g., document display/editing, diagram rendering, resource browsing).
- These tools provide rich, interactive experiences for viewing and manipulating workflow artifacts.

**Document and Diagram Support as Frontend Tools:**

- Documents and diagrams are generated as artifacts of workflow execution by backend tools.
- Frontend tools display and allow users to interact with these artifacts (e.g., "show me the architecture diagram," "edit the requirements document").
- Users can reference and interact with artifacts through chat, and the agent invokes the appropriate frontend tool to display or edit them.

**Chat as Primary Interface:**

- Users interact with the agent through natural language conversation.
- The agent understands workflow commands, resource references, and task descriptions.
- Chat is used for workflow configuration, input adjustment, progress monitoring, and iterative refinement.
- The agent seamlessly invokes both backend and frontend tools as needed to fulfill user requests.

**Key Principle:** Conversation is the primary interface; backend tools perform the work, frontend tools provide rich interactive experiences for artifacts.

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

## Slack Integration (Optional)

OpenShip provides optional, highly integrated Slack connectivity for teams that prefer to monitor and interact through their existing communication channels.

**Capabilities via Slack:**

- **Monitor process:** View real-time status of running workflows from Slack.
- **Conversation:** Interact with the agent via Slack messages — ask questions, request changes, get updates.
- **Notifications:** Receive alerts on workflow completion, errors, or when approval is needed.
- **Approve actions:** Review and approve execution plans directly from Slack notifications or messages.

**Optional by Design:**

- Slack integration is entirely optional and configured per OpenShip instance.
- If not connected to an OpenShip instance, users continue to use the OpenShip UI without any Slack dependency.
- Provides flexibility: teams can choose their preferred interaction channel (UI, Slack, or both).

**Benefits:**

- **Reduced context switching:** Engineers can stay in Slack for monitoring and approvals without switching to the OpenShip UI.
- **Team collaboration:** Workflow status and decisions can be discussed in shared Slack channels.
- **Mobile-friendly:** Slack's mobile app enables monitoring and approvals on the go.
- **Familiar interface:** Leverages a tool engineers already use daily.

---

## Feature Hierarchy (High-Level to Detailed)

The main features of OpenShip, ordered from high-level to detailed:

1. **Project** — Logical container for workflow executions and referenced resources.
2. **Workflows** — Workflow templates and executions that perform automation tasks.
3. **Workflow Executions** — Individual runs of workflows, including chat conversations, state, and artifacts.
4. **Resources** — Cloud resources, infrastructure objects, and artifacts managed by workflows.
5. **Tools/Connectors** — Available tools and connectors that workflows can use.
6. **Registry** — Central repository for verified workflows, tools, and connectors.

This hierarchy reflects the workflow-centric architecture where projects organize workflow activity, workflows perform the automation, and tools/connectors enable interaction with external systems.



---

## Core Architecture: Everything is a Workflow

All OpenShip operations — built-in features, custom automations, and user-defined tasks — are expressed as workflows executed at runtime. There is no distinction between "the product" and "user workflows"; everything follows the same execution model.

**Key Principles:**

- **Unified execution model:** Built-in capabilities (provisioning, debugging, CI/CD) and user-created workflows share the same runtime, tooling, and orchestration infrastructure.
- **Chat-driven configuration:** Users interact with workflows through conversation to adjust inputs, parameters, and step behavior. Chat is the mechanism for workflow configuration, not a separate feature.
- **Dynamic step control:** Workflow steps can loop, halt for human input, or wait for approval before proceeding to the next step. This enables true human-in-the-loop automation where the agent pauses at critical decision points.
- **Runtime configuration:** Workflows are defined by their configuration (steps, tools, inputs, outputs) rather than fixed code paths. The same workflow template can execute differently based on runtime inputs.

**Implications:**

- The agent orchestrates all workflows using the same core engine.
- Users can create, modify, and compose workflows without writing code — through chat and configuration.
- Workflow execution is transparent and inspectable at every step.

---

## Workflow Building as an Agentic Process

Building a new workflow is itself an agentic task where users collaborate with the agent to discover, select, and compose available tools and connectors into a multi-step workflow.

**How It Works:**

- User describes the desired outcome or process through chat.
- The agent helps discover available tools, connectors, and existing workflow patterns that can achieve the goal.
- Together, they compose these components into a coherent multi-step workflow.
- The resulting workflow follows the same structure and execution model as built-in templates.

**Tool and Connector Discovery:**

- The agent maintains awareness of all registered tools, connectors, and workflow templates.
- When building a workflow, the agent suggests relevant tools based on the task context.
- Users can browse, search, and filter available capabilities to find what they need.
- The discovery process is conversational — the agent explains options, trade-offs, and compatibility.

**Benefits:**

- **Lower barrier to entry:** Users don't need to know all available tools upfront; the agent guides them.
- **Composability:** Complex automations are built from simpler, well-understood components.
- **Iterative refinement:** Users can adjust and re-compose workflows through ongoing conversation.

---

## Connectors, Registry, and Workflow Safety

For workflows to execute successfully, the agent requires reliable connectivity to external services and a curated registry of verified tools and workflows.

### Connectors and Authentication

OpenShip implements a connector layer that abstracts connectivity to various platforms and services:

**Supported Connectors (non-exhaustive):**
- Cloud providers: AWS, GCP, Azure
- Development platforms: GitHub, GitLab, Bitbucket
- Project management: Jira, Trello, Asana
- Communication: Slack, Microsoft Teams
- And any API-accessible service

**Authentication Model:**
- Connectors support multiple authentication methods (API keys, OAuth, service accounts, etc.).
- Authentication credentials are managed separately from workflow definitions.
- Users configure connectors once; workflows reference connectors by name rather than embedding credentials.
- The agent can guide users through connector setup and authentication during workflow execution.

**Runtime Connector Resolution:**
- When a workflow references a tool or service, the agent checks for an available, authenticated connector.
- If no connector is available, the agent pauses at that step and guides the user through connecting the required service.
- This enables progressive setup — users can begin workflows and complete connector configuration as needed.

### Workflow and Tool Registry

OpenShip maintains a registry of verified workflows, tools, and skills that the agent can discover and use.

**Registry Properties:**
- **Code-first:** The registry is maintained as part of the OpenShip codebase, versioned with Git.
- **Community-reviewed:** All additions to the registry must pass through the pull request process with security scanning and peer review.
- **Agent-discoverable:** The agent can query the registry to discover available workflows, tools, and connectors.
- **Version-managed:** The agent can check for and pull updates from the registry to stay current with new or improved workflows and tools.

**Two Methods for Workflow Availability:**

**1. Built-in Workflows (PR to OpenShip repo):**
- Workflows added via the standard pull request process.
- Undergo security scanning and peer review.
- Available to all users immediately after merge.
- Considered verified, tested, and officially supported.

**2. Imported Workflows (from URL):**
- Users can import custom workflows from external URLs.
- OpenShip performs two verification checks before allowing execution:
  - **Harmful instruction analysis:** LLM-based analysis to detect potentially dangerous or malicious workflow steps.
  - **Tool compatibility check:** Verification that the workflow's required tools and connectors are available and compatible with the current OpenShip version.
- If verification cannot be completed (e.g., tool compatibility unknown), OpenShip displays a red alert warning the user but still allows them to proceed.
- Imported workflows are not added to the registry; they remain user-specific and unverified by the community.

**Safety Philosophy:**
- The registry provides a curated, safe baseline of verified workflows and tools.
- User-created and imported workflows offer flexibility with appropriate warnings and verification.
- The agent always has the ability to pause, warn, and request approval — safety is layered, not absolute.

---

## Workflow Compilation: Markdown to LangGraph

When a workflow is created or updated, the agent parses the markdown definition and compiles it into an executable LangGraph. This compiled graph is cached for reuse across executions.

**Compilation Process:**
1. Agent reads the markdown workflow definition.
2. Queries the workflow registry for tool and connector definitions referenced in the workflow.
3. Builds LangGraph nodes for each step and edges for the execution flow.
4. Validates tool availability and compatibility.
5. Caches the compiled graph for future executions.

**Tool Resolution:**
- Tool references in workflows (e.g., `tool: jira.create_issue`) are resolved via registry lookup.
- The registry contains tool definitions, including implementation details, input/output schemas, and compatibility information.
- This enables the agent to discover and bind tools dynamically at compilation time.

**Benefits of Compilation:**
- **Durability:** LangGraph provides built-in checkpointing, HITL, and state management.
- **Performance:** Compiled graphs execute faster than re-parsing markdown on each run.
- **Consistency:** Compilation validates the workflow upfront, catching errors early.

**Recompilation Triggers:**
- Workflow definition is updated.
- Tool/connector versions change.
- Explicit recompilation requested by user.

## Sandbox Building

Each workflow execution runs in an isolated sandbox built from a base image with common DevOps tools pre-installed.

**Sandbox Base Image:**
- Pre-built image containing Terraform, AWS CLI, GCP SDK, Azure CLI, kubectl, and other common tools.
- Reduces sandbox build time and ensures consistent tool availability.
- Updated periodically to include tool updates and security patches.

**Sandbox Construction:**
- Built on-the-fly for each workflow execution.
- Workflow-specific tools are installed if not present in the base image.
- Credentials/secrets are loaded from Vault and passed as environment variables.
- Sandbox is destroyed after workflow completion (or timeout).

**Working Directory:**
- Each workflow execution has a working directory on a shared volume mounted into the sandbox.
- This is where the workflow checks out code, generates files, and performs its operations.
- Shared volumes make it easier to debug and review workflow artifacts without needing to access the sandbox directly.
- Working directories are cleaned up on a configurable schedule (e.g., 3, 7, 15, 30, 90 days) to prevent disk space accumulation.
- Artifacts that need to persist beyond the cleanup schedule must be explicitly committed (e.g., to Git).

**Benefits:**
- **Isolation:** Workflow failures don't affect other executions or the host system.
- **Security:** Sandboxed execution limits the blast radius of errors or malicious workflows.
- **Consistency:** Same tools and versions across all executions.

## Workflow Execution Model

Workflows execute with durable state persistence, supporting pauses of any duration — from seconds to days.

**Discovery and Selection:**
- **Chat-based suggestions:** User describes their task, and the agent suggests matching workflows.
- **Browse/search interface:** Users can browse the workflow catalog or search for specific workflows.
- Both approaches complement each other — chat for natural language discovery, browse/search for explicit exploration.

**Storage:**
- **Built-in workflows:** Merged into the OpenShip code repository via the PR process.
- **Custom workflows:** Created by users, stored in a separate location (database or dedicated directory — decision deferred).
- This separation enables clear distinction between officially supported and user-defined workflows.

**Versioning:**
- Workflow definitions use semantic versioning (e.g., 1.0.0, 1.1.0, 2.0.0).
- Explicit version numbers enable clear compatibility checks, dependency management, and rollback.
- Running workflow instances continue using the version they started with, even if the definition is updated.

**Triggering:**
- **Manual:** User explicitly starts a workflow through chat or the UI.
- **Scheduled:** Workflows run on a schedule (e.g., daily cost report, weekly cleanup).
- **Event-driven:** Workflows trigger on external events (webhook, file change, API call, etc.).
- This enables reactive automation where the agent responds to real-world events.

---

## Event-Driven Workflow Triggers

OpenShip supports event-driven execution where workflows are automatically triggered by various event sources, enabling reactive automation that responds to real-world signals without manual initiation.

**Trigger Event Types:**

- **Manual:** User explicitly triggers a workflow through chat or the UI.
- **Scheduled (Cron):** Workflows run on a time-based schedule (e.g., daily cost report, weekly resource cleanup, monthly compliance check).
- **Webhook:** HTTP POST endpoints that external systems can call to trigger workflows. Each workflow can expose a unique webhook URL with optional authentication (API key, JWT, or HMAC signature).
- **Slack Messages:** Commands or mentions in Slack channels or direct messages trigger workflows (e.g., `/openship deploy --env staging`, "OpenShip, investigate this error" in #alerts).
- **Email:** Incoming emails matching configured rules trigger workflows (e.g., bug reports sent to bugs@example.com trigger a Jira ticket creation workflow, incident notifications trigger investigation workflows).
- **File Changes:** Workflows trigger when specific files or directories change (e.g., new config files pushed to a Git repo, changes to Terraform modules).
- **API Calls:** Direct REST API invocations trigger workflows with structured JSON payloads.
- **State Changes:** Workflows trigger when monitored resources change state (e.g., instance health check fails, deployment status changes, cost threshold exceeded).

**Trigger Configuration:**

- Triggers are defined in the workflow's metadata (YAML frontmatter).
- Each trigger specifies its type, configuration parameters, and optional input mapping.
- Workflows can have multiple triggers of different types.

**Input Mapping from Events:**

- Event data is mapped to workflow inputs through trigger configuration.
- Webhook payloads, Slack message content, email bodies, etc. are parsed and transformed into the workflow's expected input schema.
- Default values are applied for inputs not provided by the event.

**Trigger Authentication & Security:**

- Webhooks support authentication via API keys, JWT tokens, or HMAC signatures to prevent unauthorized triggers.
- Slack integration uses Slack's bot token and event subscription verification.
- Email triggers validate sender addresses or use IMAP authentication.
- All trigger endpoints support TLS/SSL for transport security.

**Benefits:**

- **Reactive automation:** Workflows respond immediately to real-world events without waiting for manual initiation.
- **Reduced toil:** Repetitive tasks are automated based on signals rather than schedules or manual triggers.
- **Faster incident response:** State changes and alerts can trigger investigation and remediation workflows automatically.
- **Integration with existing tools:** Teams can trigger workflows from tools they already use (Slack, email, CI/CD systems).

**Input Passing:**
- Workflows support both JSON payloads (for programmatic triggers) and chat inputs (for manual triggers).
- JSON payloads are validated against the workflow's input schema.
- Chat inputs are parsed and validated by the agent.
- This flexibility enables workflows to be triggered from diverse sources while maintaining input validation.

**State Persistence:**
- Workflow execution state is checkpointed at each step, enabling crash recovery and resume.
- The checkpoint includes the full conversation history, not just structured state (inputs/outputs/decisions). This preserves context and reasoning across pauses.
- When a workflow resumes, the agent has access to the complete prior conversation, ensuring continuity.

**Human-in-the-Loop Pausing:**
- **Explicit pause points:** Workflow creators specify critical decision points where the workflow must pause for human input or approval.
- **Agent-initiated pauses:** The agent can dynamically pause execution when uncertainty is high, when unexpected results occur, or when additional context is needed.
- Pauses can last any duration — seconds, minutes, hours, or days.
- The workflow remains persisted and resumable during the pause.
- Users can only intervene and provide feedback at pause points, not during active step execution. This simplifies the execution model and makes it more predictable.
- When a workflow pauses, the user is notified immediately through the configured notification channel.

**Notification Channels:**
- When a workflow pauses for human input, the user is notified through a configurable channel.
- Notification channel is specified per workflow (or per pause point) — options include in-app, Slack, email, or push notification.
- This enables flexible integration with existing team communication tools.

**Resume Mechanics:**
- User responds to the notification (e.g., approves a plan, provides input).
- The agent resumes the workflow from the checkpoint, with full context of prior conversation.
- The workflow continues execution from the paused step.

**Logging and Observability:**
- Workflow execution generates structured, machine-readable logs separate from the chat transcript.
- This enables debugging, monitoring, and observability without cluttering the user-facing conversation.
- Logs capture step execution details, tool invocations, errors, and performance metrics.

**Progress, Result, and Error Reporting:**
- The agent sends real-time chat updates as each step completes.
- This provides transparency into workflow execution and builds user confidence.
- Updates are conversational, describing what happened and what's happening next.
- When the workflow completes or errors, the agent sends a summary message with the final results or error details.
- Error notifications use the same notification channel as completion notifications, simplifying the notification model.
- Workflow outputs are provided as both a chat summary (for human consumption) and a structured result (for programmatic consumption).

**Secrets Management:**
- Workflows access secrets and credentials (API keys, passwords, tokens) by querying a secrets vault at runtime.
- This keeps secrets out of workflow definitions and enables centralized, secure secret management.
- Integrates with existing vault solutions (e.g., HashiCorp Vault).

**Execution Isolation:**
- Each workflow executes in its own isolated environment (sandbox) to prevent side effects on the system or other workflows.
- Sandboxing ensures that workflow failures don't affect other running workflows or the host system.
- This is particularly important for workflows that install dependencies, modify system state, or interact with external services.

**Workflow Concurrency:**
- Workflows define a `concurrent_group` in their metadata, similar to GitHub Actions.
- Workflows in the same concurrency group can run in parallel or are serialized based on the group's configuration.
- This enables managing concurrent workflow executions (e.g., preventing multiple deployments from running simultaneously).

**Workflow Completion:**
- A workflow is complete when it reaches the end of its instructions.
- The agent can also decide to stop execution based on step output or error conditions.
- This enables early termination when further steps are unnecessary or when a blocking issue is detected.

**Input Validation:**
- **Workflow inputs (on trigger):** Validated against a defined schema to ensure required parameters are provided and correctly formatted.
- **Step/tool inputs/outputs:** Validated by the agent based on context, since steps are flexibly defined in natural language.
- This balances structure (schema validation for entry points) with flexibility (agent-based validation for conversational steps).

**Direct Execution:**
- Workflows execute directly without a dry-run or preview mode.
- Users can inspect the workflow definition before starting it, but there is no separate test execution.
- This keeps the execution model simple and straightforward.

**Workflow Dependencies:**
- Workflows can declare explicit dependencies on other workflows (e.g., 'deploy' depends on 'build' completing first).
- The agent manages the dependency graph, ensuring workflows execute in the correct order.
- This enables building complex automation pipelines from simpler, specialized workflows.

**Workflow Composition:**
- Workflows can invoke other workflows as sub-agents within a step.
- This enables modular, reusable workflow design (e.g., a 'deploy' workflow calls a 'test' workflow).
- Sub-workflows execute with their own state and context, but report results back to the parent workflow.
- Enables building complex automations from simpler, specialized components.

**Dynamic Step Sequencing and Agent Reasoning:**
- Workflow definitions specify available steps and tools, but the agent dynamically determines the execution order at runtime.
- The agent adapts sequencing based on context, intermediate results, and errors — not rigidly following a predefined DAG.
- This enables flexible, context-aware execution where the agent can skip, repeat, or reorder steps as needed.
- When a step completes, the agent evaluates the results and decides which step to execute next, enabling intelligent, context-aware workflow execution.
- **Agent Reasoning:** The agent must continuously assess the execution context and reason about the next appropriate action. This requires the LLM to understand the workflow goals, current state, available options, and potential consequences of each action.

**State Persistence:**
- Workflow execution state is persisted in a database (SQLite for local, Postgres for multi-user deployments).
- This enables durable checkpoints that survive process restarts and system reboots.
- Conversation history and structured state are stored together for complete context on resume.
- When a workflow is paused, the full execution state is persisted, including step context, tool state, and conversation history, so the agent can resume seamlessly.

**Workflow Timeout and Resource Management:**
- Workflows have a global timeout (default 4 hours) for the entire execution, not per-step timeouts.
- After timeout, the execution process exits, but the checkpoint persists in storage.
- User can manually resume the workflow at the exact paused step by reopening the chat/session.
- This balances resource efficiency with user convenience.

**Post-Execution Cleanup:**
- Workflows automatically clean up resources after completion (e.g., remove temporary files, close connections).
- This ensures that workflow executions don't leave behind stale resources or consume unnecessary system memory.

**Error Handling and Rollback:**
- Workflow creators can define custom error handling for specific steps (e.g., retry logic, fallback behavior).
- If no custom handling is defined, retries follow OpenShip's error type definitions (e.g., transient errors retry, permanent errors pause for user input).
- Rollback behavior is defined by the workflow — some workflows may automatically roll back on failure, others may require manual intervention.
- When a tool fails, the agent receives a structured error object containing error type, message, and context for effective error handling.
- This enables flexible, context-aware error management while maintaining sensible defaults.

**Relationship to Technology Stack:**
- This execution model aligns with LangGraph's checkpointing and `interrupt()` patterns, which provide first-class support for durable execution and human-in-the-loop workflows.

Workflows are defined as conversational documents with structured annotations that the agent parses and executes. This maintains readability for humans while providing machine-actionable structure.

**Format Example:**

```markdown
# Create Jira Ticket from Bug Report

Create a Jira ticket in the {project} project for the following bug.

## Inputs
- bug_description: [required]
- project: [required] default: "PLATFORM"
- priority: [optional] default: "medium"

## Steps

1. Connect to Jira using the Jira connector
   - tool: jira.connect
   - requires: jira credentials

2. Create the ticket
   - tool: jira.create_issue
   - project: {project}
   - summary: "Bug: {derived from bug_description}"
   - description: {bug_description}
   - priority: {priority}

3. Report the ticket URL back to the engineer

## Outputs
- ticket_url
```

**Key Features:**

- **Conversational narrative:** Steps are described in natural language, making workflows readable and understandable.
- **Structured annotations:** `tool:`, `requires:`, and `{variables}` provide machine-actionable structure within the narrative.
- **Variable interpolation:** `{variables}` reference workflow inputs or derived values, enabling dynamic execution.
- **Explicit inputs/outputs:** Clear declaration of required and optional inputs, and workflow outputs.

**Benefits:**

- Humans and AI agents can both read, understand, and follow the workflow.
- Supports iterative refinement through conversation.
- Maintains document-driven philosophy while providing technical rigor.

Workflows use a hybrid format combining structured metadata with a conversational body:

**Structured Metadata (YAML frontmatter):**
- Technical requirements: tool dependencies, connector references, input/output schemas
- Version and compatibility information
- Safety metadata: permissions required, side effects, execution environment

**Conversational Body (Markdown):**
- Workflow steps expressed as natural language instructions
- Context, reasoning, and guidance for the agent
- Human-readable narrative that explains intent alongside execution

**Rationale:**
- Structured metadata enables validation, versioning, and automated compatibility checks.
- Conversational body maintains the document-driven, chat-friendly interface that engineers are familiar with.
- Both humans and AI agents can read, understand, and follow the workflow.
- Supports the "chat-driven configuration" principle while providing technical rigor.

**Example Structure:**

```yaml
---
name: Create Jira Ticket from Bug Report
version: 1.0.0
dependencies:
  - jira-connector@>=1.0.0
inputs:
  bug_description: string
  priority: enum[low,medium,high]
outputs:
  ticket_url: string
permissions:
  - jira:create_issue
---

## Step 1: Validate Bug Report
Check that the bug description is complete and actionable...

## Step 2: Create Jira Ticket
Using the Jira connector, create a new issue with...

## Step 3: Report Back
Share the ticket URL and summary with the engineer...
```
