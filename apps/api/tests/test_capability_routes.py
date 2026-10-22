import uuid

import pytest
from fastapi import HTTPException, Request

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.capabilities.catalog import list_capabilities
from src.openship.capabilities.routes import get_capabilities, get_project_capabilities
from src.openship.capabilities.routes import router as capabilities_router
from src.openship.config import settings
from src.openship.connectors.selection import ConnectorSelectionError
from src.openship.database.session import SessionLocal
from src.openship.execution.models import ToolAvailability
from src.openship.main import app
from src.openship.workspace.models import Project


def test_capability_catalog_requires_authentication():
    route = next(
        route
        for route in capabilities_router.routes
        if route.path == "/api/capabilities"
    )
    dependencies = {dependency.call for dependency in route.dependant.dependencies}

    assert require_jwt in dependencies
    assert "/api/capabilities" in app.openapi()["paths"]

    unauthenticated = Request(
        {"type": "http", "headers": [], "method": "GET", "path": "/api/capabilities"}
    )
    with pytest.raises(HTTPException) as error:
        require_jwt(unauthenticated)
    assert error.value.status_code == 401


def test_catalog_auth_and_secret_redaction(monkeypatch):
    sentinel = "phase4-catalog-secret-sentinel"
    monkeypatch.setattr(
        settings, "tool_enabled_ids", "brave_web_search,tavily_web_search"
    )
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", sentinel)
    monkeypatch.setenv("TAVILY_API_KEY", sentinel)
    db = SessionLocal()
    db.query(ToolAvailability).delete(synchronize_session=False)
    db.commit()
    user = User(
        username="catalog-test", email="catalog@example.com", password_hash="test-only"
    )

    summary = get_capabilities(user, db)
    body = "".join(item.model_dump_json() for item in summary)

    by_id = {item.id: item for item in summary}
    assert set(by_id) == {
        "brave_web_search",
        "tavily_web_search",
        "workspace_list_files",
        "workspace_read_file",
    }
    assert all(not item.available for item in summary)
    assert by_id["brave_web_search"].unavailable_reason == "runtime_unavailable"
    assert by_id["tavily_web_search"].unavailable_reason == "runtime_unavailable"
    assert by_id["workspace_list_files"].unavailable_reason == "disabled"
    assert by_id["workspace_read_file"].unavailable_reason == "disabled"
    assert sentinel not in body
    assert "credential_binding_ref" not in body
    assert "BRAVE_SEARCH_API_KEY" not in body
    assert "TAVILY_API_KEY" not in body
    assert "endpoint" not in body
    assert "implementation_module" not in body
    db.close()


def test_catalog_only_checks_presence_not_key_values(monkeypatch):
    sentinel = "phase4-catalog-secret-sentinel"
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", sentinel)
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    catalog = {item.id: item for item in list_capabilities()}

    assert catalog["brave_web_search"].available is True
    assert catalog["brave_web_search"].unavailable_reason is None
    assert catalog["tavily_web_search"].available is False
    assert catalog["tavily_web_search"].unavailable_reason == "disabled"
    assert catalog["workspace_list_files"].unavailable_reason == "disabled"
    assert catalog["workspace_read_file"].unavailable_reason == "disabled"


def test_project_catalog_reports_permission_separately_from_connector_readiness(
    monkeypatch,
):
    monkeypatch.setattr(settings, "tool_enabled_ids", "brave_web_search")
    def missing(*_args, **_kwargs):
        raise ConnectorSelectionError("connector_required")

    monkeypatch.setattr("src.openship.capabilities.routes.resolve_connector", missing)
    db = SessionLocal()
    user = User(
        username=f"project-catalog-{uuid.uuid4().hex[:12]}",
        email=f"project-catalog-{uuid.uuid4().hex}@example.test",
        password_hash="test-only",
    )
    db.add(user)
    db.flush()
    project = Project(owner_user_id=user.id, name=f"Catalog {user.id}")
    db.add(project)
    db.commit()
    try:
        summary = {
            item.id: item
            for item in get_project_capabilities(project.id, user, db)
        }["brave_web_search"]
        assert summary.permitted is True
        assert summary.required_connector_kinds == ("brave",)
        assert summary.connector_readiness == {"brave": "required"}

        monkeypatch.setattr(settings, "tool_enabled_ids", "")
        disabled = {
            item.id: item
            for item in get_project_capabilities(project.id, user, db)
        }["brave_web_search"]
        assert disabled.permitted is False
        assert disabled.connector_readiness == {"brave": "required"}
    finally:
        db.close()
