"""Run the tests a commit can affect, chosen from the files it stages.

The commit gate asks the mapper what the staged files can reach, then runs
exactly that. When the mapper cannot see a dependency it runs the whole
credential-free lane, and when a change is invisible to the test lanes it
says so and runs nothing. Every run prints its decision, its reasons and the
command it issued.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import testselect

APPS_API = Path(__file__).resolve().parents[1]
REPO_ROOT = APPS_API.parents[1]

# The credential-free lane, exactly as CI runs it: no PostgreSQL, no provider,
# no synthetic full stack, no real broker.
LANE_MARKER_EXPRESSION = "not postgres and not provider_live and not full_stack and not jetstream"
PYTEST_PREFIX = ["uv", "run", "pytest", "-q", "-m", LANE_MARKER_EXPRESSION]

# The shard plugin the CI workflow already loads. Sharding reuses that
# partition: deterministic, one module per shard, greedy-balanced by test count.
SHARD_PLUGIN = "scripts.pytest_shard"
# Measured on this tree (2026-11-10): 851.34 s single process, 249.1 s with 4
# shards, 152.6 s with 8. Eight is the point where the lane stops being the
# bottleneck for a developer waiting on a gate; more processes mostly add memory.
MAX_AUTO_SHARDS = 8
SELECTED_AUTO_SHARDS = 4
SELECTED_SHARD_THRESHOLD = 10
NO_TESTS_EXIT_CODE = 5
SHARD_ENV = "OPENSHIP_TEST_SHARD"
_COUNTS = re.compile(r"(\d+) (passed|failed|errors?|skipped|deselected|xfailed|xpassed)")
_SUMMARY_WORDS = ("passed", "failed", "skipped", "deselected", "error", "no tests ran")


# git variables the surrounding process may have exported. They win over
# ``cwd``, so a launcher that was handed a repository has to name its git
# directory explicitly: the commit hooks export GIT_DIR, and a linked
# worktree's git directory is not the one the parent process carries.
INHERITED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


def _git_environment(repo_root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in INHERITED_GIT_VARS}
    discovered = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout.strip()
    env["GIT_DIR"] = discovered
    env["GIT_WORK_TREE"] = str(repo_root)
    env["GIT_INDEX_FILE"] = os.path.join(discovered, "index")
    return env


def changed_files_from_index(repo_root: Path) -> list[str]:
    """The paths the commit stages, repo-root-relative.

    ``-z`` keeps paths raw: no quoting, no escaping, and deletions and renames
    come back as the single paths ``--name-only`` reports.
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "-r", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=_git_environment(repo_root),
    )
    return sorted(name for name in result.stdout.split("\0") if name)


def build_command(plan: testselect.Plan, *, apps_api_prefix: str = "apps/api/") -> list[str]:
    """The lane command, narrowed to the selected modules when there are any."""
    command = list(PYTEST_PREFIX)
    if isinstance(plan, testselect.Selected):
        command.extend(path[len(apps_api_prefix) :] for path in plan.files)
    return command


def _run_and_exit_code(command: list[str]) -> int:
    return subprocess.call(command, cwd=APPS_API)


def _run_and_capture(command: list[str], env: dict[str, str]) -> tuple[int, str]:
    """One shard, with its output kept: a report needs each shard's summary line."""
    result = subprocess.run(command, cwd=APPS_API, env=env, capture_output=True, text=True, check=False)
    return result.returncode, result.stdout + result.stderr


def _shard_count(plan: testselect.Plan, requested: str, cpus: int) -> int:
    """How many processes run this plan.

    Auto shards the full lane up to eight ways, and selections of more than ten
    modules up to four ways. A number forces shards on any plan, capped at the
    modules it selected - a shard beyond that collects nothing.
    """
    if requested == "auto":
        if isinstance(plan, testselect.FullLane):
            requested = str(min(MAX_AUTO_SHARDS, max(1, cpus)))
        elif isinstance(plan, testselect.Selected) and len(plan.files) > SELECTED_SHARD_THRESHOLD:
            requested = str(min(SELECTED_AUTO_SHARDS, max(1, cpus)))
        else:
            return 1
    count = int(requested)
    if isinstance(plan, testselect.Selected):
        count = min(count, max(1, len(plan.files)))
    return count


def shard_commands(command: list[str], shards: int) -> list[tuple[list[str], dict[str, str]]]:
    """The same lane command, once per shard, each told which partition it owns."""
    commands = []
    for index in range(shards):
        env = dict(os.environ)
        env["OPENSHIP_TEST_SHARD"] = str(index)
        env["OPENSHIP_TEST_SHARDS"] = str(shards)
        # Same order as the workflow: options first, then the selected modules.
        prefix = command[: len(PYTEST_PREFIX)]
        rest = command[len(PYTEST_PREFIX) :]
        commands.append(([*prefix, "-p", SHARD_PLUGIN, *rest], env))
    return commands


def _summary_line(output: str) -> str:
    """pytest's own last summary line, which is where a shard's counts live."""
    for line in reversed(output.strip().splitlines()):
        if any(word in line for word in _SUMMARY_WORDS):
            return line.strip()
    return ""


