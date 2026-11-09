set -eu
rm -f /etc/apt/sources.list.d/debian.sources
echo "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie main" > /etc/apt/sources.list
apt-get update -qq
grep -v '^#' /tmp/os-packages.lock | xargs apt-get install -y --no-install-recommends
rm -rf /var/lib/apt/lists/*
python3 - "$TFLINT_URL" "$TFLINT_SHA256" <<'PY'
import hashlib, io, os, sys, zipfile
from urllib.request import urlopen
with urlopen(sys.argv[1], timeout=60) as response:
    data = response.read()
if hashlib.sha256(data).hexdigest() != sys.argv[2]:
    raise SystemExit("TFLint archive checksum mismatch")
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    with open('/usr/local/bin/tflint', 'wb') as target:
        target.write(archive.read('tflint'))
os.chmod('/usr/local/bin/tflint', 0o755)
PY
