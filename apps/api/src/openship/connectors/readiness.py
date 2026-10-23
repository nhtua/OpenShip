"""Local readiness checks for exact, already-pinned Connector references."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ..capabilities.contracts import ConnectorRef
from ..connectors.aws.broker import AwsRoleBroker
from ..connectors.credentials.broker import (
    ConnectorCredentialError,
    SearchCredentialBroker,
)
from ..execution.aws_sdk import AwsSdkError


@dataclass(frozen=True, slots=True)
class ConnectorReadinessFailure:
    reference: ConnectorRef
    reason: str


def preflight_connector_references(
    *,
    session_factory,
    project_id: UUID,
    references: tuple[ConnectorRef, ...],
) -> ConnectorReadinessFailure | None:
    """Decrypt-check pinned secrets and metadata without external dispatch."""
    search_broker = SearchCredentialBroker(session_factory)
    aws_broker = AwsRoleBroker(session_factory)
    for reference in references:
        try:
            if reference.kind in {"brave", "tavily"}:
                search_broker.preflight(
                    project_id=project_id,
                    kind=reference.kind,
                    reference=reference,
                )
            elif reference.kind == "aws":
                aws_broker.preflight(project_id=project_id, reference=reference)
            else:
                return ConnectorReadinessFailure(reference, "connector_unavailable")
        except ConnectorCredentialError as error:
            return ConnectorReadinessFailure(reference, _blocker_reason(error.code))
        except AwsSdkError:
            return ConnectorReadinessFailure(reference, "connector_unavailable")
    return None


def _blocker_reason(code: str) -> str:
    if code == "connector_expired":
        return "connector_expired"
    if code == "credential_store_unavailable":
        return "credential_store_unavailable"
    return "connector_unavailable"
