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
from .cutover import (
    apply_cutover,
    apply_rollback,
    close_admission_gate,
    close_execution_gate,
    open_admission_gate,
    open_execution_gate,
    plan_rollback,
    verify_cutover,
)
from .exhaustion_supervisor import ExhaustionSupervisor
from .migration import plan_cutover
from .models import MessageTransportState
from .startup import (
    StartupCutoverBlocked,
    apply_startup_cutover,
    startup_report,
)
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
            stream="OS_RUNS",
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


async def _dispatch_gate(
    command: str, *, action: str | None, cutover_id: UUID | None
) -> int:
    if action not in {"open", "close"} or cutover_id is None:
        print(
            json.dumps(
                {"status": "refused", "errors": ["unsupported_gate_action"]},
                sort_keys=True,
            )
        )
        return 1
    try:
        if action == "close":
            with SessionLocal() as db:
                if command == "execution":
                    report = close_execution_gate(db, cutover_id=cutover_id)
                else:
                    report = close_admission_gate(db, cutover_id=cutover_id)
            print(json.dumps(report.as_dict(), sort_keys=True))
            return 0

        with SessionLocal() as db:
            state = db.get(MessageTransportState, 1)
            installation_id = state.installation_id if state else None
        if installation_id is None:
            raise RuntimeError("messaging state is missing")
        topology = await ensure_topology(
            apply=False,
            installation_id=installation_id,
        )
        if not topology.ready:
            print(
                json.dumps(
                    {
                        "status": "refused",
                        "errors": ["topology_not_ready"],
                        "topology": topology.as_dict(),
                    },
                    sort_keys=True,
                )
            )
            return 1
        with SessionLocal() as db:
            if command == "execution":
                report = open_execution_gate(db, cutover_id=cutover_id)
            else:
                report = open_admission_gate(db, cutover_id=cutover_id)
        result = report.as_dict()
        result["topology"] = topology.as_dict()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:  # noqa: BLE001 - keep DB and broker details private
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "errors": [f"gate_{action}_unavailable:{type(error).__name__}"],
                },
                sort_keys=True,
            )
        )
        return 1


