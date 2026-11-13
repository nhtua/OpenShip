"""The commit-gate launcher: index files in, credential-free test commands out."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

APPS_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APPS_API / "scripts"))

import run_selected_tests as launcher  # repo precedent for script-directory modules: tests/test_sandbox_image_commands.py
import testselect


@pytest.fixture(autouse=True)
def _not_inside_a_shard(monkeypatch: pytest.MonkeyPatch) -> None:
    """This module pins the top-level decision; a lane's own shard env must not leak in.

    The hook runs this suite inside a shard, so OPENSHIP_TEST_SHARD is set in the
    process environment. A launcher invoked from inside a shard must not fan out
    again; the two guard tests below set the variables back on purpose.
    """
    monkeypatch.delenv("OPENSHIP_TEST_SHARD", raising=False)
    monkeypatch.delenv("OPENSHIP_TEST_SHARDS", raising=False)


class Recorder:
    """The runner seam: records each command the launcher issues."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> int:
        self.commands.append(command)
        return 0


# A git call must talk to the repository it is pointed at, never to one its
# process inherited. The commit hooks export GIT_DIR and the pre-commit
# install exports core.hooksPath, so a fixture that means to build a throwaway
# repository has to scrub both: an inherited GIT_DIR silently redirects
# ``git init`` into the repository running the tests, and an inherited
# hooksPath makes a throwaway commit run the real hooks.
INHERITED_GIT_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_CONFIG",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
    "GIT_NAMESPACE",
    "GIT_TEMPLATE_DIR",
)


