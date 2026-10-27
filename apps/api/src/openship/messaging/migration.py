"""Read-only preflight reports for the drained messaging cutover."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

_REQUIRED_CUTOVER_TABLES = {"runs", "run_jobs", "events", "outbox"}
_DUPLICATE_ID_PREVIEW_LIMIT = 100


@dataclass(frozen=True)
class CutoverReport:
    """Content-free inventory of the existing queue and event records."""

    cutover_id: uuid.UUID
    schema_stage: str
    run_counts: dict[str, int]
    job_rows: int
    duplicate_job_groups: int
    duplicate_job_run_ids: tuple[str, ...]
    duplicate_ids_truncated: bool
    event_rows: int
    outbox_rows: int

    @property
    def read_only(self) -> bool:
        return True

    @property
    def status(self) -> str:
        return "blocked" if self.duplicate_job_groups else "preview"

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "cutover_id": str(self.cutover_id),
            "read_only": self.read_only,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True)


def plan_cutover(db: Session, *, cutover_id: uuid.UUID) -> CutoverReport:
    """Inventory Phase 5 or Phase 6A records without changing either store."""
    if not isinstance(cutover_id, uuid.UUID):
        raise TypeError("cutover_id must be a UUID")

    inspector = inspect(db.get_bind())
    tables = set(inspector.get_table_names())
    missing = sorted(_REQUIRED_CUTOVER_TABLES - tables)
    if missing:
        raise RuntimeError("cutover schema is missing required tables")

    run_counts = {
        str(status): int(count)
        for status, count in db.execute(
            text("SELECT status, COUNT(*) FROM runs GROUP BY status ORDER BY status")
        ).all()
    }
    job_rows = int(db.execute(text("SELECT COUNT(*) FROM run_jobs")).scalar_one())
    duplicate_job_groups = int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM ("
                "SELECT run_id FROM run_jobs GROUP BY run_id HAVING COUNT(*) > 1"
                ") AS duplicate_jobs"
            )
        ).scalar_one()
    )
    duplicate_job_run_ids = tuple(
        str(uuid.UUID(str(run_id)))
        for (run_id,) in db.execute(
            text(
                "SELECT run_id FROM run_jobs GROUP BY run_id "
                "HAVING COUNT(*) > 1 ORDER BY run_id LIMIT :limit"
            ),
            {"limit": _DUPLICATE_ID_PREVIEW_LIMIT + 1},
        ).all()
    )
    duplicate_ids_truncated = len(duplicate_job_run_ids) > _DUPLICATE_ID_PREVIEW_LIMIT
    if duplicate_ids_truncated:
        duplicate_job_run_ids = duplicate_job_run_ids[:_DUPLICATE_ID_PREVIEW_LIMIT]

    event_rows = int(db.execute(text("SELECT COUNT(*) FROM events")).scalar_one())
    outbox_rows = int(db.execute(text("SELECT COUNT(*) FROM outbox")).scalar_one())

    return CutoverReport(
        cutover_id=cutover_id,
        schema_stage="phase6a" if "messaging_state" in tables else "phase5",
        run_counts=run_counts,
        job_rows=job_rows,
        duplicate_job_groups=duplicate_job_groups,
        duplicate_job_run_ids=duplicate_job_run_ids,
        duplicate_ids_truncated=duplicate_ids_truncated,
        event_rows=event_rows,
        outbox_rows=outbox_rows,
    )
