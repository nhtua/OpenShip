"""What the inference stages tell a model to answer, and what they accept.

The planning stages are a contract with a model: the prompt is the only text a
real provider ever sees, and the validators are what the run accepts. When the
two disagree, a real model is refused on every attempt while a lane that answers
in the validators' own shape stays green. Two dev-stack QA runs of the same
request hit exactly that: run ``e54c2653-122a-4868-be96-1331899a96b1`` (a flat
assessment shape, underscores-only step ids) and run
``a99aa6de-3d41-4d0a-99b9-e6c56649c43b`` (both objects wrapped one level too
deep). These cases pin the two sides against each other: the envelope shown in
the prompt is parsed and handed to the validators, so a shape that is shown and
refused cannot survive a test run.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.openship.capabilities.catalog import list_capability_manifests
from src.openship.capabilities.contracts import CapabilityRef
from src.openship.providers.errors import ProviderError
from src.openship.runs.tasks.contracts import (
    STEP_ID_GRAMMAR,
    STEP_ID_PATTERN,
    TaskAssessment,
    TaskProgress,
    TaskStep,
)
from src.openship.runs.tasks.inference import _assessment_of, _plan_of, _stage_prompt
from tests.test_task_profile_selection import _all_profiles, _catalog

PINNED_CAPABILITIES = ("download_file", "python_execute")
ASSESSMENT_MARKER = "Assessment shape,"
PLAN_MARKER = "Assessment and plan shape,"


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
        profile_catalog=_catalog({
            key: value for key, value in _all_profiles().items()
            if key in {"standard", "logs-data"}
        }),
        task_context=SimpleNamespace(original_intent="Chart the observations and average them."),
    )


def _envelope_shown(prompt: str, marker: str) -> dict:
    """The example envelope the prompt shows, as the model would read it."""
    line = next(line for line in prompt.splitlines() if line.startswith(marker))
    return json.loads(line.split(": ", 1)[1])


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


def _assessment_fields(**overrides) -> dict:
    """The assessment example as the contract stores it: JSON lists become tuples."""
    fields = {**_assessment_envelope(), **overrides}
    return {k: (tuple(v) if isinstance(v, list) else v) for k, v in fields.items()}


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


def _wrapped(envelope: dict) -> dict:
    """Each object wrapped in a copy of its own key - what the real model wrote."""
    return {key: {key: value} for key, value in envelope.items()}


def test_the_assessment_example_the_prompt_shows_is_accepted_by_the_validator():
    """The shape a model is shown has to be the shape the stage reads. Parse it, hand it to the validator."""
    prompt = _stage_prompt("assessment", _execution(), ())
    shown = _envelope_shown(prompt, ASSESSMENT_MARKER)

    assert set(shown) == {"assessment"}
    assessment = _assessment_of(shown, _execution(), TaskProgress(current_revision=0), ())
    assert assessment.classification in {"informational", "clarification", "task", "blocked"}


def test_the_plan_example_the_prompt_shows_is_accepted_by_both_validators():
    prompt = _stage_prompt("assessment_and_plan", _execution(), ())
    shown = _envelope_shown(prompt, PLAN_MARKER)
    execution = _execution()
    progress = TaskProgress(current_revision=0)

    assert set(shown) == {"assessment", "plan"}
    _assessment_of(shown, execution, progress, ())
    plan = _plan_of(shown, execution, progress)
    assert plan.steps, "the example plan the prompt shows must be a plan the stage accepts"


def test_the_prompt_shows_the_envelope_once_not_nested_in_itself():
    """The QA run ``a99aa6de`` answered ``{"assessment":{"assessment":{…}}}`` because the prompt showed a nested object without saying what holds it."""
    prompt = _stage_prompt("assessment_and_plan", _execution(), ())

    assert "never a copy of this envelope" in prompt
    shown = _envelope_shown(prompt, PLAN_MARKER)
    for key, value in shown.items():
        assert set(value) != {key}, f'"{key}" is shown wrapped in itself'


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


def test_an_envelope_that_wraps_each_object_once_is_understood():
    """Real models write this: run ``a99aa6de-3d41-4d0a-99b9-e6c56649c43b`` answered every stage this way.

    ``{"assessment": {"assessment": {…}}, "plan": {"plan": {…}}}`` asks the same
    question one layer deeper. The stage reads through the redundant wrapper
    rather than refusing an answer that says what it means.
    """
    envelope = _wrapped({"assessment": _assessment_envelope(), "plan": _plan_envelope()})
    execution = _execution()

    assessment = _assessment_of(envelope, execution, TaskProgress(current_revision=0), ())
    plan = _plan_of(envelope, execution, TaskProgress(current_revision=0))

    assert assessment.classification == "task"
    assert [step.id for step in plan.steps] == ["find-data-source", "chart_and_average"]


def test_a_second_wrapper_is_still_refused():
    """Reading through one wrapper is leniency about a shape, not a parser."""
    envelope = {
        "assessment": {"assessment": {"assessment": _assessment_envelope()}},
        "plan": {"plan": {"plan": _plan_envelope()}},
    }

    with pytest.raises(ProviderError):
        _assessment_of(envelope, _execution(), TaskProgress(current_revision=0), ())
    with pytest.raises(ProviderError):
        _plan_of(envelope, _execution(), TaskProgress(current_revision=0))


def test_an_envelope_that_only_echoes_the_published_assessment_is_refused():
    """The plan stage shows the published assessment as data; echoing it is not a plan.

    The prompt carries ``Assessment already published: {…}`` ahead of the plan
    question (``plan_task``), so a model can answer with the assessment it was
    shown. That answer carries no plan, and the stage must say so rather than
    accept a plan-less draft.
    """
    envelope = {"assessment": _assessment_envelope()}

    with pytest.raises(Exception, match="no plan object"):
        _plan_of(envelope, _execution(), TaskProgress(current_revision=0))


def test_the_prompt_states_the_assessment_rules_the_validator_enforces():
    """A rule the model cannot see is a rule it will break, at every prompt.

    Dev-stack QA on 2026-11-11 made this universal rather than occasional: in run
    ``34341203-b8f3-4fda-8ddc-e13f67a180ff`` every one of the four recorded
    answers wrote a sentence into ``recommended_recovery_action`` (a bounded
    choice) and left ``step_id`` null under a ``known``/``material`` failure, so
    each answer was refused and the chat answered "no plan could be validated"
    for any request. The rules below are read off the contract's own validators.
    """
    prompt = _stage_prompt("assessment", _execution(), ())
    for phrase in (
        "recommended_recovery_action",
        "abort|repeat_step|continue_next",
        "uncertain",
        "step_id",
        "known|material",
        "clarification",
        "missing_information",
    ):
        assert phrase in prompt

    # The prompt's rules are the contract's rules, not a paraphrase of them.
    with pytest.raises(ValidationError, match="recovery recommendation"):
        TaskAssessment(
            **_assessment_fields(
                failure="known",
                step_id="find-data-source",
                recommended_recovery_action="abort",
            )
        )
    with pytest.raises(ValidationError, match="step identifier"):
        TaskAssessment(**_assessment_fields(failure="known"))
    with pytest.raises(ValidationError, match="clarification requires"):
        TaskAssessment(**_assessment_fields(classification="clarification"))
    accepted = TaskAssessment(
        **_assessment_fields(
            failure="uncertain",
            recommended_recovery_action="repeat_step",
        )
    )
    assert accepted.recommended_recovery_action == "repeat_step"


def test_advice_written_in_the_recovery_field_is_refused():
    """Prose belongs in the explanation; the recovery field names a choice."""
    fields = {
        **_assessment_envelope(),
        "recommended_recovery_action": "ask the user where to send the chart",
    }

    with pytest.raises(ProviderError):
        _assessment_of({"assessment": fields}, _execution(), TaskProgress(current_revision=0), ())


@pytest.mark.parametrize("step_id", ["a", "fetch_1", "find-data-source", "x" * 64])
def test_a_step_identity_the_grammar_admits_is_a_valid_step(step_id: str) -> None:
    assert TaskStep(**_step_fields(step_id)).id == step_id


@pytest.mark.parametrize("step_id", ["Find-Data", "x" * 65, "1step", "step id"])
def test_a_step_identity_the_grammar_refuses_is_rejected_by_the_pattern(step_id: str) -> None:
    # The refusal names the grammar, which is the pattern the prompt quotes.
    with pytest.raises(ValidationError, match=re.escape(STEP_ID_PATTERN)):
        TaskStep(**_step_fields(step_id))
