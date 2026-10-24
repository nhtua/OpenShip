"""Admission, single-dispatch execution and atomic result publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.openship.artifacts.contracts import ArtifactRef, StagedArtifact
from src.openship.artifacts.filesystem import ArtifactStorageError
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.catalog import (
    legacy_credential_binding,
    resolve_capability,
)
from src.openship.capabilities.contracts import (
    CapabilityRef,
    PublishedToolResult,
    ToolInvocationContext,
    canonical_json,
    decode_execution_config,
)
from src.openship.capabilities.definitions import get_tool_definition
from src.openship.capabilities.policy import (
    ToolPolicyError,
    require_current_tool_policy,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.connectors.aws.broker import AwsRoleBroker
from src.openship.connectors.credentials.broker import (
    ConnectorCredentialError,
    SearchCredentialBroker,
)
from src.openship.execution.aws_transport import AwsRequestBudget, AwsSdkError
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Run
from src.openship.runs.queue import Claim

from .admission import (
    acquire_execution_slot,
    admit_operation,
    renew_execution_slot,
)
from .errors import CapabilityError
from .models import ToolOperation
from .publication import (
    ToolPublicationError,
    mark_tool_dispatching,
    mark_tool_failed,
    mark_tool_running,
    project_published_result,
    publish_local_tool_result,
    publish_tool_result,
)

logger = logging.getLogger(__name__)


def _require_operation_policy(db, operation: ToolOperation):
    try:
        return require_current_tool_policy(
            db,
            frozenset({operation.capability_id}),
            pinned_revision=operation.tool_policy_revision,
            lock=True,
        )
    except ToolPolicyError:
        raise ToolPublicationError("disabled") from None


_PRE_DISPATCH_MESSAGES = {
    "missing_credentials": "The selected search tool is missing its operator credential.",
    "connector_required": "Choose a search Connector before using this service.",
    "connector_ambiguous": "Choose one eligible search Connector explicitly.",
    "connector_scope_denied": "The selected search Connector is not available in this project.",
    "connector_expired": "The selected search Connector credential has expired.",
    "connector_revoked": "The selected search Connector has been revoked.",
    "connector_unavailable": "The selected search Connector is currently unavailable.",
    "credential_store_unavailable": "Search credential storage is unavailable.",
    "authentication_error": "The search service rejected the configured credential.",
    "rate_limited": "The search service is rate limiting this request.",
    "quota_exhausted": "The search service account has exhausted its configured quota.",
    "provider_timeout": "The search service did not respond within the allowed time.",
    "provider_unavailable": "The search service is temporarily unavailable.",
    "aws_unavailable": "The AWS service is temporarily unavailable.",
    "aws_access_denied": "The AWS connector role does not have permission to perform this operation.",
    "aws_trust_denied": "AWS denied the configured role assumption. Check the source key and role trust policy.",
    "aws_credentials_expired": "The AWS source credentials have expired.",
    "aws_credentials_invalid": "The AWS source credentials were rejected.",
    "aws_timeout": "The AWS request exceeded its execution deadline.",
    "aws_cancelled": "The AWS request was cancelled.",
    "aws_destination_denied": "The AWS destination was rejected by network policy.",
    "aws_redirect": "The AWS service returned an unsupported redirect.",
    "aws_account_mismatch": "The AWS role belongs to a different account than the Connector.",
    "aws_identity_mismatch": "The AWS role identity does not match the Connector.",
    "aws_region_mismatch": "The bound S3 bucket is in a different AWS region.",
    "aws_resource_unavailable": "The selected AWS resource is not available in this project.",
    "aws_invalid_metric_response": "AWS returned an invalid storage metrics response.",
    "aws_invalid_metadata_response": "AWS returned invalid S3 versioning metadata.",
    "aws_request_limit_exceeded": "The AWS request limit was exceeded.",
    "aws_output_limit_exceeded": "The AWS storage observation exceeded its size limit.",
    "aws_configuration_error": "The AWS Connector configuration is unavailable.",
    "aws_not_found": "The requested AWS resource was not found or is not available to this role.",
    "aws_throttled": "AWS is throttling this request. Try again shortly.",
    "aws_invalid_parameters": "The AWS service rejected the operation parameters.",
    "aws_operation_not_supported": "This AWS read operation is not supported by OpenShip.",
    "aws_unsupported_protocol": "This AWS operation uses a protocol OpenShip cannot safely handle.",
    "aws_unsupported_output": "The AWS operation returned data OpenShip cannot safely publish.",
    "aws_invalid_response": "AWS returned an invalid response.",
    "aws_invalid_cursor": "The AWS continuation cursor is invalid or has expired. Start a fresh read.",
    "aws_catalog_mismatch": "The AWS read catalog changed. Start a fresh operation.",
    "invalid_response": "The search service returned an invalid response.",
    "output_limit_exceeded": "The search response exceeded the allowed size.",
    "disabled": "The requested tool is disabled for this installation.",
    "changed": "The requested tool definition changed after this request was admitted.",
    "capacity_timeout": "Tool capacity was not available before the execution deadline.",
    "cancelled": "The search was cancelled before it completed.",
    "artifact_storage_unavailable": "Conversation artifact storage is unavailable.",
    "artifact_quota_exceeded": "Conversation artifact storage quota is full.",
    "artifact_publication_failed": "The search result could not be retained safely.",
    "invalid_tool_output": "The maintained tool returned an invalid result.",
    "workspace_unavailable": "The conversation workspace is unavailable.",
    "artifact_not_found": "The requested file is not available in this conversation.",
    "invalid_cursor": "The pagination cursor is invalid.",
    "invalid_offset": "The requested byte offset is invalid.",
    "invalid_utf8_boundary": "The requested byte offset is not on a UTF-8 boundary.",
    "artifact_unavailable": "The saved file could not be verified in storage.",
    "invalid_page_size": "The bucket inventory page size is invalid.",
    "inventory_unavailable": "The saved bucket inventory is unavailable.",
    "execution_interrupted": "The tool execution was interrupted and will not be repeated automatically.",
}


class _CombinedControl:
    """Preserve run supervision while adding the execution-slot lease signal."""

    def __init__(self, parent, slot_lost: asyncio.Event):
        self._parent = parent
        self._slot_lost = slot_lost

    def request_timeout_seconds(self, limit: float) -> float:
        return self._parent.request_timeout_seconds(limit)

    def raise_if_stopped(self) -> None:
        self._parent.raise_if_stopped()
        if self._slot_lost.is_set():
            raise ProviderError("cancelled")


class ToolGateway:
    """Execute one exact maintained tool under a live run fence."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        capacity_poll_interval: float = 0.1,
        sleep=asyncio.sleep,
    ):
        if capacity_poll_interval <= 0:
            raise ValueError("capacity polling interval must be positive")
        self._session_factory = session_factory
        self._capacity_poll_interval = capacity_poll_interval
        self._sleep = sleep

    async def execute_tool(
        self,
        *,
        claim: Claim,
        context: ToolInvocationContext,
        logical_call_id: str,
        ref: CapabilityRef,
        arguments: dict,
        inputs: tuple[ArtifactRef, ...] = (),
        control: InvocationControl,
    ) -> PublishedToolResult:
        operation_id, status = self._admit(
            claim, context, logical_call_id, ref, arguments, inputs
        )
        if status != "admitted":
            with self._session_factory() as db:
                operation = (
                    db.query(ToolOperation)
                    .filter(ToolOperation.id == operation_id)
                    .first()
                )
                return project_published_result(db, operation)

        try:
            pinned_config, manifest, definition = self._resolve_dispatch(
                claim, context, operation_id, ref
            )
        except ToolPublicationError as refused:
            return self._finish_before_dispatch(claim, operation_id, refused.code)
        except HTTPException:
            raise

        store = context.artifact_store
        needs_workspace = "conversation_workspace" in manifest.enablement_requirements
        needs_store = needs_workspace or bool(manifest.outputs)
        store_error_code = (
            "workspace_unavailable"
            if needs_workspace
            else "artifact_storage_unavailable"
        )
        if needs_store and store is None:
            return self._finish_before_dispatch(
                claim,
                operation_id,
                store_error_code,
            )
        if needs_store:
            try:
                store.verify()
            except ArtifactStorageError:
                return self._finish_before_dispatch(
                    claim,
                    operation_id,
                    store_error_code,
                )

        binding = getattr(manifest, "credential_binding_ref", None)
        if binding is None:
            binding = legacy_credential_binding(manifest.id)
        managed_reference = None
        managed_kind = None
        broker = None
        aws_broker = None
        credential = None
        if manifest.schema_version in (2, 3) and getattr(manifest, "connector_kinds", ()):
            expected_kinds = set(manifest.connector_kinds)
            if "aws" in expected_kinds:
                managed_reference = self._aws_reference(
                    manifest.id,
                    arguments,
                    pinned_config,
                    context.authorization.project_id,
                )
                references = (
                    [managed_reference] if managed_reference is not None else []
                )
            else:
                references = [
                    item
                    for item in getattr(pinned_config, "connector_refs", ())
                    if item.kind in expected_kinds
                ]
            if len(references) != 1:
                return self._finish_before_dispatch(
                    claim, operation_id, "connector_required"
                )
            managed_reference = references[0]
            managed_kind = managed_reference.kind
            try:
                if managed_kind == "aws":
                    aws_broker = AwsRoleBroker(self._session_factory)
                    aws_configuration = aws_broker.validate(
                        project_id=context.authorization.project_id,
                        reference=managed_reference,
                    )
                    if not self._validate_aws_target(
                        manifest.id,
                        arguments,
                        pinned_config,
                        context.authorization.project_id,
                        managed_reference,
                        aws_configuration,
                    ):
                        return self._finish_before_dispatch(
                            claim, operation_id, "aws_resource_unavailable"
                        )
                else:
                    broker = SearchCredentialBroker(self._session_factory)
                    broker.validate(
                        project_id=context.authorization.project_id,
                        kind=managed_kind,
                        reference=managed_reference,
                    )
            except ConnectorCredentialError as refused:
                return self._finish_before_dispatch(claim, operation_id, refused.code)
            except AwsSdkError as refused:
                return self._finish_before_dispatch(
                    claim,
                    operation_id,
                    refused.code
                    if refused.code in _PRE_DISPATCH_MESSAGES
                    else "credential_store_unavailable",
                )
            except Exception:  # noqa: BLE001 - database/key details stay private.
                return self._finish_before_dispatch(
                    claim, operation_id, "credential_store_unavailable"
                )
        elif binding:
            try:
                credential = context.credential_resolver(binding)
            except Exception:  # noqa: BLE001 - resolver failures may contain secret details.
                credential = None
            if not _valid_credential(credential):
                return self._finish_before_dispatch(
                    claim, operation_id, "missing_credentials"
                )
        if (
            binding
            and managed_reference is None
            and binding not in getattr(pinned_config, "credential_binding_refs", ())
        ):
            return self._finish_before_dispatch(claim, operation_id, "changed")

        operation_id, attempt_id, artifact_id, slot_id = await self._acquire_and_mark(
            claim=claim,
            operation_id=operation_id,
            control=control,
            max_output_bytes=min(
                pinned_config.limits.max_observation_bytes,
                (
                    settings.artifact_cloud_observation_reservation_bytes
                    if definition.artifact_kind == "cloud_observation"
                    else settings.artifact_search_reservation_bytes
                ),
                64 * 1024,
            ),
            reserve_output=bool(manifest.outputs),
            artifact_kind=definition.artifact_kind or "search_result",
            artifact_name=definition.artifact_name,
        )
        if isinstance(attempt_id, PublishedToolResult):
            return attempt_id

        try:
            db = self._session_factory()
            try:
                mark_tool_running(db, claim, attempt_id)
                db.commit()
            finally:
                db.close()
        except ToolPublicationError as refused:
            return self._interrupted(operation_id, refused.code)
        except Exception:  # noqa: BLE001 - failures at the dispatch boundary are safe and closed.
            return self._finish_after_dispatch(
                claim, operation_id, attempt_id, "execution_interrupted"
            )

        aws_request_budget = AwsRequestBudget() if aws_broker is not None else None
        runtime_accesses = []
        runtime_context = ToolInvocationContext(
            authorization=context.authorization,
            execution=pinned_config,
            credential_resolver=lambda requested: (
                broker.acquire(
                    project_id=context.authorization.project_id,
                    kind=managed_kind,
                    reference=managed_reference,
                )
                if managed_reference is not None and requested == binding
                else credential
                if requested == binding
                else None
            ),
            artifact_store=store,
            session_factory=self._session_factory,
            operation_id=operation_id,
            aws_access_factory=(
                self._aws_access_factory(
                    broker=aws_broker,
                    capability_id=manifest.id,
                    allowed_operations=tuple(manifest.operations),
                    project_id=context.authorization.project_id,
                    run_id=claim.run_id,
                    request_budget=aws_request_budget,
                    control_holder=lambda: dispatch_control,
                    references=tuple(getattr(pinned_config, "connector_refs", ())),
                    accesses=runtime_accesses,
                )
                if aws_broker is not None
                else None
            ),
        )
        runtime_context._aws_accesses = runtime_accesses
        slot_lost = asyncio.Event()
        dispatch_control = _CombinedControl(control, slot_lost)
        renewal = asyncio.create_task(
            self._renew_slot(claim, slot_id, slot_lost, control)
        )
        if manifest.effect == "read_only_local":
            local_result = None
            local_failure = None
            try:
                validated_arguments = definition.validate_arguments(arguments)
                local_result = await definition.handler(
                    validated_arguments, runtime_context, dispatch_control
                )
                dispatch_control.raise_if_stopped()
                local_result = definition.validate_handler_result(
                    self._operation_for_validation(operation_id), local_result
                )
                local_data = local_result.model_dump(mode="json")
                if len(canonical_json(local_data)) > int(
                    manifest.limits.get("max_model_result_bytes", 16 * 1024)
                ):
                    raise ToolPublicationError("output_limit_exceeded")
            except ProviderError as failure:
                local_failure = (
                    "cancelled"
                    if failure.code == "cancelled"
                    else "execution_interrupted"
                )
            except ToolPublicationError as failure:
                local_failure = failure.code
            except Exception as failure:  # noqa: BLE001 - local tool details stay private.
                local_failure = definition.map_error(failure)
            finally:
                renewal.cancel()
                try:
                    await renewal
                except asyncio.CancelledError:
                    pass
                await runtime_context.close_runtime_accesses()
            if local_failure is not None:
                return await self._fail_active(
                    claim, operation_id, attempt_id, local_failure
                )
            return self._publish_local(
                claim=claim,
                operation_id=operation_id,
                attempt_id=attempt_id,
                definition=definition,
                result=local_result,
            )

        dispatch_failure = None
        observation = None
        try:
            validated_arguments = definition.validate_arguments(arguments)
            observation = await definition.handler(
                validated_arguments, runtime_context, dispatch_control
            )
            dispatch_control.raise_if_stopped()
            observation = definition.validate_handler_result(
                self._operation_for_validation(operation_id), observation
            )
        except ProviderError as failure:
            dispatch_failure = (
                "cancelled" if failure.code == "cancelled" else "provider_unavailable"
            )
        except ToolPublicationError as failure:
            dispatch_failure = failure.code
        except Exception as failure:  # noqa: BLE001 - adapter details stay private.
            dispatch_failure = definition.map_error(failure)
        finally:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass
            await runtime_context.close_runtime_accesses()
        if dispatch_failure is not None:
            return await self._fail_active(
                claim, operation_id, attempt_id, dispatch_failure
            )

        if not manifest.outputs:
            return self._publish_local(
                claim=claim,
                operation_id=operation_id,
                attempt_id=attempt_id,
                definition=definition,
                result=observation,
            )

        try:
            evidence = definition.project_evidence(observation)
            if hasattr(evidence, "model_dump"):
                evidence = evidence.model_dump(mode="json")
            snapshot = canonical_json(evidence)
            if len(snapshot) > min(
                pinned_config.limits.max_observation_bytes,
                (
                    settings.artifact_cloud_observation_reservation_bytes
                    if definition.artifact_kind == "cloud_observation"
                    else settings.artifact_search_reservation_bytes
                ),
                64 * 1024,
            ):
                raise ToolPublicationError("output_limit_exceeded")
            digest = hashlib.sha256(snapshot).hexdigest()
            expected = ArtifactRef(
                id=artifact_id,
                sha256=digest,
                size_bytes=len(snapshot),
                media_type="application/json",
            )
            staged = store.stage(
                artifact_id,
                (snapshot,),
                expected,
                project_id=context.authorization.project_id,
                conversation_id=context.authorization.conversation_id,
            )
            artifact = self._artifact(artifact_id)
            store.publish_file(staged, artifact.relative_storage_key)
        except ToolPublicationError as failure:
            self._remove_staging(store, artifact_id, context)
            return await self._fail_active(
                claim, operation_id, attempt_id, failure.code
            )
        except ArtifactStorageError:
            self._remove_staging(store, artifact_id, context)
            return await self._fail_active(
                claim, operation_id, attempt_id, "artifact_publication_failed"
            )
        except Exception:  # noqa: BLE001 - filesystem internals do not cross the gateway.
            self._remove_staging(store, artifact_id, context)
            return await self._fail_active(
                claim, operation_id, attempt_id, "artifact_publication_failed"
            )

        return self._publish(
            claim=claim,
            operation_id=operation_id,
            attempt_id=attempt_id,
            staged=staged,
            observation=observation,
            store=store,
            context=context,
            definition=definition,
        )

    def _admit(self, claim, context, logical_call_id, ref, arguments, inputs):
        db = self._session_factory()
        try:
            operation = admit_operation(
                db,
                claim,
                context.authorization,
                logical_call_id,
                ref,
                arguments,
                inputs,
            )
            operation_id = operation.id
            status = operation.status
            db.commit()
            return operation_id, status
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _resolve_dispatch(self, claim, context, operation_id, ref):
        with self._session_factory() as db:
            operation = (
                db.query(ToolOperation).filter(ToolOperation.id == operation_id).first()
            )
            run = db.query(Run).filter(Run.id == claim.run_id).first()
            if operation is None or run is None:
                raise ToolPublicationError("execution_interrupted")
            try:
                pinned = decode_execution_config(run.execution_configuration or "{}")
            except (ValidationError, TypeError, ValueError) as exc:
                raise ToolPublicationError("changed") from exc
            is_manual_tool = run.kind == "tool" and run.graph_version in (1, 2, 3)
            is_chat_tool = run.kind == "chat" and run.graph_version in (2, 3, 4)
            if (
                context.execution != pinned
                or not (is_manual_tool or is_chat_tool)
                or pinned.graph_kind != run.kind
                or context.execution.graph_version != run.graph_version
                or ref not in pinned.allowed_capabilities
                or operation.capability_id != ref.id
                or operation.capability_version != ref.version
                or operation.manifest_digest != ref.manifest_digest
            ):
                raise ToolPublicationError("changed")
            try:
                policy = _require_operation_policy(db, operation)
                manifest = resolve_capability(ref, policy.effective_ids)
            except CapabilityError as exc:
                code = exc.code
                if code not in ("disabled", "changed"):
                    code = "changed"
                raise ToolPublicationError(code) from None
            try:
                definition = get_tool_definition(manifest)
            except LookupError:
                raise ToolPublicationError("changed")
            if manifest.id in {"aws_read", "aws_read_catalog"}:
                from src.openship.connectors.aws.read_catalog import (
                    load_aws_read_catalog,
                )

                if (
                    not hasattr(pinned, "aws_read_catalog_digest")
                    or pinned.aws_read_catalog_digest != load_aws_read_catalog().digest
                    or manifest.aws_read_catalog_digest != pinned.aws_read_catalog_digest
                ):
                    raise ToolPublicationError("changed")
            return pinned, manifest, definition

    async def _acquire_and_mark(
        self,
        *,
        claim,
        operation_id,
        control,
        max_output_bytes,
        reserve_output=True,
        artifact_kind="search_result",
        artifact_name=None,
    ):
        while True:
            try:
                control.raise_if_stopped()
            except ProviderError:
                return (
                    operation_id,
                    await self._finish_before_dispatch_async(
                        claim, operation_id, "cancelled"
                    ),
                    None,
                    None,
                )
            remaining = control.request_timeout_seconds(self._capacity_poll_interval)
            if remaining <= 0:
                return (
                    operation_id,
                    await self._finish_before_dispatch_async(
                        claim, operation_id, "capacity_timeout"
                    ),
                    None,
                    None,
                )

            db = self._session_factory()
            try:
                locator = (
                    db.query(ToolOperation)
                    .filter(ToolOperation.id == operation_id)
                    .first()
                )
                if locator is None:
                    raise ToolPublicationError("execution_interrupted")
                conversation = (
                    db.query(Conversation)
                    .filter(Conversation.id == locator.conversation_id)
                    .with_for_update()
                    .first()
                )
                if conversation is None:
                    raise ToolPublicationError("execution_interrupted")
                from .admission import require_live_claim

                require_live_claim(db, claim, lock=True)
                operation = (
                    db.query(ToolOperation)
                    .filter(ToolOperation.id == operation_id)
                    .with_for_update()
                    .first()
                )
                if operation is None or operation.status != "admitted":
                    db.rollback()
                    with self._session_factory() as read_db:
                        current = (
                            read_db.query(ToolOperation)
                            .filter(ToolOperation.id == operation_id)
                            .first()
                        )
                        return (
                            operation_id,
                            project_published_result(read_db, current),
                            None,
                            None,
                        )
                _require_operation_policy(db, operation)
                slot = acquire_execution_slot(db, claim, operation_id)
                if slot is None:
                    db.rollback()
                else:
                    operation, attempt, artifact = mark_tool_dispatching(
                        db,
                        claim,
                        operation_id,
                        slot.id,
                        max_output_bytes,
                        reserve_output=reserve_output,
                        artifact_kind=artifact_kind,
                        artifact_name=artifact_name,
                    )
                    attempt_id = attempt.id
                    artifact_id = artifact.id if artifact is not None else None
                    slot_id = slot.id
                    db.commit()
                    return operation_id, attempt_id, artifact_id, slot_id
            except HTTPException as refused:
                db.rollback()
                if _http_error_code(refused) == "lease_lost":
                    return (
                        operation_id,
                        self._interrupted(operation_id, "lease_lost"),
                        None,
                        None,
                    )
                code = _http_error_code(refused)
                if code == "artifact_quota_exceeded":
                    result = await self._finish_before_dispatch_async(
                        claim, operation_id, "artifact_quota_exceeded"
                    )
                    return operation_id, result, None, None
                raise
            except ToolPublicationError as refused:
                db.rollback()
                if refused.code == "disabled":
                    result = await self._finish_before_dispatch_async(
                        claim, operation_id, "disabled"
                    )
                    return operation_id, result, None, None
                return (
                    operation_id,
                    self._interrupted(operation_id, refused.code),
                    None,
                    None,
                )
            finally:
                db.close()
            await self._sleep(remaining)

    async def _finish_before_dispatch_async(self, claim, operation_id, code):
        return self._finish_before_dispatch(claim, operation_id, code)

    def _finish_before_dispatch(self, claim, operation_id, code):
        return self._finish_failure(claim, operation_id, None, code)

    async def _fail_active(self, claim, operation_id, attempt_id, code):
        return self._finish_failure(claim, operation_id, attempt_id, code)

    def _finish_after_dispatch(self, claim, operation_id, attempt_id, code):
        return self._finish_failure(claim, operation_id, attempt_id, code)

    def _finish_failure(self, claim, operation_id, attempt_id, code):
        if code not in _PRE_DISPATCH_MESSAGES:
            code = "provider_unavailable"
        db = self._session_factory()
        try:
            result = mark_tool_failed(
                db,
                claim,
                operation_id,
                code,
                attempt_id=attempt_id,
                status="cancelled" if code == "cancelled" else "failed",
            )
            db.commit()
            return result
        except ToolPublicationError as refused:
            db.rollback()
            return self._interrupted(operation_id, refused.code)
        except Exception:  # noqa: BLE001 - a lost DB acknowledgement must never cause redispatch.
            db.rollback()
            return self._interrupted(operation_id, "execution_interrupted")
        finally:
            db.close()

    def _interrupted(self, operation_id, code):
        if code not in _PRE_DISPATCH_MESSAGES:
            code = "execution_interrupted"
        from src.openship.capabilities.contracts import ToolFailure

        return PublishedToolResult(
            operation_id=operation_id,
            status="interrupted",
            error=ToolFailure(code=code, message=_PRE_DISPATCH_MESSAGES[code]),
        )

    async def _renew_slot(self, claim, slot_id, slot_lost, parent_control):
        lease = max(1.0, float(getattr(settings, "worker_lease_seconds", 30)))
        interval = min(
            5.0,
            max(0.1, float(getattr(settings, "worker_heartbeat_interval", 10)) / 2),
            lease / 4,
        )
        while True:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            try:
                parent_control.raise_if_stopped()
            except ProviderError:
                return
            db = self._session_factory()
            try:
                if not renew_execution_slot(db, claim, slot_id):
                    db.rollback()
                    slot_lost.set()
                    return
                db.commit()
            except Exception:  # noqa: BLE001 - lease uncertainty stops local work.
                db.rollback()
                slot_lost.set()
                return
            finally:
                db.close()

    def _operation_for_validation(self, operation_id):
        with self._session_factory() as db:
            operation = (
                db.query(ToolOperation).filter(ToolOperation.id == operation_id).first()
            )
            if operation is None:
                raise ToolPublicationError("execution_interrupted")
            return operation

    def _aws_reference(self, capability_id, arguments, execution, project_id):
        references = tuple(getattr(execution, "connector_refs", ()))
        if capability_id == "aws_check_connection":
            try:
                connector_id = uuid.UUID(str(arguments["connector_id"]))
            except (KeyError, TypeError, ValueError):
                return None
            matches = [
                item
                for item in references
                if item.kind == "aws" and item.id == connector_id
            ]
            return matches[0] if len(matches) == 1 else None
        if capability_id in {"aws_read", "aws_read_catalog"}:
            try:
                connector_id = uuid.UUID(str(arguments["connector_id"]))
            except (KeyError, TypeError, ValueError):
                connector_id = None
            matches = [
                item
                for item in references
                if item.kind == "aws"
                and (connector_id is None or item.id == connector_id)
            ]
            return matches[0] if len(matches) == 1 else None
        if capability_id in {"aws_s3_bucket_storage", "aws_s3_bucket_metadata"}:
            try:
                binding_id = uuid.UUID(str(arguments["resource_binding_id"]))
            except (KeyError, TypeError, ValueError):
                return None
            binding_refs = [
                item
                for item in getattr(execution, "resource_binding_refs", ())
                if item.id == binding_id
            ]
            if len(binding_refs) != 1:
                return None
            target = binding_refs[0]
            matches = [
                item
                for item in references
                if item.kind == "aws"
                and item.id == target.connector_id
                and item.configuration_revision == target.connector_revision
            ]
            return matches[0] if len(matches) == 1 else None
        return None

    def _validate_aws_target(
        self,
        capability_id,
        arguments,
        execution,
        project_id,
        connector_ref,
        configuration,
    ):
        if capability_id in {"aws_read", "aws_read_catalog"}:
            references = [
                item
                for item in getattr(execution, "connector_refs", ())
                if item.kind == "aws"
            ]
            return (
                len(references) == 1
                and references[0] == connector_ref
                and configuration.kind == "aws"
            )
        binding_id = None
        if capability_id in {"aws_s3_bucket_storage", "aws_s3_bucket_metadata"}:
            try:
                binding_id = uuid.UUID(str(arguments["resource_binding_id"]))
            except (KeyError, TypeError, ValueError):
                return False
        elif capability_id == "aws_check_connection" and arguments.get(
            "resource_binding_id"
        ):
            try:
                binding_id = uuid.UUID(str(arguments["resource_binding_id"]))
            except (TypeError, ValueError):
                return False
        if binding_id is None:
            return capability_id == "aws_check_connection"
        references = [
            item
            for item in getattr(execution, "resource_binding_refs", ())
            if item.id == binding_id
        ]
        if len(references) != 1:
            return False
        target = references[0]
        with self._session_factory() as db:
            from src.openship.resources.models import ResourceBinding

            binding = (
                db.query(ResourceBinding)
                .filter(
                    ResourceBinding.id == binding_id,
                    ResourceBinding.project_id == project_id,
                    ResourceBinding.revision == target.revision,
                    ResourceBinding.connector_id == target.connector_id,
                    ResourceBinding.connector_revision == target.connector_revision,
                    ResourceBinding.state == "active",
                )
                .first()
            )
            if binding is None:
                return False
            return (
                binding.connector_id == connector_ref.id
                and binding.connector_revision == connector_ref.configuration_revision
                and binding.account_id == configuration.expected_account_id
                and binding.expected_owner == configuration.expected_account_id
                and binding.region == configuration.region
            )

    @staticmethod
    def _aws_access_factory(
        *,
        broker,
        capability_id,
        allowed_operations,
        project_id,
        run_id,
        request_budget,
        control_holder,
        references,
        accesses,
    ):
        async def acquire(reference):
            if reference not in references or reference.kind != "aws":
                raise ConnectorCredentialError("connector_scope_denied")
            access = await broker.acquire(
                project_id=project_id,
                reference=reference,
                run_id=run_id,
                control=control_holder(),
                request_budget=request_budget,
                capability_id=capability_id,
                allowed_operations=allowed_operations,
            )
            accesses.append(access)
            return access

        return acquire

    def _artifact(self, artifact_id):
        with self._session_factory() as db:
            artifact = db.query(Artifact).filter(Artifact.id == artifact_id).first()
            if artifact is None:
                raise ToolPublicationError("artifact_publication_failed")
            return artifact

    def _remove_staging(self, store, artifact_id, context):
        try:
            store.remove_staging(
                artifact_id,
                project_id=context.authorization.project_id,
                conversation_id=context.authorization.conversation_id,
            )
        except ArtifactStorageError:
            pass

    def _publish(
        self,
        *,
        claim,
        operation_id,
        attempt_id,
        staged,
        observation,
        store,
        context,
        definition,
    ):
        db = self._session_factory()
        try:
            operation = (
                db.query(ToolOperation).filter(ToolOperation.id == operation_id).first()
            )
            if operation is None:
                raise ToolPublicationError("execution_interrupted")
            policy = _require_operation_policy(db, operation)
            pinned_ref = next(
                (
                    item
                    for item in context.execution.allowed_capabilities
                    if item.id == operation.capability_id
                    and item.version == operation.capability_version
                    and item.manifest_digest == operation.manifest_digest
                ),
                None,
            )
            if pinned_ref is None:
                raise ToolPublicationError("changed")
            try:
                manifest = resolve_capability(pinned_ref, policy.effective_ids)
            except CapabilityError as refused:
                code = (
                    refused.code
                    if refused.code in ("disabled", "changed")
                    else "changed"
                )
                raise ToolPublicationError(code) from None
            if manifest.schema_version in (2, 3) and manifest.connector_kinds:
                if "aws" in manifest.connector_kinds:
                    reference = self._aws_reference(
                        operation.capability_id,
                        json.loads(operation.arguments_json),
                        context.execution,
                        context.authorization.project_id,
                    )
                    if reference is None:
                        raise ToolPublicationError("connector_required")
                    AwsRoleBroker(self._session_factory).validate_in_session(
                        db,
                        project_id=context.authorization.project_id,
                        reference=reference,
                        lock=True,
                    )
                else:
                    references = [
                        item
                        for item in getattr(context.execution, "connector_refs", ())
                        if item.kind in manifest.connector_kinds
                    ]
                    if len(references) != 1:
                        raise ToolPublicationError("connector_required")
                    SearchCredentialBroker(self._session_factory).validate_in_session(
                        db,
                        project_id=context.authorization.project_id,
                        kind=references[0].kind,
                        reference=references[0],
                        lock=True,
                    )
            result = publish_tool_result(
                db,
                claim,
                attempt_id,
                observation,
                (staged,),
                definition=definition,
            )
            try:
                db.commit()
            except Exception:  # noqa: BLE001 - commit acknowledgement may be ambiguous.
                db.rollback()
                return self._read_after_uncertain_commit(operation_id)
            return result
        except ToolPublicationError as failure:
            db.rollback()
            self._cleanup_uncommitted(staged, store, context)
            if failure.code == "lease_lost":
                return self._interrupted(operation_id, failure.code)
            return self._finish_failure(claim, operation_id, attempt_id, failure.code)
        except ConnectorCredentialError as failure:
            db.rollback()
            self._cleanup_uncommitted(staged, store, context)
            return self._finish_failure(claim, operation_id, attempt_id, failure.code)
        except HTTPException:
            db.rollback()
            self._cleanup_uncommitted(staged, store, context)
            return self._interrupted(operation_id, "execution_interrupted")
        except Exception:  # noqa: BLE001 - preserve bytes until commit state is known.
            db.rollback()
            return self._read_after_uncertain_commit(operation_id)
        finally:
            db.close()

    def _publish_local(self, *, claim, operation_id, attempt_id, result, definition):
        db = self._session_factory()
        try:
            operation = (
                db.query(ToolOperation).filter(ToolOperation.id == operation_id).first()
            )
            if operation is None:
                raise ToolPublicationError("execution_interrupted")
            _require_operation_policy(db, operation)
            published = publish_local_tool_result(
                db, claim, attempt_id, result, definition
            )
            try:
                db.commit()
            except Exception:  # noqa: BLE001 - resolve the durable state before returning.
                db.rollback()
                return self._read_after_uncertain_commit(operation_id)
            return published
        except ToolPublicationError as failure:
            db.rollback()
            if failure.code == "lease_lost":
                return self._interrupted(operation_id, failure.code)
            return self._finish_failure(claim, operation_id, attempt_id, failure.code)
        except Exception:  # noqa: BLE001 - no local implementation detail is public.
            db.rollback()
            return self._read_after_uncertain_commit(operation_id)
        finally:
            db.close()

    def _read_after_uncertain_commit(self, operation_id):
        try:
            with self._session_factory() as db:
                operation = (
                    db.query(ToolOperation)
                    .filter(ToolOperation.id == operation_id)
                    .first()
                )
                if operation is not None and operation.status in (
                    "succeeded",
                    "failed",
                    "cancelled",
                    "interrupted",
                ):
                    return project_published_result(db, operation)
        except Exception:  # noqa: BLE001 - database outcome remains unknown.
            logger.warning("Tool publication outcome remains unknown")
        return self._interrupted(operation_id, "execution_interrupted")

    def _cleanup_uncommitted(self, staged: StagedArtifact, store, context) -> None:
        try:
            with self._session_factory() as db:
                artifact = (
                    db.query(Artifact).filter(Artifact.id == staged.artifact_id).first()
                )
                if artifact is None or artifact.status != "staging":
                    return
                relative_key = artifact.relative_storage_key
            store.remove_staging(
                staged.artifact_id,
                project_id=context.authorization.project_id,
                conversation_id=context.authorization.conversation_id,
            )
            store.remove_unpublished(
                relative_key,
                expected_size=staged.observed.size_bytes,
                expected_sha256=staged.observed.sha256,
            )
        except Exception:  # noqa: BLE001 - leave unknown bytes for explicit reconciliation.
            return


def _valid_credential(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _http_error_code(error: HTTPException) -> str:
    detail = error.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        code = detail["error"].get("code")
        return code if isinstance(code, str) else ""
    return ""
