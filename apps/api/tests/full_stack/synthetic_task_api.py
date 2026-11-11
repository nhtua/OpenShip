"""Run the real API process with the host profile mount (see task_profile_mount).

The application itself is untouched: this launches ``src.openship.main:app``
through uvicorn's own server, exactly as the operator's container does, after
mounting the operator-provided sandbox profile snapshot at the location the
runtime reads. Used only by the enabled agentic-task full-stack lane.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from tests.full_stack.task_profile_mount import install_profile_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic task API process")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    install_profile_snapshot(os.environ["SYNTHETIC_PROFILE_SNAPSHOT"])
    uvicorn.run(
        "src.openship.main:app",
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