def clean_git_env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in INHERITED_GIT_VARS}
    env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=Tester",
            "-c",
            f"core.hooksPath={repo / 'no-hooks'}",
            "-c",
            "init.templateDir=",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=clean_git_env(repo),
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with a committed base tree, hooks never involved."""
    root = tmp_path / "repo"
    (root / "apps/api/src/openship").mkdir(parents=True)
    (root / "apps/api/tests").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "apps/api/src/openship/a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "apps/api/tests/test_a.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    (root / "docs/x.md").write_text("x\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "base")
    return root


def test_selected_plan_runs_the_mapped_modules_with_the_lane_expression(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    recorder = Recorder()
    plan = testselect.Selected(
        files=("apps/api/tests/test_plan.py", "apps/api/tests/test_runs_service.py"),
        reasons=(
            ("apps/api/tests/test_plan.py", "src.openship.runs.service -> x"),
            ("apps/api/tests/test_runs_service.py", "src.openship.runs.service -> y"),
        ),
    )

    code = launcher.run([], repo_root=repo, changed=["apps/api/src/openship/runs/service.py"], runner=recorder, plan=plan)
    out = capsys.readouterr().out

    assert code == 0
    assert recorder.commands == [
        [
            "uv",
            "run",
            "pytest",
            "-q",
            "-m",
            launcher.LANE_MARKER_EXPRESSION,
            "tests/test_plan.py",
            "tests/test_runs_service.py",
        ]
    ]
    assert "selected" in out


def test_full_lane_plan_runs_the_lane_without_file_arguments(repo: Path) -> None:
    recorder = Recorder()
    plan = testselect.FullLane("apps/api/alembic/versions/z.py maps to no test module")

    # cpus=1: this pins the single-process lane path, which --shards 1 selects.
    code = launcher.run(
        [], repo_root=repo, changed=["apps/api/alembic/versions/z.py"], runner=recorder, plan=plan, cpus=1
    )

    assert code == 0
    assert recorder.commands == [
        ["uv", "run", "pytest", "-q", "-m", launcher.LANE_MARKER_EXPRESSION]
    ]


def test_the_full_lane_flag_runs_the_lane_even_when_the_files_would_skip(repo: Path) -> None:
    """The pre-push gate asks for the whole lane; the expression lives in the launcher."""
    recorder = Recorder()

    # cpus=1: pins the single-process lane path, which --shards 1 selects.
    code = launcher.run(["--full-lane"], repo_root=repo, changed=["docs/x.md"], runner=recorder, cpus=1)

    assert code == 0
    assert recorder.commands == [
        ["uv", "run", "pytest", "-q", "-m", launcher.LANE_MARKER_EXPRESSION]
    ]


def test_skip_plan_runs_nothing_and_prints_its_reason(repo: Path, capsys: pytest.CaptureResponse[str]) -> None:
    recorder = Recorder()
    plan = testselect.Skip("no file the test lanes observe")

    code = launcher.run([], repo_root=repo, changed=["docs/x.md"], runner=recorder, plan=plan)
    out = capsys.readouterr().out

    assert code == 0
    assert recorder.commands == []
    assert "no file the test lanes observe" in out


def test_a_large_selection_keeps_all_modules_when_auto_sharded(repo: Path, capsys: pytest.CaptureResponse[str]) -> None:
    files = tuple(f"apps/api/tests/test_{index:03d}.py" for index in range(120))
    plan = testselect.Selected(files=files, reasons=tuple((f, "src.openship.runs -> f") for f in files))
    shards = ShardRecorder({index: (0, "30 passed in 5.00s") for index in range(4)})

    launcher.run(
        [], repo_root=repo, changed=["apps/api/src/openship/runs/service.py"],
        runner=Recorder(), shard_runner=shards, plan=plan, cpus=8,
    )
    out = capsys.readouterr().out

    assert len(shards.calls) == 4
    assert all(command[8:] == [f"tests/test_{index:03d}.py" for index in range(120)] for command, _ in shards.calls)
    assert "selected 120 test modules" in out


def test_a_mapper_failure_is_reported_and_fails_the_gate(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureResponse[str]) -> None:
    def explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("the graph is unreadable")

    monkeypatch.setattr(launcher.testselect, "map_files", explode)

    code = launcher.run([], repo_root=repo, changed=["apps/api/src/openship/runs/service.py"], runner=Recorder())
    out = capsys.readouterr().out

    assert code != 0
    assert "mapper failed" in out


def test_index_reports_staged_additions_and_modifications(repo: Path) -> None:
    (repo / "apps/api/tests/test_new.py").write_text("def test_new():\n    assert True\n", encoding="utf-8")
    (repo / "apps/api/tests/test_a.py").write_text("def test_a():\n    assert True\n# touched\n", encoding="utf-8")
    git(repo, "add", "-A")

    assert launcher.changed_files_from_index(repo) == [
        "apps/api/tests/test_a.py",
        "apps/api/tests/test_new.py",
    ]


def test_index_reports_a_deletion_by_its_old_path_and_a_rename_by_its_new_path(repo: Path) -> None:
    (repo / "apps/api/tests/test_gone.py").write_text("def test_gone():\n    assert True\n", encoding="utf-8")
    (repo / "apps/api/tests/test_before.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "more")
    git(repo, "rm", "-q", "apps/api/tests/test_gone.py")
    git(repo, "mv", "apps/api/tests/test_before.py", "apps/api/tests/test_after.py")

    assert launcher.changed_files_from_index(repo) == [
        "apps/api/tests/test_after.py",
        "apps/api/tests/test_gone.py",
    ]


def test_index_never_quotes_a_non_ascii_path(repo: Path) -> None:
    name = "apps/api/tests/test_ünïcode.py"
    (repo / name).write_text("def test_u():\n    assert True\n", encoding="utf-8")
    git(repo, "add", "-A")

    assert launcher.changed_files_from_index(repo) == [name]


def test_an_unknown_head_fails_the_gate(tmp_path: Path, capsys: pytest.CaptureResponse[str]) -> None:
    root = tmp_path / "empty"
    (root / "apps/api/src").mkdir(parents=True)
    (root / "apps/api/src/x.py").write_text("X = 1\n", encoding="utf-8")
    git(root, "init", "-q")

    code = launcher.run([], repo_root=root, runner=Recorder())
    out = capsys.readouterr().out

    assert code != 0
    assert "git failed" in out


def test_the_real_index_maps_to_the_lane_command(tmp_path: Path) -> None:
    root = tmp_path / "wide"
    api = root / "apps/api"
    (api / "src/openship/runs").mkdir(parents=True)
    (api / "tests").mkdir(parents=True)
    (api / "src/openship/runs/service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (api / "tests/test_service.py").write_text("from src.openship.runs.service import VALUE\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "base")
    (api / "src/openship/runs/service.py").write_text("VALUE = 2\n", encoding="utf-8")
    git(root, "add", "-A")
    recorder = Recorder()

    code = launcher.run([], repo_root=root, runner=recorder)

    assert code == 0
    assert recorder.commands[-1][-1] == "tests/test_service.py"


def build_repo(root: Path, source: str) -> Path:
    """A repository with a committed base tree and one staged source edit."""
    api = root / "apps/api"
    (api / "src/openship").mkdir(parents=True)
    (api / "tests").mkdir(parents=True)
    (api / f"src/openship/{source}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (api / "tests/test_service.py").write_text("from src.openship.service import VALUE\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "base")
    (api / f"src/openship/{source}.py").write_text("VALUE = 2\n", encoding="utf-8")
    git(root, "add", "-A")
    return root


def test_the_launcher_reads_the_index_of_the_repository_it_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exported GIT_DIR must not move the launcher to another repository.

    The commit hooks export GIT_DIR, and a linked worktree has a git directory
    of its own. Naming the repository has to mean the launcher reads that
    repository's index.
    """
    build_repo(tmp_path / "outer", "outer_only")
    inner = build_repo(tmp_path / "inner", "inner_only")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "outer/.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "outer"))

    assert launcher.changed_files_from_index(inner) == ["apps/api/src/openship/inner_only.py"]


