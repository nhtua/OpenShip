"""Admission, single-dispatch execution and atomic result publication."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.openship.artifacts.contracts import ArtifactRef, StagedArtifact
from src.openship.artifacts.filesystem import ArtifactStorageError
from src.openship.artifacts.models import Artifact
from src.openship.capabilities.catalog import resolve_capability
from src.openship.capabilities.contracts import (
    CapabilityRef,
    ExecutionConfig,
    PublishedToolResult,
    ToolInvocationContext,
    canonical_json,
)
from src.openship.chat.models import Conversation
from src.openship.config import settings
from src.openship.providers.control import InvocationControl
from src.openship.providers.errors import ProviderError
from src.openship.runs.models import Run
from src.openship.runs.queue import Claim
from src.openship.tools.search.brave import search_brave
from src.openship.tools.search.contracts import SearchArguments
from src.openship.tools.search.http import SearchToolError
from src.openship.tools.search.tavily import search_tavily

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
    publish_tool_result,
    validate_search_observation,
)

MAINTAINED_TOOL_HANDLERS = {
    "brave_web_search": search_brave,
    "tavily_web_search": search_tavily,
}
logger = logging.getLogger(__name__)

_PRE_DISPATCH_MESSAGES = {
    "missing_credentials": "The selected search tool is missing its operator credential.",
    "authentication_error": "The search service rejected the configured credential.",
    "rate_limited": "The search service is rate limiting this request.",
    "quota_exhausted": "The search service account has exhausted its configured quota.",
    "provider_timeout": "The search service did not respond within the allowed time.",
    "provider_unavailable": "The search service is temporarily unavailable.",
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
                operation = db.query(ToolOperation).filter(
                    ToolOperation.id == operation_id
                ).first()
                return project_published_result(db, operation)

        try:
            pinned_config, manifest, handler = self._resolve_dispatch(
                claim, context, operation_id, ref
            )
        except ToolPublicationError as refused:
            return self._finish_before_dispatch(
                claim, operation_id, refused.code
            )
        except HTTPException:
            raise

        store = context.artifact_store
        if store is None:
            return self._finish_before_dispatch(
                claim, operation_id, "artifact_storage_unavailable"
            )
        try:
            store.verify()
        except ArtifactStorageError:
            return self._finish_before_dispatch(
                claim, operation_id, "artifact_storage_unavailable"
            )

        binding = manifest.credential_binding_ref
        try:
            credential = context.credential_resolver(binding) if binding else None
        except Exception:  # noqa: BLE001 - resolver failures may contain secret details.
            credential = None
        if not _valid_credential(credential):
            return self._finish_before_dispatch(
                claim, operation_id, "missing_credentials"
            )
        if binding not in pinned_config.credential_binding_refs:
            return self._finish_before_dispatch(
                claim, operation_id, "changed"
            )

        operation_id, attempt_id, artifact_id, slot_id = await self._acquire_and_mark(
            claim=claim,
            operation_id=operation_id,
            control=control,
            max_output_bytes=min(
                pinned_config.limits.max_observation_bytes,
                settings.artifact_search_reservation_bytes,
                64 * 1024,
            ),
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

        runtime_context = ToolInvocationContext(
            authorization=context.authorization,
            execution=pinned_config,
            credential_resolver=lambda requested: credential if requested == binding else None,
            artifact_store=store,
        )
        slot_lost = asyncio.Event()
        dispatch_control = _CombinedControl(control, slot_lost)
        renewal = asyncio.create_task(
            self._renew_slot(claim, slot_id, slot_lost, control)
        )
        dispatch_failure = None
        observation = None
        try:
            search_arguments = SearchArguments.model_validate(arguments)
            observation = await handler(search_arguments, runtime_context, dispatch_control)
            dispatch_control.raise_if_stopped()
            observation = validate_search_observation(
                self._operation_for_validation(operation_id), observation
            )
        except SearchToolError as failure:
            dispatch_failure = failure.code
        except ProviderError as failure:
            dispatch_failure = (
                "cancelled" if failure.code == "cancelled" else "provider_unavailable"
            )
        except ToolPublicationError as failure:
            dispatch_failure = failure.code
        except Exception:  # noqa: BLE001 - maintained adapter failures must not expose internals.
            dispatch_failure = "provider_unavailable"
        finally:
            renewal.cancel()
            try:
                await renewal
            except asyncio.CancelledError:
                pass
        if dispatch_failure is not None:
            return await self._fail_active(
                claim, operation_id, attempt_id, dispatch_failure
            )

        try:
            snapshot = canonical_json(observation.model_dump(mode="json"))
            if len(snapshot) > min(
                pinned_config.limits.max_observation_bytes,
                settings.artifact_search_reservation_bytes,
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
            operation = db.query(ToolOperation).filter(
                ToolOperation.id == operation_id
            ).first()
            run = db.query(Run).filter(Run.id == claim.run_id).first()
            if operation is None or run is None:
                raise ToolPublicationError("execution_interrupted")
            try:
                pinned = ExecutionConfig.model_validate_json(
                    run.execution_configuration or "{}"
                )
            except ValidationError as exc:
                raise ToolPublicationError("changed") from exc
            is_manual_tool = run.kind == "tool" and run.graph_version == 1
            is_chat_tool = run.kind == "chat" and run.graph_version == 2
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
                manifest = resolve_capability(ref, settings.enabled_tool_ids)
            except CapabilityError as exc:
                code = exc.code
                if code not in ("disabled", "changed"):
                    code = "changed"
                raise ToolPublicationError(code) from None
            handler = MAINTAINED_TOOL_HANDLERS.get(manifest.id)
            if handler is None:
                raise ToolPublicationError("changed")
            return pinned, manifest, handler

    async def _acquire_and_mark(
        self, *, claim, operation_id, control, max_output_bytes
    ):
        while True:
            try:
                control.raise_if_stopped()
            except ProviderError:
                return operation_id, await self._finish_before_dispatch_async(
                    claim, operation_id, "cancelled"
                ), None, None
            remaining = control.request_timeout_seconds(self._capacity_poll_interval)
            if remaining <= 0:
                return operation_id, await self._finish_before_dispatch_async(
                    claim, operation_id, "capacity_timeout"
                ), None, None

            db = self._session_factory()
            try:
                locator = db.query(ToolOperation).filter(
                    ToolOperation.id == operation_id
                ).first()
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
                operation = db.query(ToolOperation).filter(
                    ToolOperation.id == operation_id
                ).with_for_update().first()
                if operation is None or operation.status != "admitted":
                    db.rollback()
                    with self._session_factory() as read_db:
                        current = read_db.query(ToolOperation).filter(
                            ToolOperation.id == operation_id
                        ).first()
                        return operation_id, project_published_result(read_db, current), None, None
                slot = acquire_execution_slot(db, claim, operation_id)
                if slot is None:
                    db.rollback()
                else:
                    operation, attempt, artifact = mark_tool_dispatching(
                        db, claim, operation_id, slot.id, max_output_bytes
                    )
                    attempt_id = attempt.id
                    artifact_id = artifact.id
                    slot_id = slot.id
                    db.commit()
                    return operation_id, attempt_id, artifact_id, slot_id
            except HTTPException as refused:
                db.rollback()
                if _http_error_code(refused) == "lease_lost":
                    return operation_id, self._interrupted(operation_id, "lease_lost"), None, None
                code = _http_error_code(refused)
                if code == "artifact_quota_exceeded":
                    result = await self._finish_before_dispatch_async(
                        claim, operation_id, "artifact_quota_exceeded"
                    )
                    return operation_id, result, None, None
                raise
            except ToolPublicationError as refused:
                db.rollback()
                return operation_id, self._interrupted(operation_id, refused.code), None, None
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
            operation = db.query(ToolOperation).filter(
                ToolOperation.id == operation_id
            ).first()
            if operation is None:
                raise ToolPublicationError("execution_interrupted")
            return operation

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
    ):
        db = self._session_factory()
        try:
            result = publish_tool_result(db, claim, attempt_id, observation, (staged,))
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
        except HTTPException:
            db.rollback()
            self._cleanup_uncommitted(staged, store, context)
            return self._interrupted(operation_id, "execution_interrupted")
        except Exception:  # noqa: BLE001 - preserve bytes until commit state is known.
            db.rollback()
            return self._read_after_uncertain_commit(operation_id)
        finally:
            db.close()

    def _read_after_uncertain_commit(self, operation_id):
        try:
            with self._session_factory() as db:
                operation = db.query(ToolOperation).filter(
                    ToolOperation.id == operation_id
                ).first()
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
                artifact = db.query(Artifact).filter(
                    Artifact.id == staged.artifact_id
                ).first()
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
