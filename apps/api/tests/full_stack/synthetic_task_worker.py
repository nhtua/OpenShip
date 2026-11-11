"""Durable v5 task worker with a scripted task model and a canned source body.

Synthetic boundaries only: the model (a deterministic stage script) and the
controlled HTTPS source bytes (a canned weather CSV). Everything the journey
depends on stays real - the URL policy gate, download artifact capture,
PostgreSQL, the durable queue, the eleven-node v5 task graph, the tool gateway,
the OpenSandbox client transport and the artifact store. The sandbox transport
is never stubbed, and the sandbox profile snapshot is the operator's own file
loaded by the runtime's own loader (see ``task_profile_mount``).

The script answers what each stage actually asks: the stage prompts
(``Stage: assessment.``/``Stage: assessment_and_plan.``) carry no tools and
answer with one strict JSON envelope, and the step request answers with the one
tool the plan step owns. For the weather journey the first plan envelope
carries one known JSON-field defect, which the stage refuses and the corrected
envelope then answers - a repair the durable records show, not a retry the run
hides.

A controlled fault marker may kill this process at a named provider phase,
which leaves the durable records exactly as a lost worker would: the call
binding is committed, the operation stays unsettled and the provider's own
outcome is unknown. Nothing here decides what a resumed run does with that.
"""

from __future__ import annotations

import json
import os
import re
import signal
from pathlib import Path

import src.openship.providers.runner as provider_runner
import src.openship.sandboxes.tool as sandbox_tool
from src.openship.providers.contracts import ProviderEvent, ToolCall
from src.openship.tools.download import tool as download_tool
from src.openship.tools.download.transport import _url as _download_url_gate

_API_ROOT = Path(__file__).parents[2]
_FIXTURES = _API_ROOT / "tests" / "fixtures" / "agentic_tasks"

WEATHER_CSV = (
    "city,month,temperature_c\n"
    "London,January,4\n"
    "London,January,6\n"
    "London,February,7\n"
    "London,February,9\n"
    "Paris,January,11\n"
)

WEATHER_URL = "https://weather.example.test/climate/london-2026.csv"
UNSAFE_URL = "http://data.example.com/reports/sales.csv"

WEATHER_PLAN = json.loads(
    (_FIXTURES / "weather_plan.json").read_text(encoding="utf-8")
)
LOGS_PLAN = json.loads((_FIXTURES / "logs_plan.json").read_text(encoding="utf-8"))
DEFECTIVE_PLAN = json.loads(
    (_FIXTURES / "plan_defective_notes.json").read_text(encoding="utf-8")
)
BLOCKED_SOURCE = json.loads(
    (_FIXTURES / "assessment_blocked_source.json").read_text(encoding="utf-8")
)

WEATHER_SOURCE = (
    "import csv\n"
    "import json\n"
    "import os\n"
    "from pathlib import Path\n"
    "rows = list(csv.DictReader(\n"
    '    (Path(os.environ["OPENSHIP_INPUT_DIR"]) / "weather.csv")\n'
    '    .read_text(encoding="utf-8").splitlines(),\n'
    "))\n"
    'london = [row for row in rows if row["city"] == "London"]\n'
    "averages = {}\n"
    'for month in ("January", "February"):\n'
    '    values = [int(row["temperature_c"]) for row in london if row["month"] == month]\n'
    "    averages[month] = round(sum(values) / len(values))\n"
    "out = Path(os.environ['OPENSHIP_OUTPUT_DIR'])\n"
    "(out / 'averages.json').write_text(json.dumps(averages, sort_keys=True))\n"
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "figure = plt.figure()\n"
    "axis = figure.add_subplot(1, 1, 1)\n"
    "axis.bar(list(averages), averages.values())\n"
    "figure.savefig(out / 'chart.png')\n"
    'print(f"{len(london)} London rows averaged")\n'
)

LOGS_SOURCE = (
    "import json\n"
    "import os\n"
    "from pathlib import Path\n"
    "records = json.loads(\n"
    '    (Path(os.environ["OPENSHIP_INPUT_DIR"]) / "runs.json")\n'
    '    .read_text(encoding="utf-8")\n'
    ")\n"
    "averages = {}\n"
    'for service in sorted({row["service"] for row in records}):\n'
    '    values = [row["duration_ms"] for row in records if row["service"] == service]\n'
    "    averages[service] = round(sum(values) / len(values))\n"
    "out = Path(os.environ['OPENSHIP_OUTPUT_DIR'])\n"
    "(out / 'averages.json').write_text(json.dumps(averages, sort_keys=True))\n"
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "figure = plt.figure()\n"
    "axis = figure.add_subplot(1, 1, 1)\n"
    "axis.bar(list(averages), averages.values())\n"
    "figure.savefig(out / 'chart.png')\n"
    'print(f"{len(records)} records averaged")\n'
)

OUTPUT_MEDIA = {"averages.json": "application/json", "chart.png": "image/png"}


