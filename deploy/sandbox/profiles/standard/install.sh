set -eu
install -d --mode=0755 /opt/node
python3 - "$NODE_URL" "$NODE_SHA256" /opt/node <<'PY'
import hashlib, sys, tarfile
from urllib.request import urlopen
url, digest, target = sys.argv[1:4]
scratch = "/tmp/openship-pinned-archive.bin"
hasher = hashlib.sha256()
with urlopen(url) as response, open(scratch, "wb") as sink:
    for chunk in iter(lambda: response.read(1 << 20), b""):
        hasher.update(chunk)
        sink.write(chunk)
if hasher.hexdigest() != digest:
    raise SystemExit("archive checksum mismatch: refusing to extract the pinned archive")
with tarfile.open(scratch, "r:gz") as archive:
    archive.extractall(target, filter="tar")
PY
for node_dir in /opt/node/node-v*; do mv "$node_dir" /opt/node/current; done
rm -f /tmp/openship-pinned-archive.bin
