"""The live agentic task worker: real model, real gateway, real download.

Two harness conveniences and nothing else: the operator's profile snapshot is
mounted where the runtime reads it (the location the deployment's fixed mount
provides), and the process is the product's own ``Worker.run`` loop through the
existing worker harness entry. No adapter, tool, sandbox client, transport,
queue, store, gateway or download path is stubbed, and no fault is armed here:
the live lane runs the journey exactly as an operator's stack would.
"""

from __future__ import annotations

import os

from tests.full_stack.legacy_worker_harness import main as worker_main
from tests.full_stack.task_profile_mount import install_profile_snapshot


def main() -> None:
    install_profile_snapshot(os.environ["OPENSHIP_LIVE_AGENTIC_PROFILES_FILE"])
    worker_main()


if __name__ == "__main__":
    main()
