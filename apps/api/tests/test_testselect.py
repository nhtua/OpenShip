"""The import graph the changed-file test selector reads from a commit's files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APPS_API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APPS_API / "scripts"))

import testselect  # repo precedent for script-directory modules: tests/test_sandbox_image_commands.py

TREE = {
    "src/openship/runs/service.py": "DEFAULT_CITY = 'London'\n",
    "src/openship/runs/tasks/plan.py": "from src.openship.runs import service\n\nCITY = service.DEFAULT_CITY\n",
    "tests/test_runs_service.py": "from src.openship.runs.service import DEFAULT_CITY\n",
    "tests/test_plan.py": "from src.openship.runs.tasks.plan import CITY\nfrom tests import support\n",
    "tests/conftest.py": "import pytest\n\nfrom src.openship.runs import service\n\n\n@pytest.fixture\ndef city() -> str:\n    return service.DEFAULT_CITY\n",
    "tests/support.py": "TAG = 'fixture-support'\n",
    "tests/live/test_live.py": (
        "import importlib\n\n"
        "importlib.import_module('src.openship.runs.service')\n"
        "importlib.import_module('src.openship.runs.dynamic')\n"
    ),
    "tests/test_unparseable.py": "def (:\n",
}


@pytest.fixture
def api_root(tmp_path: Path) -> Path:
    """A minimal apps/api tree whose plan path spells the repo's own layout."""
    root = tmp_path / "apps" / "api"
    for relative, body in TREE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def test_build_graph_records_importers_of_a_from_package_import(api_root: Path) -> None:
    graph = testselect.build_graph(api_root)

    assert graph["src.openship.runs.service"] == {
        "src.openship.runs.tasks.plan",
        "tests.test_runs_service",
        "tests.conftest",
    }


def test_closure_selects_tests_transitively_through_src(api_root: Path) -> None:
    graph = testselect.build_graph(api_root)

    assert testselect.tests_for_src(graph, "src.openship.runs.service", api_root) == {
        "apps/api/tests/test_runs_service.py",
        "apps/api/tests/test_plan.py",
        "apps/api/tests/conftest.py",
    }


def test_closure_excludes_a_module_reached_only_by_an_import_string(api_root: Path) -> None:
    graph = testselect.build_graph(api_root)

    selected = testselect.tests_for_src(graph, "src.openship.runs.service", api_root)

    assert "apps/api/tests/live/test_live.py" not in selected


def test_build_graph_skips_a_file_that_does_not_parse(api_root: Path) -> None:
    graph = testselect.build_graph(api_root)

    assert "tests.test_unparseable" not in graph
    assert all("tests.test_unparseable" not in importers for importers in graph.values())


def test_plan_types_carry_their_lane_kind() -> None:
    assert testselect.FullLane("unmapped code").kind == "full-lane"
    assert testselect.Selected((), ()).kind == "selected"
    assert testselect.Skip("docs only").kind == "skip"


def select(changed: list[str], api_root: Path) -> object:
    return testselect.map_files(changed, api_root=api_root)


def test_source_change_selects_the_tests_that_import_it(api_root: Path) -> None:
    plan = select(["apps/api/src/openship/runs/service.py"], api_root)

    assert plan.kind == "selected"
    assert set(plan.files) == {
        "apps/api/tests/test_runs_service.py",
        "apps/api/tests/test_plan.py",
    }
    assert all("src.openship.runs.service" in reason for _, reason in plan.reasons)


def test_changed_test_module_selects_itself(api_root: Path) -> None:
    plan = select(["apps/api/tests/test_plan.py"], api_root)

    assert plan.kind == "selected"
    assert "apps/api/tests/test_plan.py" in plan.files


def test_selection_leaves_conftest_files_to_pytest_collection(api_root: Path) -> None:
    """A conftest is collected with its directory, so naming it as a target is noise."""
    plan = select(["apps/api/src/openship/runs/service.py"], api_root)

    assert "apps/api/tests/conftest.py" not in plan.files
    assert "apps/api/tests/test_runs_service.py" in plan.files


def test_changed_test_support_module_selects_the_tests_importing_it(api_root: Path) -> None:
    plan = select(["apps/api/tests/support.py"], api_root)

    assert plan.kind == "selected"
    assert plan.files == ("apps/api/tests/test_plan.py",)


