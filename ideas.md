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
