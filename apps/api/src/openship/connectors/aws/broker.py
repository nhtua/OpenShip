"""Project-scoped AWS role sessions and invocation-only SDK access."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ...capabilities.contracts import ConnectorRef, canonical_json
from ...config import settings
from ...connectors.contracts import ConnectorConfiguration
from ...connectors.credentials.broker import ConnectorCredentialError
from ...connectors.credentials.dependencies import get_credential_store
from ...connectors.credentials.recovery import managed_dispatch_is_locked
from ...connectors.credentials.store import CredentialContext, CredentialEnvelope
from ...connectors.models import Connector, ConnectorRevision, CredentialVersion
from ...execution.aws_sdk import AwsSdkAuth, AwsSdkContext
from ...execution.aws_transport import AwsRequestBudget, AwsSdkError
from ...providers.control import InvocationControl
from .clients import AwsRoleAccess
from .contracts import AwsAssumedRole, AwsBootstrapAuth, AwsRoleCacheKey

_ROLE_ARN = re.compile(
    r"^arn:aws:iam::(?P<account>\d{12}):role/"
    r"(?:[A-Za-z0-9+=,.@_-]+/)*(?P<name>[A-Za-z0-9+=,.@_-]{1,64})$"
)
_ROLE_SESSION_PREFIX = "openship-"
_REQUEST_TIMEOUT_SECONDS = 30.0
_SESSION_DURATION_SECONDS = 900
_RENEW_BEFORE_SECONDS = 120


class AwsBrokerError(AwsSdkError):
    """Fixed, secret-safe AWS broker refusal."""


class _Binding:
    __slots__ = ("cache_key", "configuration", "source_auth")

    def __init__(
        self,
        configuration: ConnectorConfiguration,
        cache_key: AwsRoleCacheKey,
        source_auth: AwsBootstrapAuth | None,
    ):
        self.configuration = configuration
        self.cache_key = cache_key
        self.source_auth = source_auth


class AwsRoleBroker:
    """Authorize a pinned project Connector and issue scoped temporary roles."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        credential_store_factory=None,
        sdk_context_factory=None,
        transport_factory=None,
        clock: Callable[[], datetime] | None = None,
    ):
        self._session_factory = session_factory
        self._credential_store_factory = credential_store_factory or get_credential_store
        self._sdk_context_factory = sdk_context_factory or AwsSdkContext
        self._transport_factory = transport_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._guard = threading.RLock()
        self._sessions: dict[AwsRoleCacheKey, AwsAssumedRole] = {}
        self._inflight: dict[AwsRoleCacheKey, concurrent.futures.Future] = {}

    async def acquire(
        self,
        *,
        project_id: uuid.UUID,
        reference: ConnectorRef,
        run_id: uuid.UUID,
        control: InvocationControl,
        request_budget: AwsRequestBudget | None = None,
    ) -> AwsRoleAccess:
        if not isinstance(run_id, uuid.UUID):
            raise AwsBrokerError("aws_configuration_error")
        request_budget = request_budget or AwsRequestBudget()
        binding = self._read_binding(
            project_id=project_id,
            reference=reference,
            include_auth=False,
        )
        now = self._now()
        leader = False
        with self._guard:
            self._purge_expired(now)
            cached = self._sessions.get(binding.cache_key)
            if self._usable(cached, now):
                session = cached
                future = None
            else:
                future = self._inflight.get(binding.cache_key)
                if future is None:
                    future = concurrent.futures.Future()
                    self._inflight[binding.cache_key] = future
                    leader = True
                session = None

        if session is not None:
            context = self._new_context(
                auth=session.auth,
                region=binding.configuration.region,
                control=control,
                request_budget=request_budget,
            )
            return AwsRoleAccess(
                broker=self,
                project_id=project_id,
                reference=reference,
                run_id=run_id,
                control=control,
                request_budget=request_budget,
                configuration=binding.configuration,
                session=session,
                sdk_context=context,
            )

        assert future is not None
        if not leader:
            session = await self._wait_for_session(future, control)
            context = self._new_context(
                auth=session.auth,
                region=binding.configuration.region,
                control=control,
                request_budget=request_budget,
            )
            return AwsRoleAccess(
                broker=self,
                project_id=project_id,
                reference=reference,
                run_id=run_id,
                control=control,
                request_budget=request_budget,
                configuration=binding.configuration,
                session=session,
                sdk_context=context,
            )

        try:
            full_binding = self._read_binding(
                project_id=project_id,
                reference=reference,
                include_auth=True,
            )
            if full_binding.cache_key != binding.cache_key or full_binding.source_auth is None:
                raise ConnectorCredentialError("connector_scope_denied")
            session, context = await self._assume_and_verify(
                project_id=project_id,
                reference=reference,
                configuration=full_binding.configuration,
                source_auth=full_binding.source_auth,
                run_id=run_id,
                control=control,
                request_budget=request_budget,
            )
            try:
                current_binding = self._read_binding(
                    project_id=project_id,
                    reference=reference,
                    include_auth=False,
                )
                if current_binding.cache_key != binding.cache_key:
                    raise ConnectorCredentialError("connector_scope_denied")
            except BaseException:
                await context.aclose()
                raise
            with self._guard:
                self._sessions[binding.cache_key] = session
            future.set_result(session)
            return AwsRoleAccess(
                broker=self,
                project_id=project_id,
                reference=reference,
                run_id=run_id,
                control=control,
                request_budget=request_budget,
                configuration=full_binding.configuration,
                session=session,
                sdk_context=context,
            )
        except asyncio.CancelledError:
            failure = AwsBrokerError("aws_cancelled")
            if not future.done():
                future.set_exception(failure)
            raise
        except ConnectorCredentialError as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        except AwsSdkError as exc:
            failure = AwsBrokerError(exc.code)
            if not future.done():
                future.set_exception(failure)
            raise failure from None
        except Exception:  # noqa: BLE001 - database, SDK and parser details stay private.
            failure = AwsBrokerError("aws_unavailable")
            if not future.done():
                future.set_exception(failure)
            raise failure from None
        finally:
            with self._guard:
                if self._inflight.get(binding.cache_key) is future:
                    self._inflight.pop(binding.cache_key, None)

    def _read_binding(
        self,
        *,
        project_id: uuid.UUID,
        reference: ConnectorRef,
        include_auth: bool,
    ) -> _Binding:
        if managed_dispatch_is_locked():
            raise ConnectorCredentialError("connector_unavailable")
        if (
            not isinstance(reference, ConnectorRef)
            or not isinstance(reference.id, uuid.UUID)
            or reference.kind != "aws"
            or reference.visibility != "project"
            or not isinstance(project_id, uuid.UUID)
        ):
            raise ConnectorCredentialError("connector_scope_denied")
        with self._session_factory() as db:
            connector = (
                db.query(Connector)
                .filter(Connector.id == reference.id)
                .first()
            )
            if (
                connector is None
                or connector.kind != "aws"
                or connector.visibility != "project"
                or connector.project_id != project_id
            ):
                raise ConnectorCredentialError("connector_scope_denied")
            if connector.revoked_at is not None:
                self._forget_connector(connector.id)
                raise ConnectorCredentialError("connector_revoked")
            if not connector.enabled:
                raise ConnectorCredentialError("connector_unavailable")

            revision = (
                db.query(ConnectorRevision)
                .filter(
                    ConnectorRevision.connector_id == connector.id,
                    ConnectorRevision.revision == reference.configuration_revision,
                    ConnectorRevision.credential_version_id
                    == reference.credential_version_id,
                    ConnectorRevision.visibility == "project",
                    ConnectorRevision.project_id == project_id,
                )
                .first()
            )
            credential = (
                db.query(CredentialVersion)
                .filter(
                    CredentialVersion.id == reference.credential_version_id,
                    CredentialVersion.connector_id == connector.id,
                    CredentialVersion.visibility == "project",
                    CredentialVersion.project_id == project_id,
                    CredentialVersion.kind == "aws_bootstrap",
                )
                .first()
            )
            if revision is None or credential is None:
                raise ConnectorCredentialError("connector_scope_denied")
            if credential.revoked_at is not None or credential.state == "revoked":
                self._forget_connector(connector.id)
                raise ConnectorCredentialError("connector_revoked")
            if credential.state != "active":
                raise ConnectorCredentialError("connector_unavailable")
            expiry = _utc(credential.expires_at) if credential.expires_at else None
            if expiry is not None and expiry <= self._now():
                self._forget_connector(connector.id)
                raise ConnectorCredentialError("connector_expired")
            try:
                configuration = ConnectorConfiguration.model_validate_json(
                    revision.configuration_json
                )
            except (TypeError, ValueError):
                raise ConnectorCredentialError("connector_unavailable") from None
            if configuration.kind != "aws":
                raise ConnectorCredentialError("connector_scope_denied")
            account, _role_name = _configured_role(configuration)
            if account != configuration.expected_account_id:
                raise AwsBrokerError("aws_configuration_error")
            key = AwsRoleCacheKey(
                project_id=project_id,
                connector_id=connector.id,
                connector_revision=reference.configuration_revision,
                auth_version_id=credential.id,
                role_arn=configuration.basic_role_arn,
                region=configuration.region,
                visibility=connector.visibility,
            )
            source_auth = None
            if include_auth:
                installation_id = settings.connector_installation_id
                if installation_id is None:
                    raise ConnectorCredentialError("credential_store_unavailable")
                context = CredentialContext(
                    installation_id=installation_id,
                    visibility=connector.visibility,
                    project_id=connector.project_id,
                    connector_id=connector.id,
                    credential_version_id=credential.id,
                    schema_version=credential.schema_version,
                    kind=credential.kind,
                )
                try:
                    envelope = CredentialEnvelope(
                        key_id=credential.key_id,
                        nonce=credential.nonce,
                        ciphertext=credential.ciphertext,
                    )
                    payload = self._credential_store_factory().decrypt(context, envelope)
                    source_auth = AwsBootstrapAuth.model_validate_json(
                        canonical_json(payload)
                    )
                except Exception:  # noqa: BLE001 - decrypted source stays private.
                    raise ConnectorCredentialError("credential_store_unavailable") from None
                payload_expiry = (
                    _utc(source_auth.expires_at) if source_auth.expires_at else None
                )
                if payload_expiry != expiry:
                    raise ConnectorCredentialError("connector_unavailable")
                if payload_expiry is not None and payload_expiry <= self._now():
                    raise ConnectorCredentialError("connector_expired")
            return _Binding(configuration, key, source_auth)

    async def _assume_and_verify(
        self,
        *,
        project_id: uuid.UUID,
        reference: ConnectorRef,
        configuration: ConnectorConfiguration,
        source_auth: AwsBootstrapAuth,
        run_id: uuid.UUID,
        control: InvocationControl,
        request_budget: AwsRequestBudget,
    ) -> tuple[AwsAssumedRole, AwsSdkContext]:
        session_name = f"{_ROLE_SESSION_PREFIX}{run_id.hex}"
        source_context = self._new_context(
            auth=source_auth.to_sdk_auth(),
            region=configuration.region,
            control=control,
            request_budget=request_budget,
        )
        try:
            self._read_binding(
                project_id=project_id,
                reference=reference,
                include_auth=False,
            )
            sts = source_context.client("sts")
            response = await source_context.run(
                sts.assume_role,
                RoleArn=configuration.basic_role_arn,
                RoleSessionName=session_name,
                DurationSeconds=_SESSION_DURATION_SECONDS,
            )
        finally:
            await source_context.aclose()

        session = _assumed_session(
            response,
            account_id=configuration.expected_account_id,
            role_name=_configured_role(configuration)[1],
            session_name=session_name,
            now=self._now(),
        )
        role_context = self._new_context(
            auth=session.auth,
            region=configuration.region,
            control=control,
            request_budget=request_budget,
        )
        verified = False
        try:
            self._read_binding(
                project_id=project_id,
                reference=reference,
                include_auth=False,
            )
            identity = await role_context.run(
                role_context.client("sts").get_caller_identity
            )
            _verify_identity(
                identity,
                account_id=configuration.expected_account_id,
                role_name=session.role_name,
                session_name=session_name,
            )
            verified = True
            return session, role_context
        finally:
            if not verified:
                await role_context.aclose()

    def _new_context(
        self,
        *,
        auth: AwsSdkAuth,
        region: str,
        control: InvocationControl,
        request_budget: AwsRequestBudget,
    ) -> AwsSdkContext:
        options = {
            "auth": auth,
            "region": region,
            "control": control,
            "request_budget": request_budget,
        }
        if self._transport_factory is not None:
            options["transport_factory"] = self._transport_factory
        return self._sdk_context_factory(**options)

    async def _wait_for_session(
        self,
        future: concurrent.futures.Future,
        control: InvocationControl,
    ) -> AwsAssumedRole:
        wrapped = asyncio.wrap_future(future)
        while not future.done():
            try:
                control.raise_if_stopped()
            except Exception:  # noqa: BLE001 - a waiter must not expose control details.
                raise AwsBrokerError("aws_cancelled") from None
            timeout = control.request_timeout_seconds(_REQUEST_TIMEOUT_SECONDS)
            if timeout <= 0:
                raise AwsBrokerError("aws_timeout")
            await asyncio.wait((wrapped,), timeout=min(0.05, timeout))
        try:
            return await asyncio.shield(wrapped)
        except AwsSdkError as exc:
            raise AwsBrokerError(exc.code) from None
        except ConnectorCredentialError:
            raise
        except Exception:  # noqa: BLE001 - shared assumption failures stay fixed.
            raise AwsBrokerError("aws_unavailable") from None

    def _validate_before_dispatch(
        self,
        *,
        project_id: uuid.UUID,
        reference: ConnectorRef,
    ) -> None:
        try:
            self._read_binding(
                project_id=project_id,
                reference=reference,
                include_auth=False,
            )
        except AwsBrokerError:
            raise
        except ConnectorCredentialError:
            raise
        except Exception:  # noqa: BLE001 - database errors are fixed at the broker boundary.
            raise ConnectorCredentialError("credential_store_unavailable") from None

    def _forget_connector(self, connector_id: uuid.UUID) -> None:
        with self._guard:
            for key in tuple(self._sessions):
                if key.connector_id == connector_id:
                    self._sessions.pop(key, None)

    def _purge_expired(self, now: datetime) -> None:
        for key, session in tuple(self._sessions.items()):
            if _utc(session.expires_at) <= now:
                self._sessions.pop(key, None)

    def _usable(self, session: AwsAssumedRole | None, now: datetime) -> bool:
        return (
            session is not None
            and _utc(session.expires_at) - now > timedelta(seconds=_RENEW_BEFORE_SECONDS)
        )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise AwsBrokerError("aws_configuration_error")
        return value.astimezone(UTC)


