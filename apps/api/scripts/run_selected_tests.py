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
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import testselect

APPS_API = Path(__file__).resolve().parents[1]
REPO_ROOT = APPS_API.parents[1]

# The credential-free lane, exactly as CI runs it: no PostgreSQL, no provider,
# no synthetic full stack, no real broker.
LANE_MARKER_EXPRESSION = "not postgres and not provider_live and not full_stack and not jetstream"
PYTEST_PREFIX = ["uv", "run", "pytest", "-q", "-m", LANE_MARKER_EXPRESSION]


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


def run(
    argv: list[str],
    *,
    repo_root: Path,
    changed: Iterable[str] | None = None,
    runner: Callable[[list[str]], int] = _run_and_exit_code,
    plan: testselect.Plan | None = None,
) -> int:
    """Decide, announce, and run. Returns the lane's exit code."""
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
    parsed = args.parse_args(argv)

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
