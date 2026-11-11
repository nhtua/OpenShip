"""Separately authorized OpenSandbox journey for a preloaded managed image."""

import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.openship.artifacts.contracts import ArtifactRef
from src.openship.providers.control import InvocationControl
from src.openship.sandboxes.client import OpenSandboxClient
from src.openship.sandboxes.contracts import (
    ExecutionRequest,
    OutputSpec,
    PreparedFiles,
    RuntimeRequirements,
)
from src.openship.sandboxes.profile_schema import decode_catalog

pytestmark = pytest.mark.provider_live


def selected_requirements(environ):
    for name in ("ENDPOINT", "KEY_FILE", "IMAGE", "PROFILE_ID", "DEPENDENCY_IDENTITY"):
        if not environ.get("OPENSHIP_LIVE_OPENSANDBOX_" + name):
            pytest.fail(f"explicit live profile test requires OPENSHIP_LIVE_OPENSANDBOX_{name}")
    profile = environ["OPENSHIP_LIVE_OPENSANDBOX_PROFILE_ID"]
    image = environ["OPENSHIP_LIVE_OPENSANDBOX_IMAGE"].removeprefix("localhost/")
    match = re.fullmatch(r"openship-sandbox-(standard|logs-data|iac-validation|devops-shell):([a-f0-9]{64})", image)
    if not match or match[1] != profile:
        pytest.fail("live profile test requires a preloaded local managed recipe tag")
    dependency = environ["OPENSHIP_LIVE_OPENSANDBOX_DEPENDENCY_IDENTITY"]
    if dependency != "sha256:" + match[2]:
        pytest.fail("live profile dependency identity must match the recipe tag")
    return RuntimeRequirements(provider_id="live-phase6c", profile_id=profile, image=image, dependency_identity=dependency)


@pytest.mark.asyncio
async def test_preloaded_managed_image_smoke_through_opensandbox():
    if os.environ.get("OPENSHIP_LIVE_SANDBOX_PROFILES") != "1":
        pytest.skip("set OPENSHIP_LIVE_SANDBOX_PROFILES=1 only with separately authorized provider settings")
    requirements = selected_requirements(os.environ)
    key_file = Path(os.environ["OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE"])
    assert key_file.is_file(), "authorized key file is missing"
    key = key_file.read_text().strip()
    assert key, "authorized key file is empty"
    source = (
        "import json, os, subprocess, sys\nfrom pathlib import Path\n"
        "subprocess.run([sys.executable, '/opt/openship/smoke.py'], check=True, timeout=28)\n"
        f"report = {{'profile': {requirements.profile_id!r}, 'ok': True}}\n"
        "(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'report.json').write_text(json.dumps(report))\n"
    ).encode()
    reference = ArtifactRef(id=uuid4(), sha256=hashlib.sha256(source).hexdigest(), size_bytes=len(source), media_type="text/plain")
    creates = []

    async def capture(request):
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            creates.append(json.loads(request.content))

    async with httpx.AsyncClient(trust_env=False, event_hooks={"request": [capture]}) as http:
        client = OpenSandboxClient(os.environ["OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT"], key, http=http)
        handle = await client.create(requirements)
        request = ExecutionRequest(
            root_run_id=uuid4(), operation_id=uuid4(), requirements=requirements, source=reference,
            inputs=(), parameters={}, outputs=(OutputSpec(alias="report.json", media_type="application/json", kind="data"),),
        )
        result = await client.execute(handle, request, PreparedFiles(source=source, inputs={}), InvocationControl())
    assert creates[0]["image"] == {"uri": requirements.image}
    assert creates[0]["timeout"] == 1800
    assert creates[0]["resourceLimits"] == {"cpu": "1", "memory": "512Mi"}
    assert result.exit_code == 0
    assert json.loads(result.outputs["report.json"]) == {"profile": requirements.profile_id, "ok": True}


