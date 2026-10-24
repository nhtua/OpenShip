from __future__ import annotations

import uuid

import pytest

from src.openship.capabilities.contracts import ConnectorRef
from src.openship.connectors.credentials.broker import ConnectorCredentialError
from src.openship.connectors.readiness import preflight_connector_references


def _reference(kind: str, visibility: str = "project") -> ConnectorRef:
    return ConnectorRef(
        id=uuid.uuid4(),
        kind=kind,
        configuration_revision=1,
        visibility=visibility,
        credential_version_id=uuid.uuid4(),
    )


@pytest.mark.parametrize(
    "code, reason",
    [
        ("connector_expired", "connector_expired"),
        ("credential_store_unavailable", "credential_store_unavailable"),
        ("connector_revoked", "connector_unavailable"),
    ],
)
def test_preflight_maps_secret_free_failures_without_provider_dispatch(
    monkeypatch, code, reason
):
    reference = _reference("brave", "global")
    calls = []

    def unavailable(_self, **kwargs):
        calls.append(kwargs)
        raise ConnectorCredentialError(code)

    monkeypatch.setattr(
        "src.openship.connectors.readiness.SearchCredentialBroker.preflight",
        unavailable,
    )

    failure = preflight_connector_references(
        session_factory=lambda: pytest.fail("preflight must not contact a provider"),
        project_id=uuid.uuid4(),
        references=(reference,),
    )

    assert failure is not None
    assert failure.reference == reference
    assert failure.reason == reason
    assert len(calls) == 1
    assert "secret" not in repr(failure).lower()


def test_preflight_checks_each_exact_connector_once(monkeypatch):
    search = _reference("tavily", "global")
    aws = _reference("aws")
    seen = []
    monkeypatch.setattr(
        "src.openship.connectors.readiness.SearchCredentialBroker.preflight",
        lambda _self, **kwargs: seen.append(("search", kwargs["reference"])),
    )
    monkeypatch.setattr(
        "src.openship.connectors.readiness.AwsRoleBroker.preflight",
        lambda _self, **kwargs: seen.append(("aws", kwargs["reference"])),
    )

    failure = preflight_connector_references(
        session_factory=object,
        project_id=uuid.uuid4(),
        references=(search, aws),
    )

    assert failure is None
    assert seen == [("search", search), ("aws", aws)]
