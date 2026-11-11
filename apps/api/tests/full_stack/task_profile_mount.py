"""Host-lane equivalent of the operator's profile mount for the task journeys.

The sandbox profile snapshot is loaded by the runtime's own loader from the
operator-provisioned file in the repository; only the mount point differs,
because the fixed container path ``/etc/openship/sandbox-profiles.yaml`` is not
writable on a host process. The snapshot bytes are the file the operator mounts
unchanged, so every profile identity below (pinned image id, dependency
fingerprint, platform, smoke evidence) is the installation's own. Nothing about
the sandbox transport, the gateway or the artifact store is replaced here.

The swap is on the loader name that the runtime's own ``startup_catalog`` looks
up when it reads the mount, so the readiness path (enabled profile, smoke
evidence, platform) still runs over the operator's snapshot bytes.
"""

from __future__ import annotations

from pathlib import Path

import src.openship.sandboxes.profiles as profile_runtime
from src.openship.sandboxes.profiles import (
    ProfileCatalog,
    install_runtime_catalog,
    load_runtime_catalog,
)


def install_profile_snapshot(path: str) -> None:
    """Mount one operator-provided profile snapshot for this process."""
    snapshot = Path(path)
    assert snapshot.is_file(), "operator profile snapshot is missing"
    catalog: ProfileCatalog = load_runtime_catalog(snapshot)

    def read_mount(_path: object) -> ProfileCatalog:
        del _path  # the host mount point differs; the bytes do not
        return catalog

    install_runtime_catalog(catalog)
    profile_runtime.load_runtime_catalog = read_mount
