"""Standard sandbox smoke: python >=3.12, stdlib modules, regex and SymPy,
and a real node -e computation. Offline only: no package installation here."""

import subprocess
import sys


def main() -> int:
    if sys.version_info[:2] < (3, 12):
        print(f"standard smoke: requires python >= 3.12, found {sys.version.split()[0]}", file=sys.stderr)
        return 1
    import csv
    import decimal
    import json
    import math
    import re
    import uuid

    probes = [
        math.comb(7, 3) == 35,
        decimal.Decimal("0.1") + decimal.Decimal("0.2") == decimal.Decimal("0.3"),
        len(uuid.UUID(str(uuid.uuid4())).hex) == 32,
        json.loads(json.dumps({"rows": [1, 2]})) == {"rows": [1, 2]},
        re.fullmatch(r"[a-z]+=42", "answer=42") is not None,
    ]
    rows = list(csv.reader("value,label\n7,answer\n".splitlines()))
    probes.append(rows[1] == ["7", "answer"])

    try:
        import regex
    except ImportError:
        print("standard smoke: missing third-party module: regex", file=sys.stderr)
        return 1
    match = regex.search(r"(?P<key>[a-z]+)=(?P<value>\d+)", "answer=42")
    probes.append(match is not None and int(match["value"]) == 42)

    try:
        import sympy
    except ImportError:
        print("standard smoke: missing third-party module: sympy", file=sys.stderr)
        return 1
    x = sympy.Symbol("x")
    probes.append(sympy.simplify((x + 2) ** 2 - (x**2 + 4 * x + 4)) == 0)

    try:
        node = subprocess.run(["node", "-e", "console.log(6 * 7)"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print("standard smoke: missing node runtime", file=sys.stderr)
        return 1
    if node.returncode != 0:
        print(f"standard smoke: node -e failed with status {node.returncode}", file=sys.stderr)
        return 1
    try:
        answer = int(node.stdout.strip())
    except ValueError:
        print(f"standard smoke: node -e output {node.stdout.strip()!r} is not a number", file=sys.stderr)
        return 1
    probes.append(answer == 42)

    if not all(probes):
        print("standard smoke: one or more probes failed", file=sys.stderr)
        return 1
    print(
        f"standard smoke passed (python {sys.version.split()[0]}, sympy {sympy.__version__},"
        f" regex {regex.__version__}, node answer {answer})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