def _report(results: list[tuple[int, str]], seconds: float) -> int:
    """One line per shard, then the summed lane. Any shard that failed fails the lane."""
    totals: Counter[str] = Counter()
    codes = []
    for index, (code, output) in enumerate(results, 1):
        codes.append(code)
        line = _summary_line(output)
        print(f"shard {index}/{len(results)} exit {code}: {line or 'no output'}")
        for value, label in _COUNTS.findall(line):
            if label == "deselected":
                # Each shard deselects the items the plugin assigned to the other
                # shards: summing that number counts the suite several times.
                continue
            totals["error" if label.startswith("error") else label] += int(value)
        if code not in (0, NO_TESTS_EXIT_CODE):
            print(output.rstrip())
    parts = [
        f"{totals[label]} {label}"
        for label in ("failed", "error", "skipped", "xfailed", "xpassed", "passed")
        if label in totals
    ]
    counts = f"{', '.join(parts)}, " if parts else "no tests ran, "
    print(f"lane total: {counts}in {seconds:.1f}s across {len(results)} shards")
    return 0 if all(code in (0, NO_TESTS_EXIT_CODE) for code in codes) else 1


#: The shard seam, derived from a caller-supplied runner: a test that fakes the
#: executor fakes it for shards too, so no real process is ever started.
def _shard_via(
    runner: Callable[[list[str]], int],
) -> Callable[[list[str], dict[str, str]], tuple[int, str]]:
    def run_shard(command: list[str], env: dict[str, str]) -> tuple[int, str]:
        return runner(command), ""

    return run_shard


def run(
    argv: list[str],
    *,
    repo_root: Path,
    changed: Iterable[str] | None = None,
    runner: Callable[[list[str]], int] | None = None,
    plan: testselect.Plan | None = None,
    shard_runner: Callable[[list[str], dict[str, str]], tuple[int, str]] | None = None,
    cpus: int | None = None,
) -> int:
    """Decide, announce, and run. Returns the lane's exit code."""
    if runner is None:
        runner = _run_and_exit_code
        if shard_runner is None:
            shard_runner = _run_and_capture
    elif shard_runner is None:
        shard_runner = _shard_via(runner)
    args = argparse.ArgumentParser(prog="run_selected_tests", description=__doc__)
    args.add_argument(
        "--explain",
        action="store_true",
        help="print the plan, why each module is in it, and the exact command - and run nothing",
    )
    args.add_argument(
        "--full-lane",
        action="store_true",
        help="run the whole credential-free lane (the pre-push gate asks for this)",
    )
    args.add_argument(
        "--shards",
        default="auto",
        help="'auto' shards the full lane or selections over 10 modules; a number forces that many on any plan",
    )
    parsed = args.parse_args(argv)
    inside_shard = os.environ.get(SHARD_ENV)
    if parsed.shards != "auto":
        try:
            forced = int(parsed.shards)
        except ValueError:
            forced = 0
        if forced < 1:
            print("--shards must be 'auto' or a positive integer")
            return 2
        if forced > 1 and inside_shard is not None:
            # Every shard runs this suite. A shard that shards again forks the
            # lane inside itself, and each nesting level runs every test again.
            print(
                "cannot shard a lane that is already running inside a shard "
                f"({SHARD_ENV}={inside_shard}); it would fork itself"
            )
            return 2

    if changed is None:
        try:
            changed = changed_files_from_index(repo_root)
        except Exception as exc:  # noqa: BLE001 - a gate that cannot read the index blocks the commit; it never falls back to running nothing.
            print(f"git failed: {exc}")
            return 2

    try:
        if plan is None:
            plan = testselect.map_files(changed, api_root=repo_root / "apps" / "api")
    except Exception as exc:  # noqa: BLE001 - an unreadable graph blocks the commit, same rule as a failed test.
        print(f"mapper failed: {exc}")
        return 2

    if parsed.full_lane:
        plan = testselect.FullLane("--full-lane: the whole credential-free lane")

    print(plan.kind)
    if isinstance(plan, testselect.Skip):
        print(f"skip: {plan.reason}")
        return 0
    if isinstance(plan, testselect.Selected):
        print(f"selected {len(plan.files)} test modules")
        if parsed.explain:
            for target, reason in plan.reasons:
                print(f"  {target} <- {reason}")
    else:
        print(plan.reason)

    command = build_command(plan)
    print(" ".join(command))
    shards = _shard_count(plan, parsed.shards, cpus if cpus is not None else (os.cpu_count() or 1))
    if inside_shard is not None:
        # Auto-shard is a top-level decision only: a lane already running as a
        # shard runs one process, never a fan-out of its own.
        shards = 1
    if shards > 1:
        print(f"lane shards: {shards}")
        print(" ".join([*command, "-p", SHARD_PLUGIN]))
        if parsed.explain:
            # --explain is the inspection mode: the same decision, announced with
            # its reasons and its exact command - per shard - and nothing executed.
            for index in range(shards):
                print(f"  shard {index + 1}/{shards}: OPENSHIP_TEST_SHARD={index} OPENSHIP_TEST_SHARDS={shards}")
            return 0
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=shards) as pool:
            results = list(pool.map(lambda pair: shard_runner(*pair), shard_commands(command, shards)))
        return _report(results, time.monotonic() - started)
    if parsed.explain:
        # --explain is the inspection mode: the same decision, announced with its
        # reasons and its exact command, and nothing executed. The commit hook and
        # the pre-push gate ask for the lane without this flag.
        return 0
    return runner(command)


def main(argv: list[str]) -> int:
    return run(argv, repo_root=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
