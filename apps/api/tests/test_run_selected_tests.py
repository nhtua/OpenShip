"""The commit-gate launcher: index files in, one pytest command out."""

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

    code = launcher.run([], repo_root=repo, changed=["apps/api/alembic/versions/z.py"], runner=recorder, plan=plan)

    assert code == 0
    assert recorder.commands == [
        ["uv", "run", "pytest", "-q", "-m", launcher.LANE_MARKER_EXPRESSION]
    ]


def test_the_full_lane_flag_runs_the_lane_even_when_the_files_would_skip(repo: Path) -> None:
    """The pre-push gate asks for the whole lane; the expression lives in the launcher."""
    recorder = Recorder()

    code = launcher.run(["--full-lane"], repo_root=repo, changed=["docs/x.md"], runner=recorder)

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


def test_a_large_selection_is_one_command_with_no_size_cap(repo: Path, capsys: pytest.CaptureResponse[str]) -> None:
    files = tuple(f"apps/api/tests/test_{index:03d}.py" for index in range(120))
    plan = testselect.Selected(files=files, reasons=tuple((f, "src.openship.runs -> f") for f in files))
    recorder = Recorder()

    launcher.run([], repo_root=repo, changed=["apps/api/src/openship/runs/service.py"], runner=recorder, plan=plan)
    out = capsys.readouterr().out

    assert len(recorder.commands) == 1
    assert len(recorder.commands[0]) == 6 + 120
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
    )

    assert len(recorder.commands) == 1
