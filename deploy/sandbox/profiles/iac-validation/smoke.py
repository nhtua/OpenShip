"""No network, remote modules, providers, policy downloads or credentials."""

import io
import json
import subprocess
import tempfile
from importlib.metadata import version
from pathlib import Path

import graphviz
import jsonschema
import yaml
from PIL import Image


def command(args, expected):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == expected, (args[0], result.returncode, result.stdout[-2000:], result.stderr[-2000:])
    return result.stdout


schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}
jsonschema.validate({"count": 7}, schema)
try:
    jsonschema.validate({"count": "seven"}, schema)
except jsonschema.ValidationError:
    pass
else:
    raise AssertionError("JSON Schema accepted the invalid fixture")
assert yaml.safe_load("service: api\nreplicas: 2\n") == {"service": "api", "replicas": 2}

with tempfile.TemporaryDirectory() as temporary:
    work = Path(temporary)
    terraform = work / "main.tf"
    check = ["checkov", "--file", str(terraform), "--framework", "terraform", "--check", "CKV_AWS_20",
             "--skip-download", "--skip-results-upload", "--download-external-modules", "false", "--output", "json"]
    for acl, status in (("public-read", 1), ("private", 0)):
        terraform.write_text(f'resource "aws_s3_bucket" "fixture" {{\n  bucket = "offline-fixture"\n  acl = "{acl}"\n}}\n')
        report = json.loads(command(check, status))
        assert report["summary"]["failed"] == status
        if status:
            assert report["results"]["failed_checks"][0]["check_id"] == "CKV_AWS_20"
        else:
            assert report["summary"]["passed"] >= 1
    terraform.write_text('variable "fixture" {\n  default = 1\n}\n')
    lint = ["tflint", f"--chdir={work}", "--only=terraform_required_version", "--format=json"]
    bad = json.loads(command(lint, 2))
    assert any(issue["rule"]["name"] == "terraform_required_version" for issue in bad["issues"])
    terraform.write_text('terraform {\n  required_version = ">= 1.0"\n}\n')
    assert json.loads(command(lint, 0))["issues"] == []

diagram = graphviz.Digraph(format="png")
diagram.edge("input", "validated")
png = diagram.pipe()
assert png.startswith(b"\x89PNG\r\n\x1a\n")
with Image.open(io.BytesIO(png)) as decoded:
    decoded.load()
    assert decoded.width > 0 and decoded.height > 0
print(json.dumps({"profile": "iac-validation", "schema": "accept/reject", "yaml": "loaded",
                  "checkov": "violation/corrected", "tflint": "violation/corrected", "graphviz": "decoded PNG",
                  "versions": {name: version(name) for name in ("checkov", "jsonschema", "PyYAML", "graphviz")}}))