@pytest.mark.asyncio
async def test_pinned_snapshot_image_reference_executes_through_manager():
    """The manager runs a snapshot's immutable image id, with no tag fallback.

    Additive to the preloaded-tag journey above: that journey is historical
    evidence of a recipe tag, not proof that a pinned immutable reference
    executes. This case names the operator's own profiles file and profile, and
    refuses to count a SHA-shaped string the manager never proves.
    """
    if os.environ.get("OPENSHIP_LIVE_SANDBOX_PROFILES") != "1":
        pytest.skip("set OPENSHIP_LIVE_SANDBOX_PROFILES=1 only with separately authorized provider settings")
    snapshot_file = os.environ.get("OPENSHIP_LIVE_SANDBOX_PROFILES_FILE")
    profile = os.environ.get("OPENSHIP_LIVE_SANDBOX_PROFILE_ID")
    if not snapshot_file or not profile:
        pytest.fail("the immutable-reference case requires OPENSHIP_LIVE_SANDBOX_PROFILES_FILE and OPENSHIP_LIVE_SANDBOX_PROFILE_ID")
    for name in ("ENDPOINT", "KEY_FILE"):
        if not os.environ.get("OPENSHIP_LIVE_OPENSANDBOX_" + name):
            pytest.fail(f"the immutable-reference case requires OPENSHIP_LIVE_OPENSANDBOX_{name}")
    snapshot = Path(snapshot_file)
    if not snapshot.is_file():
        pytest.fail("OPENSHIP_LIVE_SANDBOX_PROFILES_FILE names a missing profiles file")
    catalog = decode_catalog(snapshot.read_bytes())
    entry = catalog.profiles.get(profile)
    if entry is None or not entry.enabled or not entry.runtime.smoke_passed:
        pytest.fail(f"profile {profile} is not an enabled, smoke-qualified entry in the profiles file")
    pinned = entry.runtime.image_id
    endpoint = os.environ["OPENSHIP_LIVE_OPENSANDBOX_ENDPOINT"]
    key = Path(os.environ["OPENSHIP_LIVE_OPENSANDBOX_KEY_FILE"]).read_text().strip()
    requirements = RuntimeRequirements(
        provider_id="live-phase7a",
        profile_id=profile,
        image=pinned,
        dependency_identity=entry.runtime.dependency_identity,
    )
    creates = []

    async def capture(request):
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            creates.append(json.loads(request.content))

    source = (
        "import json, os\nfrom pathlib import Path\n"
        f"report = {{'profile': {profile!r}, 'image_id': {pinned!r}, 'ok': True}}\n"
        "(Path(os.environ['OPENSHIP_OUTPUT_DIR']) / 'report.json').write_text(json.dumps(report))\n"
    ).encode()
    reference = ArtifactRef(id=uuid4(), sha256=hashlib.sha256(source).hexdigest(), size_bytes=len(source), media_type="text/plain")
    result = None
    async with httpx.AsyncClient(trust_env=False, event_hooks={"request": [capture]}) as http:
        client = OpenSandboxClient(endpoint, key, http=http)
        try:
            handle = await client.create(requirements)
            assert creates[0]["image"] == {"uri": pinned}, "the request must name the snapshot's immutable image id"
            record = await http.get(f"{endpoint}/v1/sandboxes/{handle.sandbox_id}")
            assert record.status_code == 200, record.text
            reported = (record.json().get("image") or {}).get("uri")
            if reported != pinned:
                pytest.fail(
                    f"the manager reports {reported!r} for a sandbox created from {pinned!r}; "
                    "an unproven reference is not immutable-execution qualification"
                )
            request = ExecutionRequest(
                root_run_id=uuid4(), operation_id=uuid4(), requirements=requirements, source=reference,
                inputs=(), parameters={}, outputs=(OutputSpec(alias="report.json", media_type="application/json", kind="data"),),
            )
            result = await client.execute(handle, request, PreparedFiles(source=source, inputs={}), InvocationControl())
        finally:
            await client.aclose()
    assert result.exit_code == 0
    assert json.loads(result.outputs["report.json"]) == {"profile": profile, "image_id": pinned, "ok": True}
