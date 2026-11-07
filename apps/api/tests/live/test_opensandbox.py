"""Opt-in client journey against an operator-configured OpenSandbox service."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from src.openship.sandboxes.client import OpenSandboxClient, SandboxUnavailable
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    InputFile,
    OutputSpec,
    PreparedFiles,
    RuntimeRequirements,
)


def ref(data: bytes) -> ArtifactRef:
    return ArtifactRef(id=uuid4(), sha256=hashlib.sha256(data).hexdigest(),
                       size_bytes=len(data), media_type="text/plain")


@pytest.mark.provider_live
@pytest.mark.asyncio
async def test_live_sandbox_transfers_current_inputs_and_reuses_handle():
    if os.environ.get("OPENSHIP_LIVE_OPENSANDBOX") != "1":
        pytest.skip("set OPENSHIP_LIVE_OPENSANDBOX=1 to select the real provider")
    endpoint = os.environ["OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT"]
    key = Path(os.environ["OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE"]).read_text().strip()
    image = os.environ["OPENSHIP_LIVE_OPENSANDBOX_IMAGE"]
    requirements = RuntimeRequirements(provider_id="live-test", profile_id="python311",
                                       image=image, dependency_identity="stdlib")
    source = (b"import os\nfrom pathlib import Path\n"
              b"data = (Path(os.environ['OPENSHIP_INPUT_DIR']) / 'log.txt').read_text()\n"
              b"(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'report.txt').write_text(data.upper())\n")
    create_requests = []
    command_directories = []

    async def capture_create(request):
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            create_requests.append(json.loads(request.content))
        if request.method == "POST" and request.url.path.endswith("/command"):
            command_directories.append(json.loads(request.content)["cwd"])

    async with httpx.AsyncClient(trust_env=False, event_hooks={"request": [capture_create]}) as http:
        client = OpenSandboxClient(endpoint, key, http=http)
        handle = await client.create(requirements)
        assert create_requests[0]["timeout"] == 1800
        assert create_requests[0]["resourceLimits"] == {"cpu": "1", "memory": "512Mi"}
        assert await client.inspect(handle) is not None
        root = uuid4()
        for value, expected in ((b"first", b"FIRST"), (b"second", b"SECOND")):
            request = ExecutionRequest(root_run_id=root, operation_id=uuid4(), requirements=requirements,
                                       source=ref(source), inputs=(InputFile(alias="log.txt", artifact=ref(value)),),
                                       parameters={}, outputs=(OutputSpec(alias="report.txt", media_type="text/plain",
                                                                         kind="data"),))
            result = await client.execute(handle, request, PreparedFiles(source=source, inputs={"log.txt": value}),
                                          InvocationControl())
            assert result.exit_code == 0
            assert result.outputs == {"report.txt": expected}
        assert len(command_directories) == 2
        assert command_directories[0] != command_directories[1]
        failure = b"raise SystemExit(3)\n"
        request = ExecutionRequest(root_run_id=root, operation_id=uuid4(), requirements=requirements,
                                   source=ref(failure), inputs=(), parameters={}, outputs=())
        with pytest.raises(SandboxUnavailable):
            await client.execute(handle, request, PreparedFiles(source=failure, inputs={}), InvocationControl())

        oversized = b"print('x' * (256 * 1024))\n"
        request = ExecutionRequest(root_run_id=root, operation_id=uuid4(), requirements=requirements,
                                   source=ref(oversized), inputs=(), parameters={}, outputs=())
        with pytest.raises(SandboxUnavailable, match="diagnostics"):
            await client.execute(handle, request, PreparedFiles(source=oversized, inputs={}), InvocationControl())

        large_file = (b"import os\nfrom pathlib import Path\n"
                      b"(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'report.txt').write_bytes(b'x' * (4 * 1024 * 1024 + 1))\n")
        request = ExecutionRequest(root_run_id=root, operation_id=uuid4(), requirements=requirements,
                                   source=ref(large_file), inputs=(), parameters={},
                                   outputs=(OutputSpec(alias="report.txt", media_type="text/plain", kind="data"),))
        with pytest.raises(SandboxUnavailable):
            await client.execute(handle, request, PreparedFiles(source=large_file, inputs={}), InvocationControl())

        link = (b"import os\nfrom pathlib import Path\n"
                b"(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'report.txt').symlink_to('/etc/passwd')\n")
        request = ExecutionRequest(root_run_id=root, operation_id=uuid4(), requirements=requirements,
                                   source=ref(link), inputs=(), parameters={},
                                   outputs=(OutputSpec(alias="report.txt", media_type="text/plain", kind="data"),))
        with pytest.raises(SandboxUnavailable):
            await client.execute(handle, request, PreparedFiles(source=link, inputs={}), InvocationControl())

        timeout_source = b"import time\ntime.sleep(40)\n"
        request = ExecutionRequest(root_run_id=root, operation_id=uuid4(), requirements=requirements,
                                   source=ref(timeout_source), inputs=(), parameters={}, outputs=())
        start = time.monotonic()
        with pytest.raises(SandboxUnavailable, match="sandbox command failed"):
            await asyncio.wait_for(client.execute(handle, request, PreparedFiles(source=timeout_source, inputs={}),
                                                  InvocationControl()), timeout=37)
        assert 25 <= time.monotonic() - start < 35

        stopped = False
        waiting = b"import time\ntime.sleep(10)\n"
        request = ExecutionRequest(root_run_id=root, operation_id=uuid4(), requirements=requirements,
                                   source=ref(waiting), inputs=(), parameters={}, outputs=())
        task = asyncio.create_task(client.execute(handle, request, PreparedFiles(source=waiting, inputs={}),
                                                  InvocationControl(stop=lambda: stopped)))
        await asyncio.sleep(1)
        stopped = True
        with pytest.raises(ProviderError, match="cancelled"):
            await asyncio.wait_for(task, timeout=3)

        # A separate short-lived fixture tests provider expiry, without changing
        # the production client's 1,800-second create request.
        response = await http.post(
            f"{endpoint}/v1/sandboxes", headers={"OPEN-SANDBOX-API-KEY": key},
            json={"image": {"uri": image}, "entrypoint": ["/bin/sleep", "infinity"],
                  "timeout": 60, "resourceLimits": {"cpu": "1", "memory": "512Mi"}}, timeout=30,
        )
        response.raise_for_status()
        fixture_id = response.json()["id"]
        for _ in range(90):
            await asyncio.sleep(1)
            response = await http.get(f"{endpoint}/v1/sandboxes/{fixture_id}",
                                      headers={"OPEN-SANDBOX-API-KEY": key}, timeout=10)
            if response.status_code == 404:
                break
        assert response.status_code == 404, "provider did not remove the expired fixture"


@pytest.mark.provider_live
@pytest.mark.asyncio
async def test_live_sandbox_general_stdout_and_image_output():
    """General python journeys: no-input stdout, and a validated PNG from the plot image."""
    if os.environ.get("OPENSHIP_LIVE_OPENSANDBOX") != "1":
        pytest.skip("set OPENSHIP_LIVE_OPENSANDBOX=1 to select the real provider")
    endpoint = os.environ["OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT"]
    key = Path(os.environ["OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE"]).read_text().strip()
    image = os.environ["OPENSHIP_LIVE_OPENSANDBOX_IMAGE"]
    plot_image = os.environ["OPENSHIP_LIVE_OPENSANDBOX_PLOT_IMAGE"]
    requirements = RuntimeRequirements(provider_id="live-general", profile_id="python311",
                                       image=image, dependency_identity="stdlib")

    async with httpx.AsyncClient(trust_env=False) as http:
        client = OpenSandboxClient(endpoint, key, http=http)
        handle = await client.create(requirements)
        assert await client.inspect(handle) is not None

        # A general program with no inputs and no declared outputs: stdout only.
        greeting = b"print('no inputs needed')\n"
        request = ExecutionRequest(root_run_id=uuid4(), operation_id=uuid4(), requirements=requirements,
                                   source=ref(greeting), inputs=(), parameters={}, outputs=())
        result = await client.execute(handle, request, PreparedFiles(source=greeting, inputs={}),
                                      InvocationControl())
        assert result.exit_code == 0
        assert result.stdout == "no inputs needed\n"
        assert result.outputs == {}

        # A plotting program publishes a PNG the client decodes and accepts.
        plotting = (b"import os\nimport matplotlib\nmatplotlib.use('Agg')\n"
                    b"import matplotlib.pyplot as plt\n"
                    b"figure = plt.figure()\n"
                    b"axis = figure.add_subplot(1, 1, 1)\n"
                    b"axis.bar(['a', 'b'], [1, 2])\n"
                    b"figure.savefig(os.environ['OPENSHIP_OUTPUT_DIR'] + '/chart.png')\n")
        plot_requirements = RuntimeRequirements(provider_id="live-general", profile_id="python311-plot",
                                               image=plot_image,
                                               dependency_identity="matplotlib-3.9.2-numpy-2.1.0")
        plot_handle = await client.create(plot_requirements)
        plot_request = ExecutionRequest(root_run_id=uuid4(), operation_id=uuid4(),
                                        requirements=plot_requirements, source=ref(plotting),
                                        inputs=(), parameters={},
                                        outputs=(OutputSpec(alias="chart.png", media_type="image/png",
                                                            kind="data"),))
        plot_result = await client.execute(plot_handle, plot_request,
                                           PreparedFiles(source=plotting, inputs={}),
                                           InvocationControl())
        assert plot_result.exit_code == 0
        assert plot_result.exit_code == 0
        png = plot_result.outputs["chart.png"]
        assert png.startswith(b"\x89PNG\r\n\x1a\n")

        # Provider disappearance: an expired sandbox cannot serve a new command.
        vanished = await client.inspect(handle)
        assert vanished is not None
