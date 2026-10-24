"""Closed registry for versioned durable run graphs."""

from collections.abc import Callable

from .agent_graph import build_agent_graph
from .graph import build_chat_graph
from .tool_graph import build_tool_graph


class UnsupportedGraphVersion(ValueError):
    """The installed runtime has no implementation for this persisted graph."""


_GRAPH_FACTORIES: dict[tuple[str, int], Callable] = {
    ("chat", 1): build_chat_graph,
    ("chat", 2): build_agent_graph,
    ("chat", 3): build_agent_graph,
    ("chat", 4): build_agent_graph,
    ("tool", 1): build_tool_graph,
    ("tool", 2): build_tool_graph,
    ("tool", 3): build_tool_graph,
}


def resolve_graph(kind: str, graph_version: int) -> Callable:
    """Resolve an exact maintained graph version; never guess historical intent."""
    try:
        return _GRAPH_FACTORIES[(kind, graph_version)]
    except KeyError as exc:
        raise UnsupportedGraphVersion(
            f"unsupported run graph {kind!r} version {graph_version}"
        ) from exc
