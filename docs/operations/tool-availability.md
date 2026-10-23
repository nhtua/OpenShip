---
type: operations
summary: Configure installed built-in tool availability and adopt a legacy deployment allowlist.
date: 2026-10-23
status: draft
---

# Built-in tool availability

Global Settings controls which installed, maintained tools are available to conversations. The installation operator can enable or disable each tool; changes take effect through the shared database policy without restarting the API or worker.

`TOOL_ENABLED_IDS` is an optional deployment ceiling:

- Unset: managed Global Settings is the only tool availability control.
- Empty: no tools are permitted, preserving a chat-only deployment.
- Nonempty: only listed tool IDs can be enabled in Global Settings. Unknown IDs are reported and do not enable anything.

If an installation still sets `TOOL_ENABLED_IDS`, Global Settings offers a one-time cutover that copies the current effective set into managed policy. Review the preview, apply the cutover, then remove `TOOL_ENABLED_IDS` from both API and worker environments and restart those processes once. The stored overrides preserve the copied allow/deny state after the ceiling is removed. Do not remove the variable before adopting its existing intent.

Removing the legacy ceiling is not required for safety: while it remains, it continues to restrict tools even if an operator enables them in Global Settings. Ordinary policy changes, Connector readiness changes, and project binding changes do not need a process restart.

Tool policy changes do not replay runs that were already blocked or interrupted. A run whose admitted tool is disabled before dispatch or publication is refused at that fence. Historical run records remain readable.