def _append_record(name: str, record: dict) -> None:
    path = Path(os.environ[name])
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)


def _fault(phase: str) -> None:
    """Die at the named provider phase, exactly as a lost worker would."""
    marker = Path(os.environ.get("SYNTHETIC_FAULT_MARKER", ""))
    if not marker.is_file():
        return
    if phase not in marker.read_text(encoding="utf-8"):
        return
    os.kill(os.getpid(), signal.SIGKILL)


def _model_call(
    kind: str,
    request,
    *,
    text=None,
    tool_name=None,
    arguments=None,
) -> None:
    _append_record(
        "SYNTHETIC_MODEL_CALL_LOG",
        {
            "kind": kind,
            "message_count": len(request.messages),
            "tool_names": [tool.name for tool in request.tools],
            "tool_result_count": sum(
                1 for message in request.messages if message.role == "tool"
            ),
            "selected_tool": tool_name,
            "arguments": arguments,
            "answer": text,
        },
    )


def _stage_of(request) -> str | None:
    """Which bounded stage this request is, from the prompt the runtime wrote."""
    for message in request.messages:
        if message.role != "user" or not message.content:
            continue
        if "Stage: assessment_and_plan." in message.content:
            return "assessment_and_plan"
        if "Stage: assessment." in message.content:
            return "assessment"
    return None


def _original_intent(request) -> str:
    """The request this run pinned, which is what the plan was reviewed against."""
    for message in request.messages:
        if message.role != "user" or not message.content:
            continue
        for line in message.content.splitlines():
            if line.startswith("Original request: "):
                return line[len("Original request: ") :]
        match = re.search(r"^Task: (.*) Plan revision \d+: ", message.content)
        if match:
            return match.group(1)
    return ""


def _plan_for(intent: str) -> dict:
    """The one plan this script proposes for this request, or none."""
    lowered = intent.lower()
    if "logs" in lowered:
        return LOGS_PLAN
    if "weather" in lowered or "london" in lowered:
        if "report" in lowered:
            # The browser recovery fixture needs a step that consumes the
            # chart, so continuing past an unresolved chart is honestly
            # blocked by a dependent successor. The happy path keeps the
            # two-step plan.
            plan = json.loads(json.dumps(WEATHER_PLAN))
            plan["plan"]["steps"].append(
                {
                    "id": "report",
                    "intent": "Write the short report from the charted averages",
                    "depends_on": ["chart"],
                    "capability": "python_execute",
                    "profile_id": None,
                    "expected_outputs": ["report.json"],
                    "completion_checks": ["the report is recorded"],
                }
            )
            return plan
        return WEATHER_PLAN
    return {}


def _source_for(intent: str) -> str:
    return LOGS_SOURCE if "logs" in intent.lower() else WEATHER_SOURCE


def _input_alias(intent: str) -> str:
    return "runs.json" if "logs" in intent.lower() else "weather.csv"


def _published_artifacts(request) -> list[str]:
    """Artifact ids this run already published through a settled tool result."""
    found: list[str] = []
    for message in request.messages:
        if message.role != "tool":
            continue
        try:
            published = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        for artifact in published.get("artifacts") or ():
            if artifact.get("id"):
                found.append(str(artifact["id"]))
        result = published.get("result") or {}
        for artifact in (result.get("artifact"), *result.get("outputs", {}).values()):
            if artifact and artifact.get("id"):
                found.append(str(artifact["id"]))
    return found


def _named_artifacts(intent: str) -> list[str]:
    """Artifact ids this submission named, in the order it named them."""
    return re.findall(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        intent,
    )


def _settled_failure(request) -> bool:
    """Whether this run already has a tool result that refused or failed."""
    for message in request.messages:
        if message.role != "tool":
            continue
        try:
            published = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        if published.get("status") in {"failed", "cancelled", "interrupted"}:
            return True
    return False


def _profile_for(request) -> str | None:
    """A profile the tool this step owns actually offered, from its enum."""
    for tool in request.tools:
        enum = (tool.parameters.get("properties") or {}).get("profile_id", {}).get("enum")
        if not enum:
            continue
        for candidate in ("logs-data", *enum):
            if candidate in enum:
                return candidate
    return None


