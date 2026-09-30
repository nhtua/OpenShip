# OpenShip Ideas

Canonical repository of project ideas. Each idea should be clear, well-structured, and placed contextually.

---

## Core Architecture: Tool-Centric LLM Runtime

**Type:** Architecture
**Summary:** OpenShip as a runtime environment that exposes tools to LLMs for autonomous planning and task completion.
**Date:** 2026-09-30
**Status:** Active

OpenShip should be visualized not as a single agent, but as an environment where LLMs plan and pick up available tools to complete tasks. This shifts the paradigm from "building an agent" to "building a runtime for agentic execution."

### Key Components

- **Tool Discovery Mechanism:** A robust system for LLMs to discover, understand, and select appropriate tools at runtime.
- **Internal Tools:** Tools developed and embedded directly in OpenShip's agent code.
- **External Tools:** Support for loading tools from outside OpenShip (extensions, MCP servers, community plugins).
- **Connectors:** Specialized tools for authentication, credential storage, and authenticated request proxying across services.

### Vision

As long as we have good tool discovery, the LLM should be able to plan and use the right tools for the job. This makes OpenShip extensible, composable, and adaptable to diverse DevOps workflows.

### Implications

- Tool interfaces need to be standardized and well-documented
- Discovery metadata must be rich enough for LLM decision-making
- Security model must handle both internal and external tool execution
- Connector framework should abstract authentication patterns (OAuth, API keys, tokens, etc.)

### Prior Art & Competitive Landscape

This idea is not unique — several projects have already explored similar concepts:

- **MCP-Zero** (2025): An active agent framework that enables LLMs to dynamically construct task-specific toolchains through on-demand tool retrieval. Reduces token costs by up to 98%.
- **LangGraph**: Agent orchestration framework with tool selection capabilities.
- **Agent Plugins 1.0** (Aug 2026): Open standard for packaging reusable agent components into portable plugins.
- **ToolRegistry** (2025): Protocol-agnostic tool management system.
- **Semantic Kernel**: Microsoft's plugin framework for agent capabilities.
- **Spring AI**: Dynamic tool search and discovery.

**OpenShip's potential differentiation:** Focus on DevOps/Platform engineering workflows with built-in connector framework for cloud services and infrastructure management.

---
