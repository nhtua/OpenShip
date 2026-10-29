"""Run a trusted HTTP compatibility fixture; this does not qualify isolation.

From apps/api: python -m scripts.qualify_opensandbox --trusted-probe
Supply OPENSHIP_SANDBOX_SERVER_URL, OPENSHIP_SANDBOX_API_KEY and
OPENSHIP_SANDBOX_PYTHON_IMAGE for an operator-controlled test deployment.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from uuid import UUID, uuid4

import httpx

from src.openship.sandboxes.client import OpenSandboxClient, SandboxProtocolError

TRUSTED_SOURCE = (
    b"import os\nimport sys\n"
    b'print(f"openship-compatibility {os.getuid()} {os.getgid()}")\n'
    b'print("openship-stderr", file=sys.stderr)\n'
)


async def _confirm_absent(http: httpx.AsyncClient, url: str, api_key: str) -> None:
    try:
        async with asyncio.timeout(10):
            while True:
                response = await http.get(
                    url, headers={"OPEN-SANDBOX-API-KEY": api_key},
                    timeout=5, follow_redirects=False,
                )
                if response.status_code == 404:
                    return
                if response.status_code != 200:
                    break
                await asyncio.sleep(0.1)
    except (httpx.RequestError, TimeoutError):
        pass
    raise SandboxProtocolError("sandbox cleanup is unconfirmed; reconcile the attempt before retrying")


async def qualify(
    http: httpx.AsyncClient, *, server_url: str, api_key: str, image: str, attempt_id: UUID,
) -> dict:
    """Check one fixed program through the same HTTP adapter used by OpenShip."""
    client = OpenSandboxClient(http, server_url, api_key, image)
    digest = hashlib.sha256(TRUSTED_SOURCE).hexdigest()
    sandbox_id = await client.create(attempt_id)
    try:
        await client.wait_ready(sandbox_id)
        await client.upload_source(sandbox_id, TRUSTED_SOURCE, digest)
        await client.verify_source(sandbox_id, digest, len(TRUSTED_SOURCE))
        result = await client.run_python(sandbox_id)
        if result.stdout != "openship-compatibility 1000 1000\n" or result.stderr != "openship-stderr\n":
            raise SandboxProtocolError("trusted fixture output or non-root identity did not match")
    finally:
        await client.destroy(sandbox_id)
        await _confirm_absent(http, server_url.rstrip("/") + "/v1/sandboxes/" + sandbox_id, api_key)
    return {
        "attempt_id": str(attempt_id), "image": image, "source_sha256": digest,
        "protocol_compatible": True, "manager_deletion_confirmed": True, "isolation_qualified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trusted-probe", action="store_true", required=True,
                        help="create a disposable sandbox and execute the fixed trusted fixture")
    parser.parse_args()
    names = ("OPENSHIP_SANDBOX_SERVER_URL", "OPENSHIP_SANDBOX_API_KEY", "OPENSHIP_SANDBOX_PYTHON_IMAGE")
    settings = [os.environ.get(name, "") for name in names]
    if not all(settings):
        parser.error("set " + ", ".join(names))
    attempt_id = uuid4()
    # Preserve correlation if a create response is lost; never automatically retry a POST.
    print(json.dumps({"attempt_id": str(attempt_id), "status": "starting"}), flush=True)

    async def run() -> dict:
        async with httpx.AsyncClient(trust_env=False) as http:
            return await qualify(http, server_url=settings[0], api_key=settings[1],
                                 image=settings[2], attempt_id=attempt_id)

    try:
        receipt = asyncio.run(run())
    except (SandboxProtocolError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
