"""Operator checks for the JetStream topology."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text

from ..database.session import SessionLocal
from .capacity import capacity_report, scan_retained_run_sources
from .capacity_recovery import recovery_gate_errors
from .connection import connect_nats
from .exhaustion_supervisor import ExhaustionSupervisor
from .models import MessageTransportState
from .topology import TopologyReport, ensure_topology


def _database_report() -> dict:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        state = db.get(MessageTransportState, 1)
        if state is None:
            raise RuntimeError("messaging state is missing")
        return {
            "status": "ready",
            "installation_id": str(state.installation_id),
            "active": state.active,
            "admission_open": state.admission_open,
            "execution_open": state.execution_open,
        }


def _topology_report(report: TopologyReport) -> dict:
    return report.as_dict()


async def _advisory_capacity_report() -> dict:
    client = await connect_nats(
        name="openship-messaging-doctor",
        allow_reconnect=False,
    )
    try:
        info = await client.jetstream(timeout=5).stream_info("OS_ADVISORIES")
        return capacity_report(info.state.bytes, info.config.max_bytes).as_dict()
    finally:
        await client.close()


async def _dispatch_reconcile(
    *,
    apply: bool,
    after_sequence: int,
    limit: int,
    operator: str | None,
) -> int:
    result: dict[str, object] = {
        "mode": "apply" if apply else "preview",
        "read_only": not apply,
        "candidates": [],
    }
    if apply and (
        not isinstance(operator, str)
        or not operator.strip()
        or len(operator.strip()) > 255
    ):
        result.update(status="refused", errors=["operator_identity_required"])
        print(json.dumps(result, sort_keys=True))
        return 1
    if apply:
        result["operator"] = operator.strip()

    try:
        database = _database_report()
        result["database"] = {
            "active": database["active"],
            "admission_open": database["admission_open"],
            "execution_open": database["execution_open"],
        }
        installation_id = UUID(database["installation_id"])
    except Exception as error:  # noqa: BLE001 - keep connection details private
        result.update(
            status="unavailable",
            errors=[f"database_unavailable:{type(error).__name__}"],
        )
        print(json.dumps(result, sort_keys=True))
        return 1

    client = None
    try:
        client = await connect_nats(
            name="openship-messaging-capacity-recovery",
            allow_reconnect=False,
        )
        js = client.jetstream(timeout=5)
        topology = await ensure_topology(
            apply=False,
            js=js,
            installation_id=installation_id,
        )
        if not topology.ready:
            result.update(status="refused", errors=["topology_not_ready"])
            result["topology"] = topology.as_dict()
            print(json.dumps(result, sort_keys=True))
            return 1

        run_info = await js.consumer_info("OS_RUNS", "run-v1")
        maintenance_info = await js.consumer_info("OS_MAINTENANCE", "maintenance-v1")
        consumers = {
            ("OS_RUNS", "run-v1"): run_info,
            ("OS_MAINTENANCE", "maintenance-v1"): maintenance_info,
        }
        gate_errors = recovery_gate_errors(
            transport_active=database["active"],
            admission_open=database["admission_open"],
            execution_open=database["execution_open"],
            consumers=consumers,
        )
        result["apply_ready"] = not gate_errors
        result["apply_gate_errors"] = list(gate_errors)

        if apply and gate_errors:
            result.update(status="refused", errors=list(gate_errors))
            print(json.dumps(result, sort_keys=True))
            return 1

        advisory_info = await js.stream_info("OS_ADVISORIES")
        capacity = capacity_report(
            advisory_info.state.bytes, advisory_info.config.max_bytes
        )
        result["advisory_capacity"] = capacity.as_dict()

        delivered = getattr(run_info, "delivered", None)
        high_water = getattr(delivered, "stream_seq", 0) or 0
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after-sequence must be a non-negative integer")
        scan = await scan_retained_run_sources(
            js,
            subject=f"openship.{installation_id.hex}.v1.command.run.execute",
            after_sequence=after_sequence,
            through_sequence=high_water,
            limit=limit,
        )
        result.update(
            through_sequence=scan.through_sequence,
            next_after_sequence=scan.next_after_sequence,
            truncated=scan.truncated,
            candidate_count=len(scan.messages),
            candidates=[
                {
                    "sequence": message.seq,
                    "timestamp": message.time.isoformat()
                    if hasattr(message.time, "isoformat")
                    else str(message.time),
                    "body_sha256": hashlib.sha256(message.data).hexdigest(),
                }
                for message in scan.messages
            ],
        )
        if not apply:
            result["status"] = "preview"
            print(json.dumps(result, sort_keys=True))
            return 0

        supervisor = ExhaustionSupervisor(
            client,
            installation_id=installation_id,
            session_factory=SessionLocal,
        )
        async with supervisor.operator_recovery_session():
            outcomes = []
            for message in scan.messages:
                current_database = _database_report()
                current_run = await js.consumer_info("OS_RUNS", "run-v1")
                current_maintenance = await js.consumer_info(
                    "OS_MAINTENANCE", "maintenance-v1"
                )
                current_errors = recovery_gate_errors(
                    transport_active=current_database["active"],
                    admission_open=current_database["admission_open"],
                    execution_open=current_database["execution_open"],
                    consumers={
                        ("OS_RUNS", "run-v1"): current_run,
                        ("OS_MAINTENANCE", "maintenance-v1"): current_maintenance,
                    },
                )
                if current_errors:
                    result["errors"] = list(current_errors)
                    break
                outcome = await supervisor.reconcile_capacity_candidate(
                    message, operator=operator.strip()
                )
                outcomes.append({"sequence": message.seq, "state": outcome})
        result["outcomes"] = outcomes
        unresolved = any(
            item["state"] not in {"resolved", "resolved_failure"} for item in outcomes
        )
        complete = (
            not scan.truncated
            and len(outcomes) == len(scan.messages)
            and not unresolved
            and not result.get("errors")
        )
        result.update(status="applied", complete=complete)
        print(json.dumps(result, sort_keys=True))
        return 0 if complete else 1
    except Exception as error:  # noqa: BLE001 - avoid printing URLs or auth data
        result.update(
            status="unavailable",
            errors=[f"recovery_unavailable:{type(error).__name__}"],
        )
        print(json.dumps(result, sort_keys=True))
        return 1
    finally:
        if client is not None:
            await client.close()


async def _dispatch(
    command: str,
    *,
    apply: bool = False,
    after_sequence: int = 0,
    limit: int = 100,
    operator: str | None = None,
) -> int:
    if command == "reconcile":
        return await _dispatch_reconcile(
            apply=apply,
            after_sequence=after_sequence,
            limit=limit,
            operator=operator,
        )
    if command == "doctor":
        result: dict[str, object] = {
            "database": None,
            "topology": None,
            "advisory_capacity": None,
        }
        try:
            result["database"] = _database_report()
        except Exception as error:  # noqa: BLE001 - keep connection details private
            result["database"] = {
                "status": "unavailable",
                "error": type(error).__name__,
            }
            result["ready"] = False
            print(json.dumps(result, sort_keys=True))
            return 1
        try:
            topology = await ensure_topology(apply=False)
            result["topology"] = _topology_report(topology)
        except Exception as error:  # noqa: BLE001 - avoid printing URLs or auth data
            result["topology"] = {
                "status": "unavailable",
                "error": type(error).__name__,
            }
            topology = None
        try:
            capacity = await _advisory_capacity_report()
            result["advisory_capacity"] = capacity
        except Exception as error:  # noqa: BLE001 - avoid printing URLs or auth data
            result["advisory_capacity"] = {
                "status": "unavailable",
                "error": type(error).__name__,
            }
            capacity = None
        result["ready"] = bool(
            topology is not None
            and topology.ready
            and capacity is not None
            and capacity.get("alert") is False
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ready"] else 1

    try:
        topology = await ensure_topology(apply=apply)
        result = _topology_report(topology)
    except Exception as error:  # noqa: BLE001 - avoid printing URLs or auth data
        result = {"ready": False, "error": type(error).__name__}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openship-messaging",
        description="Check or initialize OpenShip's JetStream streams and consumers.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap", help="check or create missing resources"
    )
    mode = bootstrap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="report missing or drifted resources"
    )
    mode.add_argument(
        "--apply", action="store_true", help="create only missing resources"
    )
    commands.add_parser(
        "doctor", help="check database state and resource compatibility"
    )
    reconcile = commands.add_parser(
        "reconcile", help="preview or recover run sources after advisory overflow"
    )
    mode = reconcile.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true", help="read-only scan")
    mode.add_argument("--apply", action="store_true", help="record and safely recover")
    reconcile.add_argument("--after-sequence", type=int, default=0)
    reconcile.add_argument("--limit", type=int, default=100)
    reconcile.add_argument("--operator", help="operator identity required with --apply")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(
        _dispatch(
            args.command,
            apply=bool(getattr(args, "apply", False)),
            after_sequence=getattr(args, "after_sequence", 0),
            limit=getattr(args, "limit", 100),
            operator=getattr(args, "operator", None),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
