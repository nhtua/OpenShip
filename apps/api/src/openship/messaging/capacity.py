"""Advisory-stream capacity reporting and bounded source enumeration."""

from __future__ import annotations

from dataclasses import dataclass

from nats.js.errors import NotFoundError

ADVISORY_STREAM_MAX_BYTES = 64 * 1024 * 1024
ADVISORY_CAPACITY_ALERT_PERCENT = 75
MAX_RECOVERY_SCAN_BATCH = 500


@dataclass(frozen=True)
class AdvisoryCapacityReport:
    stored_bytes: int
    max_bytes: int
    alert_bytes: int
    utilization_percent: float
    alert: bool
    full: bool

    def as_dict(self) -> dict[str, int | float | bool | str]:
        return {
            "status": "alert" if self.alert else "ready",
            "stored_bytes": self.stored_bytes,
            "max_bytes": self.max_bytes,
            "alert_bytes": self.alert_bytes,
            "utilization_percent": self.utilization_percent,
            "alert": self.alert,
            "full": self.full,
        }


@dataclass(frozen=True)
class RetainedSourceScan:
    messages: tuple[object, ...]
    through_sequence: int
    next_after_sequence: int
    truncated: bool


def capacity_report(stored_bytes: int, max_bytes: int) -> AdvisoryCapacityReport:
    """Return a stable 75% alert and full-capacity view from broker byte counts."""
    if (
        type(stored_bytes) is not int
        or stored_bytes < 0
        or type(max_bytes) is not int
        or max_bytes <= 0
    ):
        raise ValueError("advisory stream byte counts are invalid")
    alert_bytes = (max_bytes * ADVISORY_CAPACITY_ALERT_PERCENT + 99) // 100
    return AdvisoryCapacityReport(
        stored_bytes=stored_bytes,
        max_bytes=max_bytes,
        alert_bytes=alert_bytes,
        utilization_percent=round(stored_bytes * 100 / max_bytes, 2),
        alert=stored_bytes >= alert_bytes,
        full=stored_bytes >= max_bytes,
    )


async def scan_retained_run_sources(
    js,
    *,
    subject: str,
    after_sequence: int,
    through_sequence: int,
    limit: int,
) -> RetainedSourceScan:
    """Enumerate exact-subject retained sources without crossing a high-water mark.

    The caller must obtain ``through_sequence`` from ``run-v1`` consumer info.
    This scan identifies reconciliation candidates; it does not infer delivery
    counts or independently authorize a source disposition.
    """
    if (
        not isinstance(subject, str)
        or not subject
        or type(after_sequence) is not int
        or after_sequence < 0
        or type(through_sequence) is not int
        or through_sequence < 0
        or type(limit) is not int
        or not 1 <= limit <= MAX_RECOVERY_SCAN_BATCH
    ):
        raise ValueError("retained source scan bounds are invalid")

    cursor = after_sequence
    messages = []
    truncated = False
    while cursor < through_sequence:
        try:
            message = await js.get_msg(
                "OS_RUNS", seq=cursor, subject=subject, next=True
            )
        except NotFoundError:
            break
        sequence = getattr(message, "seq", None)
        if type(sequence) is not int or sequence <= cursor:
            raise ValueError("retained source scan did not advance")
        if sequence > through_sequence:
            break
        if message.subject != subject or not isinstance(message.data, bytes):
            raise ValueError("retained source does not match the exact run subject")
        if len(messages) == limit:
            truncated = True
            break
        messages.append(message)
        cursor = sequence

    return RetainedSourceScan(
        messages=tuple(messages),
        through_sequence=through_sequence,
        next_after_sequence=cursor,
        truncated=truncated,
    )
