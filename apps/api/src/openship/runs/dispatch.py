"""Closed registry for versioned durable run graphs."""

from collections.abc import Callable

from .graph import build_chat_graph


class UnsupportedGraphVersion(ValueError):
    """The installed runtime has no implementation for this persisted graph."""


_GRAPH_FACTORIES: dict[tuple[str, int], Callable] = {
    ("chat", 1): build_chat_graph,
}


def resolve_graph(kind: str, graph_version: int) -> Callable:
    """Resolve an exact maintained graph version; never guess historical intent."""
    try:
        return _GRAPH_FACTORIES[(kind, graph_version)]
    except KeyError as exc:
        raise UnsupportedGraphVersion(
            f"unsupported run graph {kind!r} version {graph_version}"
        ) from exc
