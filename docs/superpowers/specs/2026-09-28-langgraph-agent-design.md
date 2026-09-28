---
type: spec
summary: Design specification for redesigning the agent CLI with LangGraph builder and executor workflows, implementing the "everything is a workflow" principle.
status: approved
date: 2026-09-28
---

# LangGraph Agent Redesign — Design

## Overview

Redesign the OpenShip agent CLI PoC to model the agent's entire lifecycle as a LangGraph workflow. This implements the "everything is a workflow" principle by making the agent itself a workflow, not just a Python class that orchestrates LangGraph compilation and execution.