"""Credential-free CLI startup and local filesystem fixtures, never cloud calls."""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def command(args, expected=0):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == expected, (args[0], result.returncode, result.stderr[-2000:])
    return result.stdout.strip()


assert sys.version_info >= (3, 12)
pins = json.loads(Path("/opt/openship/tool-versions.json").read_text())
for line in Path("/opt/openship/os-packages.lock").read_text().splitlines():
    if line and not line.startswith("#"):
        name, version = line.split("=", 1)
        assert command(["dpkg-query", "-W", "-f=${Version}", name]) == version
assert "GNU bash" in command(["bash", "--version"])
assert command(["bash", "-c", "printf '%s' $((6 * 7))"]) == "42"
assert command(["git", "--version"]).startswith("git version ")
aws = command(["aws", "--version"])
assert re.search(r"aws-cli/([^ ]+)", aws)[1] == pins["aws"]
azure = json.loads(command(["az", "version", "--output", "json"]))
azure_pin = command(["/opt/azure-cli/bin/python", "-c", "import importlib.metadata; print(importlib.metadata.version('azure-cli'))"])
assert azure["azure-cli"] == azure_pin
gcloud = json.loads(command(["gcloud", "version", "--format=json"]))
assert gcloud["Google Cloud SDK"] == pins["gcloud"]
assert json.loads(command(["terraform", "version", "-json"]))["terraform_version"] == pins["terraform"]
assert re.search(r"version v?([0-9.]+)", command(["terragrunt", "--version"]))[1] == pins["terragrunt"]
with tempfile.TemporaryDirectory() as temporary:
    work = Path(temporary)
    command(["git", "init", "--quiet", str(work)])
    (work / "fixture.txt").write_text("offline\n")
    command(["git", "-C", str(work), "add", "fixture.txt"])
    assert command(["git", "-C", str(work), "diff", "--cached", "--name-only"]) == "fixture.txt"
    terraform = work / "main.tf"
    terraform.write_text('locals {\nanswer=42\n}\n')
    command(["terraform", "fmt", "-check", str(terraform)], 3)
    command(["terraform", "fmt", str(terraform)])
    command(["terraform", "fmt", "-check", str(terraform)])
print(json.dumps({"profile": "devops-shell", "archive_versions": pins, "azure": azure_pin,
                  "gcloud_components": gcloud, "bash": "42", "git": "staged", "terraform": "formatted"}))
