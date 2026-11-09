"""Build-time installation of the manifest's verified CLI archives only."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen


def download(url, digest):
    with urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("archive checksum mismatch; refusing installation")
    return data


def install_zip_binary(url, digest, target, member):
    data = download(url, digest)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        body = archive.read(member)
    Path(target).write_bytes(body)
    Path(target).chmod(0o755)


def main():
    def entry(name):
        return os.environ[f"{name}_URL"], os.environ[f"{name}_SHA256"]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(download(*entry("AWS")))) as archive:
            for member in archive.infolist():
                relative = Path(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe AWS archive member")
                archive.extract(member, root)
                if not member.is_dir():
                    (root / relative).chmod((member.external_attr >> 16) & 0o777 or 0o644)
        subprocess.run([str(root / "aws/install"), "-i", "/opt/aws-cli", "-b", "/usr/local/bin"], check=True)
    Path("/opt/gcloud").mkdir()
    with tarfile.open(fileobj=io.BytesIO(download(*entry("GCLOUD"))), mode="r:gz") as archive:
        archive.extractall("/opt/gcloud", filter="data")
    install_zip_binary(*entry("TERRAFORM"), "/usr/local/bin/terraform", "terraform")
    terragrunt = Path("/usr/local/bin/terragrunt")
    terragrunt.write_bytes(download(*entry("TERRAGRUNT")))
    terragrunt.chmod(0o755)
    Path("/opt/openship").mkdir(exist_ok=True)
    Path("/opt/openship/tool-versions.json").write_text(json.dumps({
        name.lower(): os.environ[f"{name}_VERSION"] for name in ("AWS", "GCLOUD", "TERRAFORM", "TERRAGRUNT")
    }))


if __name__ == "__main__":
    main()
