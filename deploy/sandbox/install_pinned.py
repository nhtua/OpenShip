"""Fetch one digest-pinned release archive and extract it only after verification.

usage: python3 install_pinned.py URL EXPECTED_SHA256 TARGET_DIR

Streams the download to a scratch file while hashing, refuses any checksum
mismatch before extraction, and unpacks with the standard library only. The
slim Debian snapshot has no curl, so the fetch goes through urllib.
"""

import hashlib
import sys
import tarfile
from urllib.request import urlopen

SCRATCH = "/tmp/openship-pinned-archive.bin"


def main(url: str, digest: str, target: str) -> None:
    hasher = hashlib.sha256()
    with urlopen(url) as response, open(SCRATCH, "wb") as sink:
        for chunk in iter(lambda: response.read(1 << 20), b""):
            hasher.update(chunk)
            sink.write(chunk)
    if hasher.hexdigest() != digest:
        raise SystemExit("archive checksum mismatch: refusing to extract the pinned archive")
    with tarfile.open(SCRATCH, "r:gz") as archive:
        archive.extractall(target, filter="tar")


main(*sys.argv[1:4])