async def _dispatch(
    command: str,
    *,
    apply: bool = False,
    after_sequence: int = 0,
    limit: int = 100,
    operator: str | None = None,
    action: str | None = None,
    cutover_id: UUID | None = None,
    batch_size: int = 500,
    legacy_writers_stopped: bool = False,
    development_confirmed: bool = False,
    processes_stopped: bool = False,
    consumers_drained: bool = False,
) -> int:
    if command in {"execution", "admission"}:
        gate_action = action
        return await _dispatch_gate(
            command,
            action=gate_action,
            cutover_id=cutover_id,
        )
    if command == "reconcile":
        return await _dispatch_reconcile(
            apply=apply,
            after_sequence=after_sequence,
            limit=limit,
            operator=operator,
        )
    if command == "startup":
        if action == "preview":
            try:
                with SessionLocal() as db:
                    report = startup_report(db)
                print(json.dumps(report.as_dict(), sort_keys=True))
                return 0
            except Exception as error:  # noqa: BLE001 - keep DB details private
                print(
                    json.dumps(
                        {
                            "status": "unavailable",
                            "errors": [
                                f"startup_preview_unavailable:{type(error).__name__}"
                            ],
                        },
                        sort_keys=True,
                    )
                )
                return 1
        if action == "apply" and legacy_writers_stopped and development_confirmed:
            try:
                with SessionLocal() as db:
                    state = db.get(MessageTransportState, 1)
                    installation_id = state.installation_id if state else None
                if installation_id is None:
                    raise RuntimeError("messaging state is missing")
                topology = await ensure_topology(
                    apply=False,
                    installation_id=installation_id,
                )
                if not topology.ready:
                    print(
                        json.dumps(
                            {
                                "status": "refused",
                                "errors": ["topology_not_ready"],
                                "topology": topology.as_dict(),
                            },
                            sort_keys=True,
                        )
                    )
                    return 1
                with SessionLocal() as db:
                    report = apply_startup_cutover(
                        db,
                        cutover_id=cutover_id,
                        writers_stopped=True,
                        development_confirmed=True,
                    )
                result = report.as_dict()
                result["topology"] = topology.as_dict()
                print(json.dumps(result, sort_keys=True))
                return 0
            except StartupCutoverBlocked as error:
                print(json.dumps(error.report.as_dict(), sort_keys=True))
                return 1
            except Exception as error:  # noqa: BLE001 - keep DB details private
                print(
                    json.dumps(
                        {
                            "status": "unavailable",
                            "errors": [
                                f"startup_apply_unavailable:{type(error).__name__}"
                            ],
                        },
                        sort_keys=True,
                    )
                )
                return 1
        print(
            json.dumps(
                {"status": "refused", "errors": ["unsupported_startup_action"]},
                sort_keys=True,
            )
        )
        return 1
    if command == "cutover":
        if cutover_id is None or action not in {"preview", "apply", "verify"}:
            result = {
                "status": "refused",
                "errors": ["unsupported_cutover_action"],
            }
            print(json.dumps(result, sort_keys=True))
            return 1
        try:
            if action == "preview":
                with SessionLocal() as db:
                    report = plan_cutover(db, cutover_id=cutover_id)
                print(json.dumps(report.as_dict(), sort_keys=True))
                return 1 if report.status == "blocked" else 0
            if action == "apply":
                with SessionLocal() as db:
                    state = db.get(MessageTransportState, 1)
                    installation_id = state.installation_id if state else None
                if installation_id is None:
                    result = {
                        "status": "refused",
                        "errors": ["messaging_state_missing"],
                    }
                    print(json.dumps(result, sort_keys=True))
                    return 1
                topology = await ensure_topology(
                    apply=False,
                    installation_id=installation_id,
                )
                if not topology.ready:
                    result = {
                        "status": "refused",
                        "errors": ["topology_not_ready"],
                        "topology": topology.as_dict(),
                    }
                    print(json.dumps(result, sort_keys=True))
                    return 1
                with SessionLocal() as db:
                    report = apply_cutover(
                        db,
                        cutover_id=cutover_id,
                        operator=operator,
                        batch_size=batch_size,
                        legacy_writers_stopped=legacy_writers_stopped,
                    )
                result = report.as_dict()
                result["topology"] = topology.as_dict()
                print(json.dumps(result, sort_keys=True))
                return 0 if report.status == "applied" else 1

            with SessionLocal() as db:
                verification = verify_cutover(db, cutover_id=cutover_id)
                state = db.get(MessageTransportState, 1)
                installation_id = state.installation_id if state else None
            result = verification.as_dict()
            if installation_id is None:
                result["errors"].append("messaging_state_missing")
                result.update(status="not_ready", ready=False)
            else:
                topology = await ensure_topology(
                    apply=False,
                    installation_id=installation_id,
                )
                result["topology"] = topology.as_dict()
                if not topology.ready:
                    result["errors"].append("topology_not_ready")
                    result.update(status="not_ready", ready=False)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ready"] else 1
        except Exception as error:  # noqa: BLE001 - keep DB details private
            result = {
                "status": "unavailable",
                "errors": [f"cutover_{action}_unavailable:{type(error).__name__}"],
            }
            print(json.dumps(result, sort_keys=True))
            return 1
    if command == "rollback":
        if cutover_id is None or action not in {"preview", "apply"}:
            print(
                json.dumps(
                    {"status": "refused", "errors": ["unsupported_rollback_action"]},
                    sort_keys=True,
                )
            )
            return 1
        try:
            with SessionLocal() as db:
                if action == "preview":
                    report = plan_rollback(db, cutover_id=cutover_id)
                else:
                    report = apply_rollback(
                        db,
                        cutover_id=cutover_id,
                        operator=operator,
                        batch_size=batch_size,
                        processes_stopped=processes_stopped,
                        consumers_drained=consumers_drained,
                    )
            print(json.dumps(report.as_dict(), sort_keys=True))
            return 0 if report.ready else 1
        except Exception as error:  # noqa: BLE001 - keep DB details private
            print(
                json.dumps(
                    {
                        "status": "unavailable",
                        "errors": [
                            f"rollback_{action}_unavailable:{type(error).__name__}"
                        ],
                    },
                    sort_keys=True,
                )
            )
            return 1
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
        description="Operate OpenShip messaging and inspect the durable queue migration.",
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
    cutover = commands.add_parser(
        "cutover", help="inspect the existing run queue before migration"
    )
    cutover_actions = cutover.add_subparsers(dest="cutover_action", required=True)
    preview = cutover_actions.add_parser("preview", help="read-only cutover inventory")
    preview.add_argument("--cutover-id", type=UUID, required=True)
    apply_cutover_command = cutover_actions.add_parser(
        "apply", help="backfill event and eligible run messages in resumable batches"
    )
    apply_cutover_command.add_argument("--cutover-id", type=UUID, required=True)
    apply_cutover_command.add_argument(
        "--operator",
        required=True,
        help="operator identity recorded in cutover progress",
    )
    apply_cutover_command.add_argument("--batch-size", type=int, default=500)
    apply_cutover_command.add_argument(
        "--writers-stopped",
        action="store_true",
        required=True,
        help="attest that legacy API and worker processes are stopped",
    )
    verify = cutover_actions.add_parser(
        "verify", help="verify completed backfill, publication catch-up and topology"
    )
    verify.add_argument("--cutover-id", type=UUID, required=True)
    startup = commands.add_parser(
        "startup", help="inspect or activate the first JetStream-backed startup"
    )
    startup_actions = startup.add_subparsers(dest="startup_action", required=True)
    startup_actions.add_parser("preview", help="read-only startup and cutover report")
    startup_apply = startup_actions.add_parser(
        "apply", help="apply an explicitly confirmed, unblocked dev cutover"
    )
    startup_apply.add_argument(
        "--cutover-id",
        type=UUID,
        required=True,
        help="the stable cutover ID shown by startup preview",
    )
    startup_apply.add_argument(
        "--writers-stopped",
        action="store_true",
        required=True,
        help="attest that legacy and Compose app writers are stopped",
    )
    startup_apply.add_argument(
        "--dev-confirmed",
        action="store_true",
        required=True,
        help="confirm this automated cutover is for disposable dev data",
    )
    rollback = commands.add_parser(
        "rollback", help="inspect or apply the drained return to DB polling"
    )
    rollback_actions = rollback.add_subparsers(dest="rollback_action", required=True)
    rollback_preview = rollback_actions.add_parser(
        "preview", help="read-only rollback readiness inventory"
    )
    rollback_preview.add_argument("--cutover-id", type=UUID, required=True)
    rollback_apply_command = rollback_actions.add_parser(
        "apply", help="fence the NATS epoch and prepare the old runtime"
    )
    rollback_apply_command.add_argument("--cutover-id", type=UUID, required=True)
    rollback_apply_command.add_argument(
        "--operator", required=True, help="operator identity recorded in progress"
    )
    rollback_apply_command.add_argument("--batch-size", type=int, default=500)
    rollback_apply_command.add_argument(
        "--processes-stopped",
        action="store_true",
        required=True,
        help="attest API, workers and relay are stopped",
    )
    rollback_apply_command.add_argument(
        "--consumers-drained",
        action="store_true",
        required=True,
        help="attest JetStream consumer deliveries are drained or reconciled",
    )
    for gate_name in ("execution", "admission"):
        gate = commands.add_parser(gate_name, help=f"control the {gate_name} gate")
        gate_actions = gate.add_subparsers(dest="gate_action", required=True)
        close_gate = gate_actions.add_parser(
            "close", help=f"close {gate_name} without requiring broker availability"
        )
        close_gate.add_argument("--cutover-id", type=UUID, required=True)
        open_gate = gate_actions.add_parser(
            "open", help=f"open {gate_name} after verification"
        )
        open_gate.add_argument("--cutover-id", type=UUID, required=True)
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
            cutover_id=getattr(args, "cutover_id", None),
            batch_size=getattr(args, "batch_size", 500),
            legacy_writers_stopped=bool(getattr(args, "writers_stopped", False)),
            development_confirmed=bool(getattr(args, "dev_confirmed", False)),
            processes_stopped=bool(getattr(args, "processes_stopped", False)),
            consumers_drained=bool(getattr(args, "consumers_drained", False)),
            action=getattr(args, "gate_action", None)
            or getattr(args, "cutover_action", None)
            or getattr(args, "startup_action", None)
            or getattr(args, "rollback_action", None),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
