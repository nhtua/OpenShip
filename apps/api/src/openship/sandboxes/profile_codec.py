"""JSON/base64 stdin bridge for the stdlib-only host image tooling.

Requests: {"action":"decode", "yaml_base64":"..."} or
{"action":"encode", "catalog":{...}}. Responses contain respectively
"catalog" or "yaml_base64". No file, settings, or database access is needed.
"""

import base64
import binascii
import json
import sys
from collections.abc import Sequence

from src.openship.capabilities.contracts import canonical_json
from src.openship.sandboxes.profile_schema import (
    ProfileCatalog,
    decode_catalog,
    encode_catalog,
)

_MAX_BRIDGE_BYTES = 1024 * 1024


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def main(argv: Sequence[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if args:
            raise ValueError("unexpected arguments")
        raw = sys.stdin.buffer.read(_MAX_BRIDGE_BYTES + 1)
        if len(raw) > _MAX_BRIDGE_BYTES:
            raise ValueError("bridge input exceeds limit")
        request = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        if not isinstance(request, dict):
            raise TypeError("request must be an object")
        if request.get("action") == "decode" and set(request) == {"action", "yaml_base64"}:
            data = base64.b64decode(request["yaml_base64"], validate=True)
            response = {"catalog": decode_catalog(data).model_dump(mode="json")}
        elif request.get("action") == "encode" and set(request) == {"action", "catalog"}:
            catalog = ProfileCatalog.model_validate_json(canonical_json(request["catalog"]))
            response = {"yaml_base64": base64.b64encode(encode_catalog(catalog)).decode("ascii")}
        else:
            raise ValueError("invalid bridge request")
        output = canonical_json(response) + b"\n"
        if len(output) > _MAX_BRIDGE_BYTES:
            raise ValueError("bridge output exceeds limit")
        sys.stdout.buffer.write(output)
        return 0
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        print("Invalid profile codec request or catalog", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
