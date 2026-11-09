"""Lock generation must resolve transitives, not merely label direct pins."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import lock_sandbox_dependencies


def wheel(directory, name, requires=()):
    path = directory / f"{name}-1.0-py3-none-any.whl"
    dist = f"{name}-1.0.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}.py", "VALUE = 42\n")
        archive.writestr(
            f"{dist}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
            + "".join(f"Requires-Dist: {dep}\n" for dep in requires),
        )
        archive.writestr(f"{dist}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{dist}/RECORD", "")
    return path


def test_lock_resolves_transitive_closure_and_verifies_hashes_offline(tmp_path, monkeypatch):
    directory = tmp_path / "wheels"
    directory.mkdir()
    wheel(directory, "fixture_parent", ("fixture_child==1.0",))
    wheel(directory, "fixture_child")
    sandbox = tmp_path / "deploy/sandbox"
    profile = sandbox / "profiles/standard"
    profile.mkdir(parents=True)
    (sandbox / "pins.json").write_text(json.dumps({"uv_version": "0.12.23", "python_version": "3.12"}))
    (sandbox / "build-requirements.lock").write_text("# no build deps for fixture wheels\n")
    (profile / "requirements.in").write_text("fixture-parent\n")
    other = sandbox / "profiles/other/requirements.lock"
    other.parent.mkdir()
    other.write_text("do not touch\n")
    monkeypatch.setenv("UV_NO_INDEX", "true")
    monkeypatch.setenv("UV_FIND_LINKS", str(directory))
    assert lock_sandbox_dependencies.main(["--root", str(tmp_path), "standard"]) == 0
    lock = profile / "requirements.lock"
    generated = lock.read_text()
    assert "fixture-child==1.0" in generated
    assert "fixture-parent==1.0" in generated
    assert "--hash=sha256:" in generated
    assert other.read_text() == "do not touch\n"
    # Failed resolution must leave the previous usable lock intact.
    (profile / "requirements.in").write_text("nonexistent-offline-fixture\n")
    assert lock_sandbox_dependencies.main(["--root", str(tmp_path), "standard"]) == 1
    assert lock.read_text() == generated
    env = tmp_path / "workload"
    subprocess.run(["uv", "venv", str(env), "--python", "3.12"], check=True, capture_output=True)
    bad = tmp_path / "bad.lock"
    import re

    bad.write_text(re.sub(r"sha256:[0-9a-f]{64}", "sha256:" + "0" * 64, generated))
    result = subprocess.run(
        ["uv", "pip", "sync", "--require-hashes", "--python", str(env), str(bad)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "Hash mismatch" in result.stderr
    subprocess.run(
        ["uv", "pip", "sync", "--require-hashes", "--python", str(env), str(lock)],
        check=True, capture_output=True,
    )
    subprocess.run([str(env / "bin/python"), "-c", "import fixture_parent, fixture_child"], check=True)
