"""What the inference stages tell a model to answer, and what they accept.

The planning stages are a contract with a model: the prompt is the only text a
real provider ever sees, and the validators are what the run accepts. When the
two disagree, a real model is refused on every attempt while a lane that answers
in the validators' own shape stays green. The dev-stack QA of an agentic task hit
exactly that (run ``e54c2653-122a-4868-be96-1331899a96b1``): every real envelope
was refused for "carries no assessment object", and every plan for its hyphenated
step ids, and the run completed with an empty assistant message. These cases pin
the two sides of the contract against each other.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef
from src.openship.runs.tasks.contracts import (
    STEP_ID_GRAMMAR,
    STEP_ID_PATTERN,
    TaskProgress,
    TaskStep,
)
from src.openship.runs.tasks.inference import _assessment_of, _plan_of, _stage_prompt

PINNED_CAPABILITIES = ("download_file", "python_execute")


def _execution():
    """The pinned references a stage may accept: ids, profiles, nothing else."""
    refs = tuple(
        CapabilityRef(
            id=manifest.id,
            version=manifest.version,
            manifest_digest=manifest.manifest_digest,
        )
        for manifest in list_capability_manifests()
        if manifest.id in PINNED_CAPABILITIES
    )
    return SimpleNamespace(
        allowed_capabilities=refs,
        profile_catalog=SimpleNamespace(profiles={"standard": {}, "logs-data": {}}),
        task_context=SimpleNamespace(original_intent="Chart the observations and average them."),
    )


def _assessment_envelope() -> dict:
    return {
        "classification": "task",
        "explanation": "The observations can be charted and averaged as supplied.",
        "evidence_refs": [],
        "missing_information": [],
        "failure": "none",
        "step_id": None,
        "unknowns": [],
        "risks": [],
        "recommended_recovery_action": None,
    }


def _plan_steps(step_id: str = "find-data-source") -> list[dict]:
    return [
        {
            "id": step_id,
            "intent": "Download the supplied observations",
            "depends_on": [],
            "capability": "download_file",
            "profile_id": None,
            "expected_outputs": ["observations.csv"],
            "completion_checks": ["observations.csv is staged"],
        },
        {
            "id": "chart_and_average",
            "intent": "Chart the observations and report their averages",
            "depends_on": [step_id],
            "capability": "python_execute",
            "profile_id": "standard",
            "expected_outputs": ["chart.png"],
            "completion_checks": ["the chart exists"],
        },
    ]


def _plan_envelope(step_id: str = "find-data-source") -> dict:
    return {
        "intent": "Chart the supplied observations and report their averages.",
        "constraints": [],
        "steps": _plan_steps(step_id),
    }


def _step_fields(step_id: str) -> dict:
    """The same step as the model writes it, in the types the contract stores."""
    draft = _plan_steps(step_id)[0]
    return {
        "id": step_id,
        "intent": draft["intent"],
        "depends_on": (),
        "capability": None,
        "profile_id": None,
        "expected_outputs": tuple(draft["expected_outputs"]),
        "completion_checks": tuple(draft["completion_checks"]),
    }


def test_the_assessment_prompt_shows_the_nested_assessment_object():
    """The stage accepts ``{"assessment": {...}}``; that is the shape it must show."""
    prompt = _stage_prompt("assessment", _execution(), ())

    assert '"assessment":' in prompt
    assert '"plan":' not in prompt


def test_the_plan_prompt_shows_both_nested_objects():
    prompt = _stage_prompt("assessment_and_plan", _execution(), ())

    assert '"assessment":' in prompt
    assert '"plan":' in prompt


def test_the_plan_prompt_states_the_step_identity_grammar():
    """A model may not have to guess an identity the server validates by pattern."""
    prompt = _stage_prompt("assessment_and_plan", _execution(), ())

    assert STEP_ID_GRAMMAR in prompt


def test_the_stated_grammar_is_the_pattern_the_contract_enforces():
    """The words in the prompt and the regex behind the field describe one grammar."""
    for step_id in ("a", "fetch", "chart_2", "find-data-source", "download-observations"):
        assert TaskStep(**_step_fields(step_id)).id == step_id

    assert STEP_ID_PATTERN.startswith("^")


def test_an_envelope_answered_per_the_prompt_validates():
    """The shape a model is told to answer with has to be the shape that is accepted."""
    envelope = {"assessment": _assessment_envelope(), "plan": _plan_envelope()}
    execution = _execution()
    progress = TaskProgress(current_revision=0)

    assessment = _assessment_of(envelope, execution, progress, ())
    plan = _plan_of(envelope, execution, progress)

    assert assessment.classification == "task"
    assert [step.id for step in plan.steps] == ["find-data-source", "chart_and_average"]


def test_an_envelope_that_only_echoes_the_published_assessment_is_refused():
    """The plan stage shows the published assessment as data; echoing it is not a plan.

    The prompt carries ``Assessment already published: {…}`` ahead of the plan
    question (``plan_task``), so a model can answer with the assessment it was
    shown. That answer carries no plan, and the stage must say so rather than
    accept a plan-less draft.
    """
    envelope = {"assessment": _assessment_envelope()}
    execution = _execution()

    with pytest.raises(Exception, match="no plan object"):
        _plan_of(envelope, execution, TaskProgress(current_revision=0))


@pytest.mark.parametrize("step_id", ["a", "fetch_1", "find-data-source", "x" * 64])
def test_a_step_identity_the_grammar_admits_is_a_valid_step(step_id: str) -> None:
    assert TaskStep(**_step_fields(step_id)).id == step_id


@pytest.mark.parametrize("step_id", ["Find-Data", "x" * 65, "1step", "step id"])
def test_a_step_identity_the_grammar_refuses_is_rejected_by_the_pattern(step_id: str) -> None:
    # The refusal names the grammar, which is the pattern the prompt quotes.
    with pytest.raises(ValidationError, match=re.escape(STEP_ID_PATTERN)):
        TaskStep(**_step_fields(step_id))
