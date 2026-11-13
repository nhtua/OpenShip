"""Closed registry for versioned durable run graphs."""

from collections.abc import Callable

from ..runs.tasks.contracts import ExecutionConfigV5
from .agent_graph import build_agent_graph
from .analysis_graph import build_analysis_graph
from .graph import build_chat_graph
from .task_graph import build_task_graph
from .tool_graph import build_tool_graph


class UnsupportedGraphVersion(ValueError):
    """The installed runtime has no implementation for this persisted graph."""


_GRAPH_FACTORIES: dict[tuple[str, int], Callable] = {
    ("chat", 1): build_chat_graph,
    ("chat", 2): build_agent_graph,
    ("chat", 3): build_agent_graph,
    ("chat", 4): build_agent_graph,
    ("chat", 5): build_task_graph,
    ("tool", 1): build_tool_graph,
    ("tool", 2): build_tool_graph,
    ("tool", 3): build_tool_graph,
}


_ANALYSIS_CAPABILITY = "sandbox_analysis"


def resolve_graph(
    kind: str,
    graph_version: int,
    execution=None,
) -> Callable:
    """Resolve an exact maintained graph version; never guess historical intent.

    A pinned tool execution that authorizes the sandbox analysis capability
    runs the fixed analysis journey inside the same persisted graph version, so
    a durable run always resolves to the same implementation.
    """
    if (
        kind == "tool"
        and execution is not None
        and any(
            item.id == _ANALYSIS_CAPABILITY for item in execution.allowed_capabilities
        )
    ):
        return build_analysis_graph
    if (kind, graph_version) == ("chat", 5) and not isinstance(
        execution, ExecutionConfigV5
    ):
        # A v5 task run only runs against its own pinned snapshot: a missing
        # or legacy snapshot blocks instead of falling back to older routing.
        raise UnsupportedGraphVersion(
            "chat graph 5 requires a pinned ExecutionConfigV5 snapshot"
        )
    try:
        return _GRAPH_FACTORIES[(kind, graph_version)]
    except KeyError as exc:
        raise UnsupportedGraphVersion(
            f"unsupported run graph {kind!r} version {graph_version}"
        ) from exc
