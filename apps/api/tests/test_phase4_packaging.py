"""Static deployment checks for the Phase 4A runtime boundary."""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[3]


def _service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"^  {re.escape(service)}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|^volumes:\n)",
        compose,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing Compose service: {service}"
    return match.group(1)


def test_artifact_volume_is_shared_and_search_keys_are_runtime_only():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    api = _service_block(compose, "api")
    worker = _service_block(compose, "worker")

    assert "artifactdata:/var/lib/openship/artifacts" in api
    assert "artifactdata:/var/lib/openship/artifacts" in worker
    assert re.search(r"(?m)^  artifactdata:\s*$", compose)
    assert "ARTIFACT_ROOT: /var/lib/openship/artifacts" in api
    assert "ARTIFACT_ROOT: /var/lib/openship/artifacts" in worker
    assert "ARTIFACT_STORAGE_ID:" in api and "ARTIFACT_STORAGE_ID:" in worker

    assert "BRAVE_SEARCH_API_KEY" not in api
    assert "TAVILY_API_KEY" not in api
    assert "BRAVE_SEARCH_API_KEY" in worker
    assert "TAVILY_API_KEY" in worker
    assert "TOOL_ENABLED_IDS" in worker


def test_compose_ports_and_no_sandbox_runtime():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    services = yaml.safe_load(compose)["services"]
    assert "ports" not in services["postgres"]
    assert "ports" not in services["nats"]
    assert services["nats-nui"]["ports"] == ["127.0.0.1:${DEV_NATS_NUI_PORT:-31311}:31311"]
    assert services["api"]["ports"] == ["${DEV_BIND_ADDRESS:-0.0.0.0}:${API_PORT:-8000}:8000"]
    assert services["web"]["ports"] == ["${DEV_BIND_ADDRESS:-0.0.0.0}:${WEB_PORT:-8080}:80"]
    dev_ports = yaml.safe_load((REPO_ROOT / "docker-compose.dev-ports.yml").read_text())
    assert dev_ports["services"]["web"]["ports"] == [
        "${DEV_BIND_ADDRESS:-0.0.0.0}:${DEV_WEB_PORT:-5173}:5173"
    ]

    lowered = compose.lower()
    for forbidden in (
        "/var/run/docker.sock",
        "runsc",
        "gvisor",
        "privileged:",
        "docker:dind",
        "openship-sandbox",
    ):
        assert forbidden not in lowered


def test_production_web_container_routes_api_and_sse_to_fastapi():
    dockerfile = (REPO_ROOT / "apps/web/Dockerfile").read_text()
    nginx = (REPO_ROOT / "apps/web/nginx.conf").read_text()
    assert "COPY nginx.conf /etc/nginx/conf.d/default.conf" in dockerfile
    assert "location /api/" in nginx
    assert "proxy_pass http://api:8000/api/" in nginx
    assert "proxy_buffering off;" in nginx


def test_examples_leave_tool_availability_managed_and_search_secrets_empty():
    root_example = (REPO_ROOT / ".env.example").read_text()
    api_example = (REPO_ROOT / "apps/api/.env.example").read_text()
    for example in (root_example, api_example):
        values = dict(
            line.split("=", 1)
            for line in example.splitlines()
            if line and not line.lstrip().startswith("#") and "=" in line
        )
        assert "TOOL_ENABLED_IDS" not in values
        assert values["BRAVE_SEARCH_API_KEY"] == ""
        assert values["TAVILY_API_KEY"] == ""


def test_runtime_budget_settings_reach_both_processes():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    expected = {
        "WORKFLOW_RUN_TIMEOUT_SECONDS": "1800",
        "MODEL_CONTEXT_TOKENS": "128000",
        "MODEL_MAX_CONTEXT_BYTES": "2097152",
    }
    for service in ("api", "worker"):
        block = _service_block(compose, service)
        for name, default in expected.items():
            assert f"{name}: ${{{name}:-{default}}}" in block

    from dotenv import dotenv_values

    from src.openship.config import Settings
    from src.openship.providers.registry import load_profiles

    for path in (REPO_ROOT / ".env.example", REPO_ROOT / "apps/api/.env.example"):
        values = dotenv_values(path)
        for name, default in expected.items():
            assert values[name] == default
        installed = Settings(_env_file=path)
        pinned = load_profiles(installed).pin_selection(None)
        assert pinned.limits.run_timeout_seconds == 1800
        assert pinned.primary.capabilities.context_tokens == 128000
