"""The commit gate's own wiring: which hook runs what, and where the lane lives."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
VERIFY = REPO_ROOT / "scripts" / "openship-verify"
LANE_EXPRESSION = "not postgres and not provider_live and not full_stack and not jetstream"


def hooks() -> dict[str, dict]:
    document = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return {hook["id"]: hook for repo in document["repos"] for hook in repo["hooks"]}


def test_the_api_commit_hook_calls_the_launcher() -> None:
    hook = hooks()["api-tests"]

    assert "scripts/run_selected_tests.py" in hook["entry"]
    assert hook["pass_filenames"] is False
    # The lane expression belongs to the launcher alone: a hook entry that
    # repeats it is a second definition free to drift.
    assert "not postgres" not in hook["entry"]


def test_the_frontend_hooks_are_gated_on_the_web_tree() -> None:
    for hook_id in ("frontend-typecheck", "frontend-tests"):
        hook = hooks()[hook_id]

        assert hook["files"].startswith("^(apps/web/")
        assert "always_run" not in hook

    assert "run-changed-tests.mjs" in hooks()["frontend-tests"]["entry"]


def test_the_pre_push_hook_runs_the_full_lane_script() -> None:
    pre_push = [hook for hook in hooks().values() if hook.get("stages") == ["pre-push"]]

    assert len(pre_push) == 1
    assert "scripts/openship-verify" in pre_push[0]["entry"]


def test_the_lane_expression_is_defined_once_in_the_repo() -> None:
    assert LANE_EXPRESSION not in CONFIG.read_text(encoding="utf-8")

    definitions = [
        path
        for path in [
            *(REPO_ROOT / "apps/api/scripts").glob("*.py"),
            *(REPO_ROOT / "scripts").glob("openship-verify"),
            *(REPO_ROOT / "scripts").glob("*.sh"),
        ]
        if LANE_EXPRESSION in path.read_text(encoding="utf-8")
    ]

    assert definitions == [REPO_ROOT / "apps/api/scripts" / "run_selected_tests.py"]


def test_the_verify_script_names_every_gate_it_runs() -> None:
    text = VERIFY.read_text(encoding="utf-8")

    assert "run_selected_tests.py --full-lane" in text
    assert "pnpm typecheck" in text
    assert "pnpm test:run" in text
    assert "rayloc scan" in text
    assert VERIFY.stat().st_mode & 0o111
