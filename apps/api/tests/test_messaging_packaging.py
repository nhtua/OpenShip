"""Repository-level checks for the supported Phase 6A deployment packages."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STACK_SCRIPT = ROOT / "scripts" / "openship-stack"


def test_stack_script_initializes_private_role_credentials_without_overwrite(tmp_path):
    secret_dir = tmp_path / "messaging"
    environment = {
        "PATH": os.environ["PATH"],
        "OPENSHIP_SECRETS_DIR": str(secret_dir),
    }

    first = subprocess.run(
        [str(STACK_SCRIPT), "--environment", "local", "init-messaging"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert "password" not in first.stdout.lower()
    assert secret_dir.stat().st_mode & 0o777 == 0o700
    for config_name in ("server.compose.json", "server.local.json"):
        assert (secret_dir / config_name).stat().st_mode & 0o777 == 0o600
    for role in ("api", "worker", "relay", "operator"):
        credential_file = secret_dir / f"{role}.json"
        assert credential_file.exists()
        assert credential_file.stat().st_mode & 0o777 == 0o600

    config = json.loads((secret_dir / "server.compose.json").read_text())
    users = {user["user"]: user for user in config["authorization"]["users"]}
    worker_publish = users["openship_worker"]["permissions"]["publish"]["allow"]
    assert "$JS.API.STREAM.MSG.DELETE.OS_RUNS" in worker_publish
    assert "$JS.API.STREAM.MSG.DELETE.>" not in worker_publish
    for stream in (
        "OS_RUNS",
        "OS_MAINTENANCE",
        "OS_EVENTS",
        "OS_CONTROLS",
        "OS_FAILURES",
        "OS_ADVISORIES",
    ):
        assert f"$JS.API.STREAM.INFO.{stream}" in worker_publish
    assert "permissions" not in users["openship_operator"]
    assert (
        "openship.*.v1.event.>"
        in users["openship_api"]["permissions"]["subscribe"]["allow"]
    )

    second = subprocess.run(
        [str(STACK_SCRIPT), "--environment", "local", "init-messaging"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert second.returncode != 0
    assert "refus" in (second.stdout + second.stderr).lower()


def test_stack_script_resolves_relative_secret_directory_from_repository_root(
    tmp_path,
):
    secret_dir = tmp_path / "relative-messaging"
    relative_path = os.path.relpath(secret_dir, ROOT)
    environment = {"PATH": os.environ["PATH"], "OPENSHIP_SECRETS_DIR": relative_path}

    initialized = subprocess.run(
        [str(STACK_SCRIPT), "--environment", "local", "init-messaging"],
        cwd=ROOT / "apps" / "api",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert initialized.returncode == 0, initialized.stderr
    assert (secret_dir / "operator.json").is_file()


def test_stack_script_has_explicit_environment_and_supported_operations():
    help_result = subprocess.run(
        [str(STACK_SCRIPT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert help_result.returncode == 0, help_result.stderr
    for value in (
        "local",
        "dev",
        "prod",
        "doctor",
        "bootstrap",
        "up-dependencies",
        "start",
        "stop",
        "status",
        "cutover",
        "rollback",
    ):
        assert value in help_result.stdout


def test_stack_script_rejects_unsupported_secret_overrides_and_tls_modes(tmp_path):
    secret_dir = tmp_path / "external-messaging"
    base_env = {"PATH": os.environ["PATH"], "OPENSHIP_SECRETS_DIR": str(secret_dir)}

    production_override = subprocess.run(
        [str(STACK_SCRIPT), "--environment", "prod", "status"],
        cwd=ROOT,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert production_override.returncode != 0
    assert "fixed production Compose credential path" in production_override.stderr

    tls_in_dev = subprocess.run(
        [
            str(STACK_SCRIPT),
            "--environment",
            "dev",
            "init-messaging",
            "--tls-cert",
            str(tmp_path / "server.crt"),
            "--tls-key",
            str(tmp_path / "server.key"),
            "--tls-ca",
            str(tmp_path / "ca.crt"),
        ],
        cwd=ROOT,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tls_in_dev.returncode != 0
    assert "supported only for the production Compose stack" in tls_in_dev.stderr
    assert not secret_dir.exists()


def test_production_nats_initialization_requires_tls_and_syncs_each_write(tmp_path):
    secret_dir = tmp_path / "messaging"
    sources = {}
    for name in ("server.crt", "server.key", "ca.crt"):
        source = tmp_path / name
        source.write_text(f"fixture for {name}\n")
        sources[name] = source

    initialized = subprocess.run(
        [
            str(ROOT / "scripts" / "init_messaging.py"),
            "--directory",
            str(secret_dir),
            "--tls-cert",
            str(sources["server.crt"]),
            "--tls-key",
            str(sources["server.key"]),
            "--tls-ca",
            str(sources["ca.crt"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert initialized.returncode == 0, initialized.stderr
    compose_config = json.loads((secret_dir / "server.compose.json").read_text())
    local_config = json.loads((secret_dir / "server.local.json").read_text())
    assert compose_config["tls"]["cert_file"] == "/run/nats-tls/server.crt"
    assert compose_config["jetstream"]["sync_interval"] == "always"
    assert local_config["tls"]["cert_file"] == str(secret_dir / "server.crt")
    assert (secret_dir / "server.key").stat().st_mode & 0o777 == 0o600


def test_production_compose_is_standalone_and_excludes_development_mounts():
    production = (ROOT / "docker-compose.prod.yml").read_text()

    assert "\n  build:" not in production
    assert "docker-compose.dev.yml" not in production
    assert "--reload" not in production
    assert "watchmedo" not in production
    assert "${OPENSHIP_API_IMAGE:?" in production
    assert "${OPENSHIP_WEB_IMAGE:?" in production
    assert "${NATS_SERVER_IMAGE:?" in production
    assert "./secrets:/run/secrets:ro" in production
    assert "natsdata" in production
    assert "artifactdata" in production
    assert "pgdata" in production
    assert "4222:" not in production
    assert "./.openship/messaging/prod:/run/nats-tls" not in production
    assert "server.crt:/run/nats-tls/server.crt:ro" in production
    assert "server.key:/run/nats-tls/server.key:ro" in production


def test_dev_override_preserves_native_reload_entry_points():
    development = (ROOT / "docker-compose.dev.yml").read_text()

    assert "--reload" in development
    assert "watchmedo" in development
    assert "auto-restart" in development
    assert 'command: ["pnpm", "run", "dev", "--host", "0.0.0.0"]' in development
    assert "VITE_API_PROXY_TARGET: http://api:8000" in development
    assert "/app/node_modules" in development
    assert "!override" in development


def test_images_use_locked_runtime_dependencies_and_exclude_secret_contexts():
    api = (ROOT / "apps" / "api" / "Dockerfile").read_text()
    web = (ROOT / "apps" / "web" / "Dockerfile").read_text()
    dockerignore_files = (
        ROOT / "apps" / "api" / ".dockerignore",
        ROOT / "apps" / "web" / ".dockerignore",
    )

    assert "uv sync --frozen --no-dev" in api
    assert "uv sync --frozen --all-groups" in api
    assert "pnpm install --frozen-lockfile" in web
    assert "node:22" in web
    for dockerignore in dockerignore_files:
        ignored = dockerignore.read_text()
        assert ".env" in ignored
        assert "secrets" in ignored
        assert ".venv" in ignored or "node_modules" in ignored


def test_production_env_validator_requires_private_values_and_immutable_images(
    tmp_path,
):
    env_file = tmp_path / ".env.prod"
    nats_image = next(
        line.split("=", 1)[1]
        for line in (ROOT / "deploy" / "versions.env").read_text().splitlines()
        if line.startswith("NATS_SERVER_IMAGE=")
    )
    env_file.write_text(
        "\n".join(
            (
                "OPENSHIP_API_IMAGE=registry.example/api@sha256:" + "a" * 64,
                "OPENSHIP_WEB_IMAGE=registry.example/web@sha256:" + "b" * 64,
                "POSTGRES_IMAGE=registry.example/postgres@sha256:" + "c" * 64,
                f"NATS_SERVER_IMAGE={nats_image}",
                "DATABASE_URL=postgresql://openship:" + "p" * 40 + "@postgres/openship",
                "POSTGRES_PASSWORD=" + "p" * 40,
                "JWT_SECRET=" + "j" * 40,
                "ARTIFACT_STORAGE_ID=4d4ee5c7-c013-4d39-8f49-7d0d7d2f635a",
                "CONNECTOR_BROWSER_ORIGINS=https://openship.example.test",
                "",
            )
        )
    )
    env_file.chmod(0o600)

    accepted = subprocess.run(
        [str(ROOT / "scripts" / "check_prod_env.py"), "--file", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr

    env_file.chmod(0o644)
    rejected = subprocess.run(
        [str(ROOT / "scripts" / "check_prod_env.py"), "--file", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "mode 0600" in rejected.stderr


def test_production_env_validator_requires_postgres_scheme_and_compose_port(tmp_path):
    env_file = tmp_path / ".env.prod"
    nats_image = next(
        line.split("=", 1)[1]
        for line in (ROOT / "deploy" / "versions.env").read_text().splitlines()
        if line.startswith("NATS_SERVER_IMAGE=")
    )
    settings = (
        "OPENSHIP_API_IMAGE=registry.example/api@sha256:" + "a" * 64,
        "OPENSHIP_WEB_IMAGE=registry.example/web@sha256:" + "b" * 64,
        "POSTGRES_IMAGE=registry.example/postgres@sha256:" + "c" * 64,
        f"NATS_SERVER_IMAGE={nats_image}",
        "DATABASE_URL=mysql://openship:" + "p" * 40 + "@postgres:5432/openship",
        "POSTGRES_PASSWORD=" + "p" * 40,
        "JWT_SECRET=" + "j" * 40,
        "ARTIFACT_STORAGE_ID=4d4ee5c7-c013-4d39-8f49-7d0d7d2f635a",
        "CONNECTOR_BROWSER_ORIGINS=https://openship.example.test",
        "",
    )
    contents = "\n".join(settings)
    env_file.write_text(contents)
    env_file.chmod(0o600)

    wrong_scheme = subprocess.run(
        [str(ROOT / "scripts" / "check_prod_env.py"), "--file", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert wrong_scheme.returncode != 0

    env_file.write_text(
        contents.replace("mysql://", "postgresql://").replace(
            "@postgres:5432/", "@postgres:5433/"
        )
    )
    env_file.chmod(0o600)
    wrong_port = subprocess.run(
        [str(ROOT / "scripts" / "check_prod_env.py"), "--file", str(env_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert wrong_port.returncode != 0
