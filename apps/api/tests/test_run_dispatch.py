"""Closed graph-version dispatch prevents guessing at historical records."""

import pytest

from src.openship.runs.dispatch import UnsupportedGraphVersion, resolve_graph


def test_chat_graph_v1_is_the_existing_graph():
    from src.openship.runs.graph import build_chat_graph

    assert resolve_graph("chat", 1) is build_chat_graph


def test_tool_graph_v1_is_the_model_free_graph():
    from src.openship.runs.tool_graph import build_tool_graph

    assert resolve_graph("tool", 1) is build_tool_graph


def test_unknown_graph_version_fails_closed():
    with pytest.raises(UnsupportedGraphVersion):
        resolve_graph("chat", 99)
