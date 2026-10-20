"""Static deployment checks for the Phase 4A runtime boundary."""

import re
from pathlib import Path

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


def test_compose_ports_are_loopback_only_and_have_no_sandbox_runtime():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    ports = re.findall(r'(?m)^\s+- "([^"\n]+:[^"\n]+)"\s*$', compose)
    assert len(ports) == 3
    assert all(port.startswith("127.0.0.1:") for port in ports)

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


def test_examples_keep_search_optional_and_secret_values_empty():
    root_example = (REPO_ROOT / ".env.example").read_text()
    api_example = (REPO_ROOT / "apps/api/.env.example").read_text()
    for example in (root_example, api_example):
        values = dict(
            line.split("=", 1)
            for line in example.splitlines()
            if line and not line.lstrip().startswith("#") and "=" in line
        )
        assert values["TOOL_ENABLED_IDS"] == ""
        assert values["BRAVE_SEARCH_API_KEY"] == ""
        assert values["TAVILY_API_KEY"] == ""