@pytest.mark.parametrize(
    "path",
    [
        "apps/api/tests/conftest.py",
        "apps/api/pyproject.toml",
        "apps/api/uv.lock",
        ".pre-commit-config.yaml",
        "apps/api/scripts/x.py",
        "scripts/y.py",
        "apps/api/alembic/versions/z.py",
        "apps/api/tests/fixtures/a.json",
    ],
)
def test_lane_infrastructure_changes_force_the_full_lane(api_root: Path, path: str) -> None:
    plan = select([path], api_root)

    assert plan.kind == "full-lane"
    assert path in plan.reason


@pytest.mark.parametrize(
    "path",
    ["docs/development.md", "deploy/x.yaml", "README.md", "poc/agent/main.py"],
)
def test_document_and_deploy_changes_skip_the_test_lanes(api_root: Path, path: str) -> None:
    assert select([path], api_root).kind == "skip"


def test_a_docs_and_code_commit_selects_the_code(api_root: Path) -> None:
    plan = select(["docs/development.md", "apps/api/src/openship/runs/service.py"], api_root)

    assert plan.kind == "selected"
    assert set(plan.files) == {
        "apps/api/tests/test_runs_service.py",
        "apps/api/tests/test_plan.py",
    }


def test_unmapped_source_file_forces_the_full_lane(api_root: Path) -> None:
    plan = select(["apps/api/src/openship/newthing.py"], api_root)

    assert plan.kind == "full-lane"
    assert "apps/api/src/openship/newthing.py" in plan.reason


def test_a_module_reached_only_by_an_import_string_forces_the_full_lane(api_root: Path) -> None:
    plan = select(["apps/api/src/openship/runs/dynamic.py"], api_root)

    assert plan.kind == "full-lane"


def test_explain_cli_prints_the_plan_and_its_reasons(
    api_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureResponse[str]
) -> None:
    monkeypatch.setattr(testselect, "APPS_API", api_root)

    code = testselect.main(["--explain", "apps/api/src/openship/runs/service.py"])
    out = capsys.readouterr().out

    assert code == 0
    assert "selected" in out
    assert "apps/api/tests/test_plan.py" in out
    assert "src.openship.runs.service" in out


RELATIVE_TREE = {
    "src/openship/tools/inner.py": "VALUE = 1\n",
    "src/openship/tools/outer.py": "from .inner import VALUE\n\nX = VALUE\n",
    "src/openship/deep/a/b/c.py": "from ....tools.inner import VALUE\n\nY = VALUE\n",
    "src/openship/tools/bare.py": "from . import inner\n\nZ = inner.VALUE\n",
    "tests/test_outer.py": "from src.openship.tools.outer import X\n",
    "tests/test_c.py": "from src.openship.deep.a.b.c import Y\n",
}


@pytest.fixture
def relative_root(tmp_path: Path) -> Path:
    """A tree whose src files import each other the way this one really does."""
    root = tmp_path / "apps" / "api"
    for relative, body in {**TREE, **RELATIVE_TREE}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def test_relative_imports_are_edges_like_any_other_import(relative_root: Path) -> None:
    """`from .x import y` and `from ...x import y` name a module as surely as an absolute import.

    The src package is written almost entirely in relative imports, so a graph
    that skipped them would map most src modules to nothing and turn the narrowed
    gate into the full lane.
    """
    graph = testselect.build_graph(relative_root)

    assert graph["src.openship.tools.inner"] >= {
        "src.openship.tools.outer",
        "src.openship.tools.bare",
        "src.openship.deep.a.b.c",
    }


def test_closure_reaches_the_tests_through_a_relative_chain(relative_root: Path) -> None:
    graph = testselect.build_graph(relative_root)

    assert testselect.tests_for_src(graph, "src.openship.tools.inner", relative_root) == {
        "apps/api/tests/test_outer.py",
        "apps/api/tests/test_c.py",
    }


def test_a_module_imported_only_by_a_relative_import_is_not_a_full_lane() -> None:
    """The real tree's case: `runs/task_graph.py` is imported by `from .task_graph import …`.

    Under a graph that read only absolute imports this module mapped to no test
    module, so editing it paid for the whole lane. Its importer chain ends in real
    tests, and the selection has to follow it.
    """
    plan = testselect.map_files(
        ["apps/api/src/openship/runs/task_graph.py"], api_root=APPS_API
    )

    assert isinstance(plan, testselect.Selected)
    assert "apps/api/tests/test_task_graph.py" in plan.files
