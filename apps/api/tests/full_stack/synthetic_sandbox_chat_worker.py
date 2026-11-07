"""Durable chat-v2 worker with a scripted download/python model and canned download bytes.

Synthetic boundaries only: the model adapter (deterministic tool choice) and the
public HTTPS download body (canned CSV). The URL validation gate, download
artifact capture, PostgreSQL, the queue, the agent graph, the tool gateway, the
OpenSandbox client transport and the artifact store remain real; the sandbox
transport is never stubbed. Provider traffic is observed through a logging
wrapper on the client's own http session: created sandbox ids and the per
execution fresh request directory (command cwd) are recorded, never replaced.

The scripted model replays recorded transcript decisions when it sees a
carried-over transcript (a resumed run), minting fresh provider call ids so
the replay re-issues the same decisions and reuses the committed operations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import src.openship.providers.runner as provider_runner
import src.openship.sandboxes.tool as sandbox_tool
from src.openship.providers.contracts import ProviderEvent, ToolCall
from src.openship.tools.download import tool as download_tool
from src.openship.tools.download.transport import _url as _download_url_gate

DOWNLOAD_CSV = (
    "name,value\n"
    "widget-a,3\n"
    "widget-b,5\n"
    "widget-c,2\n"
    "widget-d,7\n"
)

DOWNLOAD_URL = "https://data.example.com/reports/sales.csv"

PYTHON_SOURCE = (
    "import csv\n"
    "from pathlib import Path\n"
    "import os\n"
    "rows = list(csv.DictReader(\n"
    '    (Path(os.environ["OPENSHIP_INPUT_DIR"]) / "sales.csv")\n'
    '    .read_text(encoding="utf-8").splitlines(),\n'
    "))\n"
    "labels = [row['name'] for row in rows]\n"
    "values = [int(row['value']) for row in rows]\n"
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "figure = plt.figure()\n"
    "axis = figure.add_subplot(1, 1, 1)\n"
    "axis.bar(labels, values)\n"
    "figure.savefig(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'chart.png')\n"
    "print(f'{len(rows)} rows charted')\n"
)


def _append_record(name: str, record: dict) -> None:
    path = Path(os.environ[name])
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


def _record_model_turn(profile, request, tool_name, arguments=None):
    _append_record(
        "SYNTHETIC_MODEL_CALL_LOG",
        {
            "profile_id": profile.id,
            "model": profile.model,
            "message_count": len(request.messages),
            "tool_names": [tool.name for tool in request.tools],
            "tool_result_count": sum(message.role == "tool" for message in request.messages),
            "selected_tool": tool_name,
            "arguments": arguments,
        },
    )


class _LoggingSandboxClient(sandbox_tool.OpenSandboxClient):
    """Observation only: records created sandbox ids and each command's cwd."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        session = self._http
        for name in ("post", "get"):
            original = getattr(session, name)

            def make_observer(name=name, original=original):
                async def request(*args, **kwargs):
                    url = str(args[0]) if args else str(kwargs.get("url", ""))
                    response = await original(*args, **kwargs)
                    if (
                        name == "post"
                        and url.endswith("/v1/sandboxes")
                        and 200 <= response.status_code < 300
                    ):
                        data = response.json()
                        _append_record(
                            "SYNTHETIC_SANDBOX_CALL_LOG",
                            {
                                "kind": "create",
                                "sandbox_id": data["id"],
                                "image": kwargs.get("json", {}).get("image", {}).get("uri"),
                            },
                        )
                    return response

                return request

            setattr(session, name, make_observer(name, original))
        original_stream = session.stream

        def observe_stream(*args, **kwargs):
            method, url = args[0], str(args[1])
            body = kwargs.get("json")
            if method == "POST" and url.endswith("/command") and isinstance(body, dict):
                _append_record(
                    "SYNTHETIC_SANDBOX_CALL_LOG",
                    {"kind": "command", "cwd": body.get("cwd"), "argv": body.get("argv")},
                )
            return original_stream(*args, **kwargs)

        session.stream = observe_stream