def test_the_fixture_commits_land_in_their_own_repository(tmp_path: Path) -> None:
    """The fixture's own git calls are hermetic, hooks and GIT_DIR included.

    A test that builds a repository must not commit into the repository that
    runs it: an inherited GIT_DIR made that happen once already.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    (outer / "keep.txt").write_text("one\n", encoding="utf-8")
    git(outer, "add", "-A")
    git(outer, "commit", "-q", "--no-verify", "-m", "one")
    os.environ["GIT_DIR"] = str(outer / ".git")
    os.environ["GIT_WORK_TREE"] = str(outer)
    try:
        build_repo(tmp_path / "inner", "inner_only")
    finally:
        del os.environ["GIT_DIR"]
        del os.environ["GIT_WORK_TREE"]

    assert git(outer, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(outer, "status", "--porcelain").strip() == ""


def test_explain_announces_the_plan_and_runs_nothing(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    """`--explain` is the inspection mode: the decision, its reasons, zero runs."""
    recorder = Recorder()
    plan = testselect.Selected(
        files=("apps/api/tests/test_plan.py", "apps/api/tests/test_runs_service.py"),
        reasons=(
            ("apps/api/tests/test_plan.py", "apps/api/src/openship/a.py -> tests/test_plan.py"),
            (
                "apps/api/tests/test_runs_service.py",
                "apps/api/src/openship/a.py -> tests/test_runs_service.py",
            ),
        ),
    )

    code = launcher.run(
        ["--explain"],
        repo_root=repo,
        changed=["apps/api/src/openship/a.py"],
        runner=recorder,
        plan=plan,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert recorder.commands == []
    assert "selected 2 test modules" in out
    assert "tests/test_plan.py <- apps/api/src/openship/a.py -> tests/test_plan.py" in out
    lane_command = [
        "uv",
        "run",
        "pytest",
        "-q",
        "-m",
        launcher.LANE_MARKER_EXPRESSION,
        "tests/test_plan.py",
        "tests/test_runs_service.py",
    ]
    assert " ".join(lane_command) in out


def test_explain_overrides_the_full_lane_flag(repo: Path) -> None:
    """Asking to see the plan never starts the lane the gate would run."""
    recorder = Recorder()

    code = launcher.run(
        ["--full-lane", "--explain"], repo_root=repo, changed=["docs/x.md"], runner=recorder
    )

    assert code == 0
    assert recorder.commands == []


def test_explain_on_a_skip_plan_runs_nothing(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    recorder = Recorder()

    code = launcher.run(["--explain"], repo_root=repo, changed=["docs/x.md"], runner=recorder)
    out = capsys.readouterr().out

    assert code == 0
    assert recorder.commands == []
    assert "skip" in out


def test_the_lane_runs_when_explain_is_absent(repo: Path) -> None:
    """The gate's own path is unchanged: decide, announce, run."""
    recorder = Recorder()

    launcher.run(
        [],
        repo_root=repo,
        changed=["apps/api/alembic/versions/z.py"],
        runner=recorder,
        plan=testselect.FullLane("apps/api/alembic/versions/z.py maps to no test module"),
        cpus=1,
    )

    assert len(recorder.commands) == 1


