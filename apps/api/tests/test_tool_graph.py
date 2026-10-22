"""The maintained manual graph invokes only the exact persisted command."""

import json
import uuid
from datetime import UTC, datetime

from src.openship.capabilities.catalog import list_legacy_search_manifests
from src.openship.capabilities.contracts import (
    AuthorizationContext,
    CapabilityRef,
    ExecutionConfig,
    PublishedToolResult,
    ToolInvocationContext,
)
from src.openship.providers.control import InvocationControl
from src.openship.runs.queue import Claim
from src.openship.runs.tool_graph import build_tool_graph


def test_manual_graph_calls_gateway_once_with_the_pinned_tool_and_no_runtime_state():
    manifest = next(
        item
        for item in list_legacy_search_manifests()
        if item.id == "tavily_web_search"
    )
    ref = CapabilityRef(
        id=manifest.id,
        version=manifest.version,
        manifest_digest=manifest.manifest_digest,
    )
    run_id = uuid.uuid4()
    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    claim = Claim(
        run_id=run_id,
        fence=4,
        lease_until=datetime.now(UTC),
        worker_id="tool-graph-test",
        user_id=user_id,
    )
    context = ToolInvocationContext(
        authorization=AuthorizationContext(
            principal_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            action="execute_tool",
            target_id=str(run_id),
        ),
        execution=ExecutionConfig(
            graph_kind="tool",
            graph_version=1,
            allowed_capabilities=(ref,),
            credential_binding_refs=(manifest.credential_binding_ref,),
        ),
        credential_resolver=lambda _name: "runtime-only-test-credential",
    )
    control = InvocationControl()
    calls = []

    class FakeGateway:
        async def execute_tool(self, **kwargs):
            calls.append(kwargs)
            return PublishedToolResult(
                operation_id=uuid.uuid4(),
                status="succeeded",
                result={"items": [], "truncated": False},
            )

    graph = build_tool_graph(
        gateway_factory=lambda: FakeGateway(),
        invocation_factory=lambda _state: (claim, context, control),
    )
    command = {
        "capability": ref.model_dump(mode="json"),
        "arguments": {"query": "filesystem artifact workspace", "max_results": 3},
    }

    result = graph.invoke(
        {
            "run_id": str(run_id),
            "tool_command_json": json.dumps(command),
            "execution_configuration": context.execution.model_dump_json(),
        }
    )

    assert len(calls) == 1
    assert calls[0]["logical_call_id"] == "manual:0"
    assert calls[0]["ref"] == ref
    assert calls[0]["arguments"] == command["arguments"]
    assert calls[0]["claim"] is claim
    assert calls[0]["context"] is context
    assert calls[0]["control"] is control
    assert result["tool_result"]["status"] == "succeeded"
    assert set(result) == {
        "run_id",
        "tool_command_json",
        "execution_configuration",
        "tool_result",
    }
    assert "runtime-only-test-credential" not in json.dumps(result)