class SyntheticTaskAdapter:
    """Scripted v5 task model: stage envelopes, one step call, then report."""

    def _assessment(self, intent: str, request) -> dict:
        if _settled_failure(request) and "unreachable" in intent.lower():
            return BLOCKED_SOURCE
        plan = _plan_for(intent)
        return dict(
            plan.get("assessment")
            or {
                "classification": "informational",
                "explanation": "Nothing further was requested.",
                "evidence_refs": [],
                "missing_information": [],
                "failure": "none",
                "step_id": None,
                "unknowns": [],
                "risks": [],
                "recommended_recovery_action": None,
            }
        )

    def _plan_answers(self, intent: str) -> list[dict]:
        plan = _plan_for(intent)
        if not plan:
            return []
        if "london" in intent.lower():
            return [DEFECTIVE_PLAN, plan]
        return [plan]

    async def stream(self, request, config, control):
        control.raise_if_stopped()

        stage = _stage_of(request)
        if stage is not None and not request.tools:
            intent = _original_intent(request)
            if stage == "assessment":
                assessment = self._assessment(intent, request)
                text = json.dumps({"assessment": assessment})
                _model_call("assessment", request, text=text)
                yield ProviderEvent(type="text_delta", text=text)
                yield ProviderEvent(
                    type="stream_finished", reason="stop", actual_model=config.model
                )
                return
            answers = self._plan_answers(intent)
            asked = sum(
                1
                for message in request.messages
                if message.role == "user"
                and "The previous answer was refused:" in message.content
            )
            envelope = answers[min(asked, len(answers) - 1)]
            text = json.dumps(envelope)
            _model_call("plan", request, text=text)
            yield ProviderEvent(type="text_delta", text=text)
            yield ProviderEvent(
                type="stream_finished", reason="stop", actual_model=config.model
            )
            return

        if request.tools and not _settled_failure(request):
            capability = request.tools[0].name
            intent = _original_intent(request)
            arguments: dict | None = None
            if capability == "download_file":
                arguments = {
                    "url": UNSAFE_URL if "unreachable" in intent.lower() else WEATHER_URL
                }
            elif capability == "python_execute":
                profile_id = _profile_for(request)
                if profile_id is not None:
                    alias = _input_alias(intent)
                    named = _named_artifacts(intent) or _published_artifacts(request)
                    if named:
                        arguments = {
                            "profile_id": profile_id,
                            "source": _source_for(intent),
                            "input_ids": {alias: named[-1]},
                            "output_media": dict(OUTPUT_MEDIA),
                        }
            if arguments is not None:
                _model_call("step", request, tool_name=capability, arguments=arguments)
                yield ProviderEvent(
                    type="tool_call_ready",
                    tool_call=ToolCall(
                        provider_call_id=f"synthetic-task-call-{capability}",
                        name=capability,
                        arguments=arguments,
                    ),
                )
                yield ProviderEvent(
                    type="stream_finished",
                    reason="tool_calls",
                    actual_model=config.model,
                )
                return

        text = self._report(request)
        _model_call("text", request, text=text)
        yield ProviderEvent(type="text_delta", text=text)
        yield ProviderEvent(
            type="stream_finished", reason="stop", actual_model=config.model
        )

    def _report(self, request) -> str:
        if _settled_failure(request):
            return "A recorded step refused, so this run reports that blocker."
        return "The recorded steps are reported by the run's own plan."


class _LoggingSandboxClient(sandbox_tool.OpenSandboxClient):
    """Observation plus the fault point, never a replacement provider call."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        session = self._http
        for name in ("post", "get"):
            original = getattr(session, name)

            def make_observer(name=name, original=original):
                async def request(*args, **kwargs):
                    url = str(args[0]) if args else str(kwargs.get("url", ""))
                    if name == "post" and url.endswith("/v1/sandboxes"):
                        _fault("create")
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
                                "image": (kwargs.get("json") or {})
                                .get("image", {})
                                .get("uri"),
                                "profile_id": os.environ.get("SANDBOX_PROFILE_ID"),
                                "dependency_identity": os.environ.get(
                                    "SANDBOX_DEPENDENCY_IDENTITY"
                                ),
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
                _fault("command")
                _append_record(
                    "SYNTHETIC_SANDBOX_CALL_LOG",
                    {"kind": "command", "cwd": body.get("cwd"), "argv": body.get("argv")},
                )
            return original_stream(*args, **kwargs)

        session.stream = observe_stream


def _synthetic_adapter(profile):
    if profile.adapter == "openai":
        return SyntheticTaskAdapter()
    return _original_build_adapter(profile)


_original_build_adapter = provider_runner.build_adapter


def main() -> None:
    from tests.full_stack.task_profile_mount import install_profile_snapshot

    install_profile_snapshot(os.environ["SYNTHETIC_PROFILE_SNAPSHOT"])
    download_tool.download_bytes = _stub_download
    download_tool.download_stream = _stub_download_stream
    sandbox_tool.OpenSandboxClient = _LoggingSandboxClient
    provider_runner.build_adapter = _synthetic_adapter
    from tests.full_stack.legacy_worker_harness import main as worker_main

    worker_main()


async def _stub_download_stream(url, control, write):
    _download_url_gate(url)
    _fault("download")
    _append_record(
        "SYNTHETIC_DOWNLOAD_CALL_LOG", {"url": url, "bytes": len(WEATHER_CSV.encode())}
    )
    write(WEATHER_CSV.encode("utf-8"))


async def _stub_download(url, control):
    _download_url_gate(url)
    _fault("download")
    _append_record(
        "SYNTHETIC_DOWNLOAD_CALL_LOG", {"url": url, "bytes": len(WEATHER_CSV.encode())}
    )
    return WEATHER_CSV.encode("utf-8")


if __name__ == "__main__":
    main()