class ShardRecorder:
    """The shard seam: records each shard's command and environment, returns canned results.

    Results are keyed by the shard index the launcher asks the environment to
    run, so the fake answers identically however the shards are scheduled.
    """

    def __init__(self, results: dict[int, tuple[int, str]]) -> None:
        self.results = dict(results)
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, command: list[str], env: dict[str, str]) -> tuple[int, str]:
        self.calls.append((command, env))
        return self.results[int(env["OPENSHIP_TEST_SHARD"])]


def _calls_in_shard_order(
    shards: ShardRecorder,
) -> list[tuple[list[str], dict[str, str]]]:
    """Shards run concurrently: their *calls* arrive in any order, their report does not."""
    return sorted(shards.calls, key=lambda pair: int(pair[1]["OPENSHIP_TEST_SHARD"]))


def _shard_prefix() -> list[str]:
    return [*launcher.PYTEST_PREFIX, "-p", launcher.SHARD_PLUGIN]


def _lane_shard_env(index: int, total: int) -> dict[str, str]:
    return {
        "OPENSHIP_TEST_SHARD": str(index),
        "OPENSHIP_TEST_SHARDS": str(total),
    }


@pytest.mark.parametrize("count, cpus, expected_shards", [
    (10, 8, 1),
    (11, 8, 4),
    (11, 2, 2),
])
def test_auto_shards_selected_plans_only_above_ten_modules(
    repo: Path, count: int, cpus: int, expected_shards: int
) -> None:
    files = tuple(f"apps/api/tests/test_{index:02d}.py" for index in range(count))
    plan = testselect.Selected(files=files, reasons=())
    single = Recorder()
    shards = ShardRecorder({index: (0, "3 passed in 1.00s") for index in range(expected_shards)})

    code = launcher.run(
        [], repo_root=repo, changed=["apps/api/src/openship/runs/service.py"],
        runner=single, shard_runner=shards, plan=plan, cpus=cpus,
    )

    assert code == 0
    if expected_shards == 1:
        assert len(single.commands) == 1
        assert shards.calls == []
    else:
        assert single.commands == []
        assert len(shards.calls) == expected_shards
        assert {env["OPENSHIP_TEST_SHARDS"] for _, env in shards.calls} == {str(expected_shards)}