def _configured_role(configuration: ConnectorConfiguration) -> tuple[str, str]:
    arn = configuration.basic_role_arn or ""
    match = _ROLE_ARN.fullmatch(arn)
    if match is None:
        raise AwsBrokerError("aws_configuration_error")
    return match.group("account"), match.group("name")


def _assumed_session(
    response: dict,
    *,
    account_id: str | None,
    role_name: str,
    session_name: str,
    now: datetime,
) -> AwsAssumedRole:
    if not isinstance(response, dict) or account_id is None:
        raise AwsBrokerError("aws_identity_mismatch")
    credentials = response.get("Credentials")
    assumed_user = response.get("AssumedRoleUser")
    if not isinstance(credentials, dict) or not isinstance(assumed_user, dict):
        raise AwsBrokerError("aws_invalid_response")
    auth_values = (
        credentials.get("AccessKeyId"),
        credentials.get("SecretAccessKey"),
        credentials.get("SessionToken"),
    )
    expiration = credentials.get("Expiration")
    expected_arn = (
        f"arn:aws:sts::{account_id}:assumed-role/{role_name}/{session_name}"
    )
    if (
        any(not isinstance(value, str) or not value for value in auth_values)
        or not isinstance(expiration, datetime)
        or not isinstance(assumed_user.get("Arn"), str)
        or assumed_user["Arn"] != expected_arn
    ):
        raise AwsBrokerError("aws_identity_mismatch")
    expiration = _utc(expiration)
    if expiration <= now + timedelta(seconds=_RENEW_BEFORE_SECONDS):
        raise AwsBrokerError("aws_credentials_expired")
    auth = AwsSdkAuth(
        access_key_id=auth_values[0],
        secret_access_key=auth_values[1],
        session_token=auth_values[2],
    )
    return AwsAssumedRole(
        auth=auth,
        expires_at=expiration,
        account_id=account_id,
        role_name=role_name,
    )


def _verify_identity(
    identity: dict,
    *,
    account_id: str | None,
    role_name: str,
    session_name: str,
) -> None:
    if not isinstance(identity, dict) or account_id is None:
        raise AwsBrokerError("aws_identity_mismatch")
    if identity.get("Account") != account_id:
        raise AwsBrokerError("aws_account_mismatch")
    expected_arn = (
        f"arn:aws:sts::{account_id}:assumed-role/{role_name}/{session_name}"
    )
    if identity.get("Arn") != expected_arn:
        raise AwsBrokerError("aws_identity_mismatch")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = ["AwsBrokerError", "AwsRoleAccess", "AwsRoleBroker"]
