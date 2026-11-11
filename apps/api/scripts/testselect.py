"""Map a commit's files to the tests that import them.

The commit gate needs a selection it can explain: this module reads the
repository's own imports with ``ast`` and answers which test modules can
observe a changed source module. It is a pure function over a file list with
no cache: 412 files parse in about half a second, so staleness is not a risk
worth a cache.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

APPS_API = Path(__file__).resolve().parents[1]
REPO_ROOT = APPS_API.parents[1]


@dataclass(frozen=True)
class FullLane:
    """The selection cannot be trusted: run the whole credential-free lane."""

    reason: str
    kind: str = "full-lane"


@dataclass(frozen=True)
class Selected:
    """The test modules a changed file can affect, with the reason for each."""

    files: tuple[str, ...]
    reasons: tuple[tuple[str, str], ...]
    kind: str = "selected"


@dataclass(frozen=True)
class Skip:
    """No changed file is visible to the test lanes."""

    reason: str
    kind: str = "skip"


def _module_name(path: Path, api_root: Path) -> str:
    parts = list(path.relative_to(api_root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_names(node: ast.AST) -> set[str]:
    """Every dotted module an import statement can make this file depend on."""
    names: set[str] = set()
    if isinstance(node, ast.ImportFrom):
        if node.module is None:
            return names
        candidates = {node.module, *(f"{node.module}.{alias.name}" for alias in node.names)}
    elif isinstance(node, ast.Import):
        candidates = {alias.name for alias in node.names}
    else:
        return names
    for candidate in candidates:
        parts = candidate.split(".")
        for index in range(1, len(parts) + 1):
            names.add(".".join(parts[:index]))
    return names


GRAPH_ROOTS = ("src.", "tests.")


def build_graph(api_root: Path) -> dict[str, set[str]]:
    """Dotted module name -> the dotted names whose imports mention it."""
    graph: dict[str, set[str]] = {}
    for directory in (api_root / "src", api_root / "tests"):
        for path in sorted(directory.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            importer = _module_name(path, api_root)
            for node in ast.walk(tree):
                for imported in _imported_names(node):
                    if not imported.startswith(GRAPH_ROOTS):
                        continue
                    graph.setdefault(imported, set()).add(importer)
    return graph


def _node_path(node: str, api_root: Path) -> str:
    path = api_root.joinpath(*node.split(".")).with_suffix(".py")
    return path.relative_to(api_root.parents[1]).as_posix()


def tests_for_src(graph: dict[str, set[str]], module: str, api_root: Path) -> set[str]:
    """Test modules importing ``module``, closing transitively through ``src``."""
    selected: set[str] = set()
    seen: set[str] = {module}
    pending = [module]
    while pending:
        node = pending.pop()
        for importer in graph.get(node, set()):
            if importer in seen:
                continue
            seen.add(importer)
            if importer.startswith("tests."):
                selected.add(_node_path(importer, api_root))
            elif importer.startswith("src."):
                pending.append(importer)
    return selected


Plan = FullLane | Selected | Skip

# A commit touching any of these runs the whole credential-free lane: the
# selection cannot see what shared infrastructure changes, and an empty
# selection is never a pass.
FULL_LANE_TRIGGERS: tuple[str, ...] = (
    "apps/api/pyproject.toml",
    "apps/api/uv.lock",
    ".pre-commit-config.yaml",
    "apps/api/scripts/",
    "scripts/",
    "apps/api/alembic/",
    "apps/api/tests/fixtures/",
)

# Paths the test lanes never read, so they cannot select or force anything.
IGNORED_PREFIXES: tuple[str, ...] = ("docs/", "deploy/", "poc/", "apps/web/", ".github/")

# Paths whose changes the import graph can follow.
CODE_PREFIXES: tuple[str, ...] = ("apps/api/src/", "apps/api/tests/")


def _full_lane_reason(path: str) -> str | None:
    if path.endswith("conftest.py"):
        return f"{path} is shared fixture code for every lane"
    for trigger in FULL_LANE_TRIGGERS:
        if path == trigger or path.startswith(trigger):
            return f"{path} is shared lane infrastructure"
    return None


def _module_of(path: str) -> str:
    """The dotted module a repo-root-relative ``apps/api/`` path names."""
    parts = list(PurePosixPath(path).relative_to("apps/api").with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _selection_for(path: str, api_root: Path, graph: dict[str, set[str]]) -> set[str]:
    """The test modules this changed file can affect, and how they are named."""
    if not path.endswith(".py"):
        return set()
    module = _module_of(path)
    if module.startswith("src."):
        selected = tests_for_src(graph, module, api_root)
    else:
        selected = {path} if PurePosixPath(path).name.startswith("test_") else set()
        selected |= {
            _node_path(importer, api_root)
            for importer in graph.get(module, set())
            if importer.startswith("tests.")
        }
    # A conftest is collected with its directory: naming it as a target only
    # inflates the count, and pytest never runs tests from it.
    return {target for target in selected if PurePosixPath(target).name != "conftest.py"}


def map_files(
    changed: Iterable[str],
    *,
    api_root: Path = APPS_API,
    graph: dict[str, set[str]] | None = None,
) -> Plan:
    """Decide what the commit gate runs for this set of changed paths."""
    paths = sorted(set(changed))
    if not paths:
        return Skip("no file the test lanes observe")

    for path in paths:
        reason = _full_lane_reason(path)
        if reason:
            return FullLane(reason)

    code = [path for path in paths if path.startswith(CODE_PREFIXES)]
    if not code:
        return Skip("no file the test lanes observe")

    if graph is None:
        graph = build_graph(api_root)

    files: set[str] = set()
    reasons: list[tuple[str, str]] = []
    for path in code:
        selected = _selection_for(path, api_root, graph)
        if not selected:
            return FullLane(f"{path} maps to no test module")
        files |= selected
        label = _module_of(path) if path.startswith("apps/api/src/") else path
        reasons.extend((target, f"{label} -> {target}") for target in sorted(selected))

    return Selected(tuple(sorted(files)), tuple(reasons))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="testselect", description=__doc__)
    parser.add_argument("--explain", action="store_true", help="print why each module was selected")
    parser.add_argument("files", nargs="*", help="repo-root-relative changed paths")
    args = parser.parse_args(argv)

    plan = map_files(args.files, api_root=APPS_API)
    print(plan.kind)
    if isinstance(plan, Selected):
        print(f"selected {len(plan.files)} test modules")
        if args.explain:
            for target, reason in plan.reasons:
                print(f"  {target} <- {reason}")
    else:
        print(plan.reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