class SyntheticSandboxChatAdapter:
    """Scripted chat model: download, chart, report; replays recorded decisions."""

    def _recorded_chain(self, messages):
        """Split a carried-over transcript chain from live replay messages."""
        chain_end = -1
        for index, message in enumerate(messages):
            if (
                message.role == "assistant"
                and not message.tool_calls
                and message.content
            ):
                chain_end = index
        if chain_end < 0:
            return None
        decisions = [
            call
            for message in messages[:chain_end]
            if message.role == "assistant"
            for call in (message.tool_calls or ())
        ]
        recorded_tools = sum(message.role == "tool" for message in messages[:chain_end])
        return decisions, recorded_tools

    async def stream(self, request, config, control):
        control.raise_if_stopped()
        by_name = {tool.name: tool for tool in request.tools}

        replay = self._recorded_chain(request.messages)
        if replay is not None:
            decisions, recorded_tools = replay
            tool_total = sum(message.role == "tool" for message in request.messages)
            step = tool_total - recorded_tools
            if step < len(decisions):
                recorded = decisions[step]
                if recorded.name not in by_name:
                    _record_model_turn(config, request, None)
                    yield ProviderEvent(
                        type="text_delta",
                        text="A previously recorded tool is no longer authorized.",
                    )
                    yield ProviderEvent(type="stream_finished", reason="stop", actual_model=config.model)
                    return
                _record_model_turn(config, request, recorded.name, recorded.arguments)
                yield ProviderEvent(
                    type="tool_call_ready",
                    tool_call=ToolCall(
                        provider_call_id=f"synthetic-replay-call-{step}",
                        name=recorded.name,
                        arguments=recorded.arguments,
                    ),
                )
                yield ProviderEvent(type="stream_finished", reason="tool_calls", actual_model=config.model)
                return
            final_text = next(
                (
                    message.content
                    for message in reversed(request.messages)
                    if message.role == "assistant" and message.content
                ),
                "",
            )
            _record_model_turn(config, request, None)
            yield ProviderEvent(type="text_delta", text=final_text)
            yield ProviderEvent(type="stream_finished", reason="stop", actual_model=config.model)
            return

        prior_results = [message for message in request.messages if message.role == "tool"]
        latest_user = next(
            (message.content for message in reversed(request.messages) if message.role == "user"),
            "",
        )
        selected = None
        arguments = None
        if request.tools and "chart" in latest_user.lower() and not prior_results:
            selected = next((item for item in request.tools if item.name == "download_file"), None)
            if selected is not None:
                arguments = {"url": DOWNLOAD_URL}
        elif prior_results and prior_results[-1].name == "download_file":
            selected = next((item for item in request.tools if item.name == "python_execute"), None)
            if selected is not None:
                downloaded = json.loads(prior_results[-1].content)["result"]["artifact"]
                arguments = {
                    "source": PYTHON_SOURCE,
                    "input_ids": {"sales.csv": downloaded["id"]},
                    "output_media": {"chart.png": "image/png"},
                }

        if selected is not None and arguments is not None:
            _record_model_turn(config, request, selected.name, arguments)
            yield ProviderEvent(
                type="tool_call_ready",
                tool_call=ToolCall(
                    provider_call_id=f"synthetic-sandbox-call-{len(prior_results)}",
                    name=selected.name,
                    arguments=arguments,
                ),
            )
            yield ProviderEvent(type="stream_finished", reason="tool_calls", actual_model=config.model)
            return

        _record_model_turn(config, request, None)
        if prior_results and prior_results[-1].name == "python_execute":
            executed = json.loads(prior_results[-1].content)["result"]
            fetched = json.loads(prior_results[0].content)["result"]["artifact"]
            text = (
                f"Fetched {fetched['size_bytes']} CSV bytes with SHA-256 {fetched['sha256']} and "
                f"charted them: exit {executed['exit_code']}, stdout {executed['stdout'].strip()}"
            )
        else:
            text = "No tool was needed for this message."
        yield ProviderEvent(type="text_delta", text=text)
        yield ProviderEvent(type="stream_finished", reason="stop", actual_model=config.model)


def _synthetic_adapter(profile):
    if profile.adapter == "openai":
        return SyntheticSandboxChatAdapter()
    return _original_build_adapter(profile)


_original_build_adapter = provider_runner.build_adapter


def main() -> None:
    # Only the model decision and the public download body are canned; the
    # OpenSandbox provider, gateway, queue, database and stores are live.
    download_tool.download_bytes = _stub_download
    download_tool.download_stream = _stub_download_stream
    sandbox_tool.OpenSandboxClient = _LoggingSandboxClient
    provider_runner.build_adapter = _synthetic_adapter
    from tests.full_stack.legacy_worker_harness import main as worker_main

    worker_main()


async def _stub_download_stream(url, control, write):
    # The real URL policy gate runs first; only the response bytes are canned.
    _download_url_gate(url)
    _append_record("SYNTHETIC_DOWNLOAD_CALL_LOG", {"url": url, "bytes": len(DOWNLOAD_CSV.encode())})
    write(DOWNLOAD_CSV.encode("utf-8"))


async def _stub_download(url, control):
    _download_url_gate(url)
    _append_record("SYNTHETIC_DOWNLOAD_CALL_LOG", {"url": url, "bytes": len(DOWNLOAD_CSV.encode())})
    return DOWNLOAD_CSV.encode("utf-8")


if __name__ == "__main__":
    main()
