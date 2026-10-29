"""Logical file heads referencing immutable, already captured artifacts.

These functions do not commit; callers own the transaction.
"""

import uuid

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import Artifact, FileVersion, LogicalFile
from .routes import _active_conversation_scope


def _error(status, code, message):
    return HTTPException(
        status_code=status, detail={"error": {"code": code, "message": message}}
    )


def _capture(db, conversation, artifact_id):
    artifact = (
        db.query(Artifact)
        .filter(
            Artifact.id == artifact_id,
            Artifact.conversation_id == conversation.id,
            Artifact.project_id == conversation.project_id,
            Artifact.status == "ready",
        )
        .with_for_update()
        .first()
    )
    if artifact is None:
        raise _error(404, "artifact_not_found", "Artifact is not available")
    if artifact.media_type != "text/plain":
        raise _error(422, "invalid_file_type", "Logical files require plain text")
    return artifact


def _prior(db, conversation_id, key):
    return (
        db.query(FileVersion)
        .filter_by(conversation_id=conversation_id, request_key=key)
        .first()
    )


def create_file(
    db: Session,
    actor_id: uuid.UUID,
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    request_key: uuid.UUID,
):
    conversation = _active_conversation_scope(db, actor_id, str(conversation_id))
    prior = _prior(db, conversation_id, request_key)
    if prior:
        if prior.base_version_id is not None or prior.artifact_id != artifact_id:
            raise _error(
                409, "file_request_conflict", "Idempotency key has different content"
            )
        return db.get(LogicalFile, prior.file_id), prior
    artifact = _capture(db, conversation, artifact_id)
    file_id, version_id = uuid.uuid4(), uuid.uuid4()
    file = LogicalFile(
        id=file_id,
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        name=artifact.display_name,
        current_version_id=version_id,
    )
    version = FileVersion(
        id=version_id,
        file_id=file_id,
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        artifact_id=artifact.id,
        actor_id=actor_id,
        request_key=request_key,
    )
    db.add(file)
    db.flush()
    db.add(version)
    db.flush()
    return file, version


def advance_file(
    db: Session,
    actor_id: uuid.UUID,
    conversation_id: uuid.UUID,
    file_id: uuid.UUID,
    base_version_id: uuid.UUID,
    artifact_id: uuid.UUID,
    request_key: uuid.UUID,
):
    conversation = _active_conversation_scope(db, actor_id, str(conversation_id))
    file = (
        db.query(LogicalFile)
        .filter_by(
            id=file_id,
            conversation_id=conversation.id,
            project_id=conversation.project_id,
            deleted_at=None,
        )
        .with_for_update()
        .first()
    )
    if file is None:
        raise _error(404, "file_not_found", "File not found")
    prior = _prior(db, conversation_id, request_key)
    if prior:
        if (
            prior.file_id != file_id
            or prior.base_version_id != base_version_id
            or prior.artifact_id != artifact_id
        ):
            raise _error(
                409, "file_request_conflict", "Idempotency key has different content"
            )
        return prior
    artifact = _capture(db, conversation, artifact_id)
    version_id = uuid.uuid4()
    result = db.execute(
        update(LogicalFile)
        .where(
            LogicalFile.id == file_id,
            LogicalFile.current_version_id == base_version_id,
            LogicalFile.deleted_at.is_(None),
        )
        .values(current_version_id=version_id)
    )
    if result.rowcount != 1:
        raise _error(
            409, "file_version_conflict", "File has changed; reload its current version"
        )
    version = FileVersion(
        id=version_id,
        file_id=file_id,
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        artifact_id=artifact.id,
        base_version_id=base_version_id,
        actor_id=actor_id,
        request_key=request_key,
    )
    db.add(version)
    db.flush()
    return version
