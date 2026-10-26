"""Operator checks for the JetStream topology."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from sqlalchemy import text

from ..database.session import SessionLocal
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


async def _dispatch(command: str, *, apply: bool = False) -> int:
    if command == "doctor":
        result: dict[str, object] = {"database": None, "topology": None}
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
            result["ready"] = topology.ready
        except Exception as error:  # noqa: BLE001 - avoid printing URLs or auth data
            result["topology"] = {
                "status": "unavailable",
                "error": type(error).__name__,
            }
            result["ready"] = False
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
        description="Check or initialize OpenShip's JetStream streams.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="check or create missing streams")
    mode = bootstrap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="report missing or drifted streams"
    )
    mode.add_argument(
        "--apply", action="store_true", help="create only missing streams"
    )
    commands.add_parser("doctor", help="check database state and stream compatibility")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(
        _dispatch(
            args.command,
            apply=bool(getattr(args, "apply", False)),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
