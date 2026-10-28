"""Fail-closed checks shared by the advisory-capacity recovery command."""

from __future__ import annotations


def recovery_gate_errors(
    *,
    transport_active: bool,
    admission_open: bool,
    execution_open: bool,
    consumers: dict[tuple[str, str], object],
) -> tuple[str, ...]:
    """Return stable refusal codes unless all execution delivery is drained."""
    errors = []
    if transport_active is not True:
        errors.append("transport_inactive")
    if admission_open is not False:
        errors.append("admission_gate_open")
    if execution_open is not False:
        errors.append("execution_gate_open")
    for stream, durable in (
        ("OS_RUNS", "run-v1"),
        ("OS_MAINTENANCE", "maintenance-v1"),
    ):
        info = consumers.get((stream, durable))
        if info is None:
            errors.append(f"consumer_missing:{stream}:{durable}")
            continue
        if getattr(info, "paused", None) is not True:
            errors.append(f"consumer_not_paused:{stream}:{durable}")
        if getattr(info, "num_ack_pending", None) != 0:
            errors.append(f"consumer_not_drained:{stream}:{durable}")
    return tuple(errors)