def test_the_full_lane_splits_into_parallel_shards(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    """CI already runs the lane as shards; a local full-lane run is one process today.

    The launcher drives the same shard plugin the workflow uses, in parallel, so
    the pre-commit and pre-push lane stops being one 14-minute process.
    """
    shards = ShardRecorder({index: (0, "500 passed in 210.00s") for index in range(8)})
    single = Recorder()

    code = launcher.run(
        ["--full-lane"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=single,
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )

    assert code == 0
    assert single.commands == []
    calls = _calls_in_shard_order(shards)
    assert [command for command, _ in calls] == [_shard_prefix()] * 8
    assert [env["OPENSHIP_TEST_SHARD"] for _, env in calls] == [str(i) for i in range(8)]
    assert {env["OPENSHIP_TEST_SHARDS"] for _, env in calls} == {"8"}


def test_auto_sharding_leaves_a_narrowed_plan_single_process(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    """A few dozen modules are not worth four process starts: the ruling is auto-shard the full lane only."""
    shards = ShardRecorder({})
    single = Recorder()
    plan = testselect.Selected(
        files=("apps/api/tests/test_plan.py",),
        reasons=(("apps/api/tests/test_plan.py", "src.openship.runs.service -> x"),),
    )

    code = launcher.run(
        [],
        repo_root=repo,
        changed=["apps/api/src/openship/runs/service.py"],
        runner=single,
        plan=plan,
        shard_runner=shards,
        cpus=8,
    )

    assert code == 0
    assert shards.calls == []
    assert len(single.commands) == 1
    assert launcher.SHARD_PLUGIN not in single.commands[0]


def test_the_shards_flag_forces_shards_on_a_narrowed_plan(repo: Path) -> None:
    shards = ShardRecorder({index: (0, "10 passed in 5.00s") for index in range(3)})
    single = Recorder()
    plan = testselect.Selected(
        files=(
            "apps/api/tests/test_plan.py",
            "apps/api/tests/test_runs_service.py",
            "apps/api/tests/test_tasks.py",
        ),
        reasons=(
            ("apps/api/tests/test_plan.py", "src.openship.runs.service -> x"),
            ("apps/api/tests/test_runs_service.py", "src.openship.runs.service -> y"),
            ("apps/api/tests/test_tasks.py", "src.openship.runs.service -> z"),
        ),
    )

    code = launcher.run(
        ["--shards", "3"],
        repo_root=repo,
        changed=["apps/api/src/openship/runs/service.py"],
        runner=single,
        plan=plan,
        shard_runner=shards,
        cpus=8,
    )

    assert code == 0
    assert single.commands == []
    calls = _calls_in_shard_order(shards)
    assert [command for command, _ in calls] == [
        [
            *_shard_prefix(),
            "tests/test_plan.py",
            "tests/test_runs_service.py",
            "tests/test_tasks.py",
        ]
    ] * 3
    assert [env["OPENSHIP_TEST_SHARD"] for _, env in calls] == ["0", "1", "2"]


def test_auto_shards_never_exceed_the_processors_available(repo: Path) -> None:
    shards = ShardRecorder({index: (0, "700 passed in 250.00s") for index in range(2)})

    launcher.run(
        ["--full-lane"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=Recorder(),
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=2,
    )

    assert len(shards.calls) == 2
    assert {env["OPENSHIP_TEST_SHARDS"] for _, env in shards.calls} == {"2"}


def test_the_shard_count_stops_at_the_modules_selected(repo: Path) -> None:
    shards = ShardRecorder({index: (0, "8 passed in 4.00s") for index in range(2)})
    plan = testselect.Selected(
        files=("apps/api/tests/test_plan.py", "apps/api/tests/test_runs_service.py"),
        reasons=(
            ("apps/api/tests/test_plan.py", "src.openship.runs.service -> x"),
            ("apps/api/tests/test_runs_service.py", "src.openship.runs.service -> y"),
        ),
    )

    launcher.run(
        ["--shards", "9"],
        repo_root=repo,
        changed=["apps/api/src/openship/runs/service.py"],
        runner=Recorder(),
        plan=plan,
        shard_runner=shards,
        cpus=8,
    )

    assert len(shards.calls) == 2


def test_any_failing_shard_fails_the_lane(repo: Path) -> None:
    shards = ShardRecorder(
        {
            0: (0, "500 passed in 210.00s"),
            1: (1, "498 passed, 2 failed in 214.00s\nFAILED tests/test_x.py::test_y\n"),
        }
    )

    code = launcher.run(
        ["--full-lane", "--shards", "2"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=Recorder(),
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )

    assert code == 1


def test_a_shard_that_collects_nothing_is_not_a_failure(repo: Path) -> None:
    """A shard capped above the module count collects nothing: pytest exits 5, the lane did not fail."""
    shards = ShardRecorder(
        {
            0: (0, "100 passed in 40.00s"),
            1: (0, "99 passed in 41.00s"),
            2: (5, "no tests ran in 2.00s"),
        }
    )

    code = launcher.run(
        ["--full-lane", "--shards", "3"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=Recorder(),
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )

    assert code == 0


def test_the_shard_report_adds_the_shards_up(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    shards = ShardRecorder(
        {index: (0, "250 passed, 40 deselected in 211.10s") for index in range(8)}
    )

    code = launcher.run(
        ["--full-lane"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=Recorder(),
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "2000 passed" in out
    assert "8 shards" in out
    total = next(line for line in out.splitlines() if line.startswith("lane total"))
    assert "deselected" not in total


def test_a_failing_shard_prints_what_it_reported(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    shards = ShardRecorder(
        {
            0: (0, "500 passed in 210.00s"),
            1: (1, "498 passed, 2 failed in 214.00s\nFAILED tests/test_x.py::test_y\nassert 1 == 2\n"),
        }
    )

    launcher.run(
        ["--full-lane", "--shards", "2"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=Recorder(),
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )
    out = capsys.readouterr().out

    assert "assert 1 == 2" in out


def test_explain_announces_the_shards_without_running_anything(
    repo: Path, capsys: pytest.CaptureResponse[str]
) -> None:
    shards = ShardRecorder({})
    single = Recorder()

    code = launcher.run(
        ["--full-lane", "--explain"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=single,
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert single.commands == []
    assert shards.calls == []
    assert "lane shards: 8" in out
    assert launcher.SHARD_PLUGIN in out


def test_shards_below_one_is_refused(repo: Path, capsys: pytest.CaptureResponse[str]) -> None:
    code = launcher.run(
        ["--shards", "0"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=Recorder(),
        plan=testselect.FullLane("maps to no test module"),
        cpus=8,
    )
    out = capsys.readouterr().out

    assert code == 2
    assert "shards" in out


def test_two_real_shards_partition_the_lane() -> None:
    """The plugin the workflow already loads, driven locally: one shard runs the module, the other collects nothing."""
    plan = testselect.Selected(
        files=(
            "apps/api/tests/test_pytest_shard.py",
            "apps/api/tests/test_precommit_wiring.py",
        ),
        reasons=(
            ("apps/api/tests/test_pytest_shard.py", "scripts/pytest_shard.py -> x"),
            ("apps/api/tests/test_precommit_wiring.py", "scripts/openship-verify -> y"),
        ),
    )

    code = launcher.run(
        ["--shards", "2"],
        repo_root=APPS_API.parents[1],
        changed=[],
        plan=plan,
    )

    assert code == 0


def test_a_lane_inside_a_shard_does_not_fan_out(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launcher running inside a shard must not start shards of its own.

    Every shard runs this suite, so an unguarded auto-shard forks itself: a real
    full lane inside a real full lane, each nesting level running every test
    again. Measured as a 35-deep chain before the guard, 2026-11-10.
    """
    monkeypatch.setenv("OPENSHIP_TEST_SHARD", "1")
    monkeypatch.setenv("OPENSHIP_TEST_SHARDS", "4")
    shards = ShardRecorder({})
    single = Recorder()

    code = launcher.run(
        ["--full-lane"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=single,
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )

    assert code == 0
    assert shards.calls == []
    assert single.commands == [launcher.PYTEST_PREFIX]


def test_a_sharded_request_from_inside_a_shard_is_refused(
    repo: Path, capsys: pytest.CaptureResponse[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shard that asks for shards is a mistake; refuse it and say why."""
    monkeypatch.setenv("OPENSHIP_TEST_SHARD", "1")
    monkeypatch.setenv("OPENSHIP_TEST_SHARDS", "4")
    shards = ShardRecorder({})
    single = Recorder()

    code = launcher.run(
        ["--full-lane", "--shards", "4"],
        repo_root=repo,
        changed=["docs/x.md"],
        runner=single,
        plan=testselect.FullLane("maps to no test module"),
        shard_runner=shards,
        cpus=8,
    )
    out = capsys.readouterr().out

    assert code == 2
    assert "already running inside a shard" in out
    assert shards.calls == []
    assert single.commands == []
