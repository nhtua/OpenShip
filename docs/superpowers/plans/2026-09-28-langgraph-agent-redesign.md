---
type: plan
summary: Implementation plan for redesigning the agent CLI with LangGraph builder and executor workflows, including HITL via interrupt().
status: completed
date: 2026-09-28
---

# LangGraph Agent Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the agent CLI to model the agent lifecycle as two LangGraph workflows (builder and executor) with HITL via `interrupt()`.

**Architecture:** Replace `Agent` class with two LangGraph StateGraph workflows: builder (load → plan → approve loop) and executor (compile → execute). CLI orchestrates both. Shared utilities (parser, compiler, tools) remain unchanged.

**Tech Stack:** Python 3.11+, LangGraph, OpenAI Python SDK, python-dotenv