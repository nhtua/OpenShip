"""One admitted outcome drives a bounded task graph to its committed result.

The topology is fixed at eleven nodes. A plan's steps and revisions are durable
data in ``tasks.state``, never generated LangGraph nodes, so a grown plan
cannot grow the graph and an out-of-date checkpoint cannot regenerate an
approved plan or replay finished work. Authoritative state is always read
back from the durable records (task, plan, attempts, call bindings,
transcript, committed operations); the checkpoint carries only run identity,
the expected plan revision and a serializable cursor.

Two rules this module exists to protect:

* A model never gains authority. It may classify a request, draft a plan and
  ask for one tool call; the server rebuilds every capability, profile and
  evidence reference from the snapshot this run pinned (Task 10) and refuses
  the rest.
* A wait returns a parked disposition to the worker, which must not publish
  its ordinary final message or completion for a parked run.

Verification authority lives in ``tasks.verification`` and this graph only
records what it derives: the ``verify`` node asks for one step's verdict per
pass and commits it, so a step whose evidence does not hold stays unverified
and an approved plan keeps running without re-prompting; nothing here claims
a step is verified or a task complete beyond what the verdicts support.
A plan revision that reaches beyond what this run's pinned plan reviewed is
classified in ``tasks.routing`` and asks the user again, in either mode.
An unsettled call is never read as absence of work: the graph parks a durable
recovery decision and keeps its identity (Task 15).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import HTTPException
from langgraph.graph import END, StateGraph
from pydantic import ValidationError
from typing_extensions import TypedDict

from ..capabilities.contracts import PublishedToolResult, decode_execution_config
from ..capabilities.definitions import task_tool_definitions
from ..capabilities.policy import (
    policy_changed_for_capabilities_since,
    read_effective_tool_policy,
)
from ..providers.contracts import ChatMessage, ModelRequest
from ..providers.errors import ProviderError
from ..providers.runner import run_model_turn
from ..runs.service import parse_run_model_config
from ..runs.tasks.contracts import (
    ExecutionConfigV5,
    TaskAssessment,
    TaskCallBinding,
    TaskPlan,
    TaskProgress,
    TaskRecoveryReport,
)
from ..runs.tasks.decisions import park_task_wait
from ..runs.tasks.inference import assess_task, evidence_only_assessment, plan_task
from ..runs.tasks.recovery import (
    capture_recovery_evidence,
    confirmed_recovery_choice,
    prepare_task_recovery,
)
from ..runs.tasks.routing import classify_revision, has_open_uncertainty, select_action
from ..runs.tasks.state import (
    read_plan,
    read_progress,
    record_attempt_result,
    record_task_call,
    record_verification,
    save_plan,
)
from ..runs.tasks.verification import verify_step
from .agent_graph import _failure_for_tool, _result_message
from .transcript import append_transcript, read_transcript

TaskGraphState = TypedDict(
    "TaskGraphState",
    {
        "run_id": str,
        "conversation_id": str,
        "messages": list,
        "model_configuration": str,
        "execution_configuration": str,
        "tool_policy_revision": int,
        "expected_revision": int,
        "cursor": dict,
        "assistant_response": str,
        "usage": dict,
        "finish_reason": str,
        "parked": str,
    },
    total=False,
)

#: The cursor is the only mutable graph state: bounded counters plus the
#: drafts each stage published. Everything authoritative is read back.
_CURSOR_KEYS = frozenset(
    {
        "classification",
        "assessment_json",
        "plan_json",
        "plan_revision",
        "step_index",
        "step_id",
        "step_capability",
        "attempt",
        "logical_call_id",
        "provider_call_id",
        "arguments_json",
        "result_json",
        "parked",
        "stopped",
        "next_action",
        "action_step_id",
        "verification_recorded",
        "verification_inspected",
        "revision_class",
        "advance",
        "awaiting",
        "stage",
    }
)

#: A step may be attempted more than once only within this bound: one initial
#: attempt plus the repairs the per-step ceiling allows. The switcher and the
#: run's budget enforce the ceilings; this is the graph's own stop.
_MAX_STEP_ATTEMPTS = 3

#: What the answer node says when the run has nothing to execute. A plan the
#: server refused, and a run that reaches an answer with no plan at all, are both
#: results the person has to be told about in words: an empty assistant message
#: reads as a completed request, and the QA of run
#: ``e54c2653-122a-4868-be96-1331899a96b1`` ended exactly that way.
STOPPED_ANSWER = (
    "No plan could be validated for this request, so nothing was executed. "
    "The plan draft was refused ({code}). Narrow the request, enable the "
    "capabilities it needs, and ask again."
)
NO_PLAN_ANSWER = "This run finished without a plan to execute, so nothing was done."


def _cursor_of(state: TaskGraphState) -> dict:
    """Only known cursor keys survive a round trip through a checkpoint."""
    return {
        key: value
        for key, value in (state.get("cursor") or {}).items()
        if key in _CURSOR_KEYS
    }


def _execution_of(state: TaskGraphState) -> ExecutionConfigV5:
    """The snapshot this run pinned. A v5 graph never reads a legacy one."""
    raw = state.get("execution_configuration")
    try:
        execution = decode_execution_config(raw)
    except (TypeError, ValueError, ValidationError):
        raise ProviderError(
            "configuration_error",
            safe_message=(
                "this task run is not pinned to a readable v5 execution snapshot; "
                "a v5 graph never falls back to a legacy requirements graph"
            ),
        ) from None
    if not isinstance(execution, ExecutionConfigV5):
        raise ProviderError(
            "configuration_error",
            safe_message="a v5 graph runs only a chat run pinned to graph version 5",
        )
    if execution.graph_version != 5 or execution.graph_kind != "chat":
        raise ProviderError(
            "configuration_error",
            safe_message="a v5 graph runs only a chat run pinned to graph version 5",
        )
    return execution


def _stage(invocation, state: TaskGraphState, execution) -> Any:
    """One stage's view of this run: the worker invocation plus its snapshots.

    The snapshots come from the run's own records, never from today's
    installation, so a changed default cannot change what this run executes.
    """
    model_config = parse_run_model_config(
        state.get("model_configuration"), invocation.claim.run_id
    )
    stage = invocation
    stage.execution = execution
    stage.model_config = model_config
    return stage


def _assessment_of(cursor: dict) -> TaskAssessment | None:
    raw = cursor.get("assessment_json")
    if not raw:
        return None
    try:
        return TaskAssessment.model_validate_json(raw)
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
        raise ProviderError(
            "configuration_error",
            safe_message="the published assessment cannot be read back",
        ) from None


def _plan_of(cursor: dict) -> TaskPlan | None:
    raw = cursor.get("plan_json")
    if not raw:
        return None
    try:
        return TaskPlan.model_validate_json(raw)
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
        raise ProviderError(
            "configuration_error", safe_message="the published plan cannot be read back"
        ) from None


def _presentation(plan: TaskPlan, intent: str) -> str:
    """The public statement of a plan: intent, ordered steps, expectations."""
    steps = "; ".join(f"{step.id}: {step.intent}" for step in plan.steps)
    return f"Plan revision {plan.revision}: {intent} Steps: {steps}."


def _recovery_prompt(report: TaskRecoveryReport) -> str:
    """The parked recovery ask: what happened and the choices the records offer."""
    offer = ", ".join(report.eligible_actions)
    return (
        f"{report.explanation} Execution may have happened without a settled "
        f"result; the choices the durable records support are {offer}. Nothing "
        "runs again until one is chosen explicitly."
    )


def _committed_steps(progress: TaskProgress) -> frozenset[str]:
    """Which plan steps durable progress has settled, including a user's skip.

    A skipped step is settled as skipped, not as verified: it stays out of the
    verified set the verification records build, so a later pass can report the
    gap while the graph never replays it.
    """
    return frozenset(
        attempt.step_id
        for attempt in progress.attempts
        if attempt.status in {"succeeded", "failed", "interrupted"}
    ) | frozenset(progress.skipped_steps)


def _pending_step(
    plan: TaskPlan, cursor: dict, progress: TaskProgress, committed: frozenset[str]
) -> tuple[int, str] | None:
    """The next plan step durable progress has not committed a result for.

    The cursor may say where this graph invocation is, but the attempts that
    already exist say what is done; only the second one decides, so a stale
    checkpoint cannot re-run finished work or skip a step. A step whose
    dependencies are not committed yet is not eligible, which is how an
    approved continuation runs exactly one step at a time.
    """
    steps_by_id = {step.id: step for step in plan.steps}
    for index, step in enumerate(plan.steps):
        if step.id in committed:
            continue
        if not set(step.depends_on) <= committed:
            continue
        if not all(dependency in steps_by_id for dependency in step.depends_on):
            continue
        return index, step.id
    return None


def _bound_step(
    progress: TaskProgress, step_id: str, attempt: int
) -> TaskCallBinding | None:
    """The call this step attempt already committed, if any.

    A binding without an attempt row means the run dispatched a call whose
    outcome it never settled. That is uncertainty, not absence of work: the
    caller must stop and keep the identity, never ask the model again.
    """
    for binding in progress.call_bindings:
        if binding.step_id == step_id and binding.attempt == attempt:
            return binding
    return None


def build_task_graph(
    checkpointer=None,
    gateway_factory=None,
    invocation_factory=None,
):
    """The fixed v5 task graph: classify, plan, gate, then bounded steps.

    Registered only as ``("chat", 5)`` by ``runs.dispatch``. Every node is a
    transition over durable state, so an eight-step plan still walks these
    same nodes and a resumed run rebuilds from records, not a checkpoint.
    """
    if invocation_factory is None:
        raise ProviderError(
            "configuration_error",
            safe_message="a task graph requires a supervised worker invocation.",
        )
    selected_gateway = gateway_factory
    if selected_gateway is None:
        from ..database.session import SessionLocal
        from ..execution.gateway import ToolGateway

        selected_gateway = lambda: ToolGateway(SessionLocal)

    def _invocation(state: TaskGraphState):
        invocation = invocation_factory(state)
        if str(invocation.claim.run_id) != state["run_id"]:
            raise ProviderError("lease_lost")
        return invocation

    def assess(state: TaskGraphState) -> dict:
        """Classify this request against the evidence this run holds."""
        invocation = _invocation(state)
        execution = _execution_of(state)
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
        cursor = _cursor_of(state)
        if progress.current_revision >= 1:
            # A resumed or re-admitted run already has a plan: it rebuilds
            # from durable state instead of asking the model to plan again.
            cursor["stage"] = "resume"
            return {"cursor": cursor, "expected_revision": progress.current_revision}
        try:
            assessment = assess_task(
                _stage(invocation, state, execution), progress, corrections=1
            )
        except ProviderError:
            assessment = evidence_only_assessment(progress, ())
        cursor["classification"] = assessment.classification
        cursor["assessment_json"] = assessment.model_dump_json()
        return {"cursor": cursor, "expected_revision": progress.current_revision}

    def propose(state: TaskGraphState) -> dict:
        """Draft a plan whose every reference the server rebuilds or refuses."""
        invocation = _invocation(state)
        execution = _execution_of(state)
        cursor = _cursor_of(state)
        assessment = _assessment_of(cursor)
        if assessment is None:
            raise ProviderError(
                "configuration_error",
                safe_message="a plan requires the assessment this stage published.",
            )
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
        stage = _stage(invocation, state, execution)
        try:
            proposed = plan_task(stage, progress, assessment, corrections=1)
        except ProviderError as refused:
            cursor["stopped"] = refused.code
            return {"cursor": cursor}
        cursor["plan_json"] = proposed.model_dump_json()
        return {"cursor": cursor}

    def publish(state: TaskGraphState) -> dict:
        """Persist the validated plan, then present it before any execution.

        The published plan is classified against the plan this run carried
        before: the class is only a durable reading of what the new revision
        changed, and it is what lets the gate ask again for work that reaches
        beyond the reviewed scope.
        """
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        plan = _plan_of(cursor)
        if plan is None:
            raise ProviderError(
                "configuration_error", safe_message="no plan draft was published."
            )
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
            previous = read_plan(db, invocation.claim.run_id)
            save_plan(
                db, invocation.claim, plan, expected_revision=progress.current_revision
            )
            db.commit()
        intent = execution_of_intent(state)
        return {
            "cursor": {
                **cursor,
                "plan_revision": plan.revision,
                "step_index": 0,
                "revision_class": (
                    classify_revision(previous, plan) if previous is not None else ""
                ),
            },
            "assistant_response": _presentation(plan, intent),
            "expected_revision": plan.revision,
        }

    def gate(state: TaskGraphState) -> dict:
        """A Copilot run parks its plan until the exact decision is confirmed."""
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        with invocation.session_factory() as db:
            plan = read_plan(db, invocation.claim.run_id)
        prompt = (
            _presentation(plan, execution_of_intent(state))
            if plan
            else "Confirm the plan for this task."
        )
        with invocation.session_factory() as db:
            park_task_wait(
                db,
                invocation.claim,
                kind="plan_confirmation",
                prompt=prompt,
                now=_utcnow(),
            )
            db.commit()
        cursor["parked"] = "plan_confirmation"
        return {"cursor": cursor, "parked": "plan_confirmation"}

    def wait(state: TaskGraphState) -> dict:
        """Park focused input: a clarification ask, or a recovery decision for unsettled work.

        Uncertain work never guesses and never re-dispatches: the diagnosis runs
        OUTSIDE any transaction - capture the evidence identities, ask once, fall
        back to the labeled evidence-only report - and the report plus the request
        are prepared and parked in one transaction, so a crash leaves either both
        or neither. Re-entry re-presents the durable request, never a new ask.
        """
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        execution = _execution_of(state)
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
            if progress.pending_decision is not None:
                # The request is already open and delivered: re-presenting it is
                # the caller's job, and a second record would invent a new ask.
                return {
                    "cursor": {**cursor, "parked": progress.pending_decision.kind},
                    "parked": progress.pending_decision.kind,
                }
            snapshot = None
            recovery_choice = confirmed_recovery_choice(
                db, run_id=invocation.claim.run_id
            )
            recovering = has_open_uncertainty(progress) and recovery_choice is None
            if recovering:
                snapshot = capture_recovery_evidence(db, run_id=invocation.claim.run_id)
        if recovering:
            # Budgeted inference happens outside any lock or transaction: the
            # model may diagnose, but only the durable rows decide eligibility.
            try:
                assessment = assess_task(
                    _stage(invocation, state, execution), progress, corrections=1
                )
            except ProviderError:
                assessment = evidence_only_assessment(progress, ())
            with invocation.session_factory() as db:
                report = prepare_task_recovery(
                    db,
                    run_id=invocation.claim.run_id,
                    assessment=assessment,
                    now=_utcnow(),
                    snapshot=snapshot,
                )
                park_task_wait(
                    db,
                    invocation.claim,
                    kind="recovery",
                    prompt=_recovery_prompt(report),
                    now=_utcnow(),
                    recovery_report=report,
                )
                db.commit()
            cursor["parked"] = "recovery"
            return {"cursor": cursor, "parked": "recovery"}
        if has_open_uncertainty(progress) and recovery_choice is not None:
            # A committed choice already owns this uncertainty: opening another
            # request would invent a second ask, so the run says what is pending.
            return {
                "cursor": {
                    **cursor,
                    "stopped": "recovery_choice_pending",
                    "awaiting": recovery_choice[0],
                },
                "finish_reason": "blocked",
                "assistant_response": (
                    "A recovery choice is already recorded for the unresolved "
                    f"step ({recovery_choice[0]}); that step waits for its own "
                    "result rather than being asked about again."
                ),
            }
        assessment = _assessment_of(cursor)
        missing = assessment.missing_information if assessment else ()
        if missing:
            prompt = (
                f"{assessment.explanation} Please provide: "
                + "; ".join(missing)
                + ". The plan resumes with these values."
            )
        else:
            prompt = (
                assessment.explanation
                if assessment
                else "This run needs focused input before it can continue."
            )
        with invocation.session_factory() as db:
            park_task_wait(
                db,
                invocation.claim,
                kind="clarification",
                prompt=prompt[:10_000],
                now=_utcnow(),
            )
            db.commit()
        cursor["parked"] = "clarification"
        return {"cursor": cursor, "parked": "clarification"}

    def answer(state: TaskGraphState) -> dict:
        cursor = _cursor_of(state)
        assessment = _assessment_of(cursor)
        stopped = cursor.get("stopped")
        if stopped:
            # A refusal is news the person gets: the words name the code that
            # stopped the run, so a stopped plan is never an empty message.
            return {
                "assistant_response": STOPPED_ANSWER.format(code=stopped),
                "finish_reason": "stop",
                "cursor": cursor,
            }
        return {
            "assistant_response": assessment.explanation if assessment else NO_PLAN_ANSWER,
            "finish_reason": "stop",
            "cursor": cursor,
        }

    def step(state: TaskGraphState) -> dict:
        """Ask for the current step's one call, then bind it before dispatch."""
        invocation = _invocation(state)
        execution = _execution_of(state)
        cursor = _cursor_of(state)
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
            plan = read_plan(db, invocation.claim.run_id)
            recovery_choice = confirmed_recovery_choice(
                db, run_id=invocation.claim.run_id
            )
        if plan is None:
            raise ProviderError(
                "configuration_error",
                safe_message="no saved plan tells this run what to execute.",
            )
        if cursor.get("plan_revision") and cursor["plan_revision"] != plan.revision:
            # A checkpoint drafted for a plan this run is no longer on stopped
            # here: the record decides, so a stale draft cannot drive another
            # plan's steps.
            return {"cursor": {**cursor, "stopped": "plan_revision_stale"}}
        committed = _committed_steps(progress)
        target = _pending_step(plan, cursor, progress, committed)
        if target is None:
            raise ProviderError(
                "configuration_error", safe_message="no step is eligible to execute."
            )
        index, step_id = target
        if (
            recovery_choice is not None
            and recovery_choice[0] == "repeat_step"
            and recovery_choice[1] != step_id
        ):
            # The authorized repeat names its own step: it is eligible only if
            # its dependencies are committed, so the choice cannot jump ahead.
            named = next(
                (
                    (idx, item)
                    for idx, item in enumerate(plan.steps)
                    if item.id == recovery_choice[1]
                ),
                None,
            )
            if named is not None and set(named[1].depends_on) <= committed:
                index, step_id = named
        draft = next((item for item in plan.steps if item.id == step_id), None)
        if draft is None or draft.capability is None:
            raise ProviderError(
                "configuration_error",
                safe_message="the step this plan proposed has no authorized capability.",
            )

        attempt_ordinal = (
            sum(1 for attempt in progress.attempts if attempt.step_id == step_id) + 1
        )
        repeating = (
            recovery_choice is not None
            and recovery_choice[0] == "repeat_step"
            and recovery_choice[1] == step_id
            and recovery_choice[2] >= attempt_ordinal
        )
        already_bound = _bound_step(progress, step_id, attempt_ordinal)
        if already_bound is not None and not repeating:
            # The call this step attempted is recorded but has no outcome: the
            # work may have run, so repeating it is not this graph's to decide.
            cursor["stopped"] = "dispatch_uncertain"
            cursor["logical_call_id"] = already_bound.logical_call_id
            return {"cursor": cursor}
        if attempt_ordinal > _MAX_STEP_ATTEMPTS:
            raise ProviderError(
                "budget_exceeded",
                safe_message="this step already carries its bounded attempts.",
            )
        if repeating:
            # The user chose to run this step again: the dead call keeps its
            # place in the ledger and the transcript as history, and its open
            # model turn is closed with the interruption the records support
            # before the same attempt ordinal is re-opened with its own fresh
            # committed call. Nothing else about the step is decided here.
            _close_interrupted_calls(
                invocation, step_id=step_id, below_attempt=attempt_ordinal + 1
            )

        enabled = _policy_enabled_ids(invocation, state)
        definitions = tuple(
            tool
            for tool in task_tool_definitions(execution, enabled)
            if tool.name == draft.capability.id
        )
        if not definitions:
            raise ProviderError(
                "unsupported_capability",
                safe_message=(
                    f"The capability this plan step needs is no longer authorized: {draft.capability.id}."
                ),
            )

        ordinal = _next_model_turn_ordinal(invocation)
        stage = _stage(invocation, state, execution)
        request = ModelRequest(
            messages=_step_messages(invocation, execution, plan, step_id, draft.intent),
            tools=definitions,
            tool_choice="auto",
            max_output_tokens=min(
                stage.model_config.limits.max_output_tokens,
                execution.loop_limits.max_output_tokens_per_request,
            ),
        )
        result = asyncio.run(
            run_model_turn(
                request,
                stage.model_config,
                stage.control,
                stage.recorder,
                authorize_profile=stage.authorize_profile,
                budget=stage.budget,
                model_turn_ordinal=ordinal,
            )
        )
        assistant = ChatMessage(
            role="assistant",
            content=result.text,
            tool_calls=result.tool_calls or None,
            continuation=result.continuation,
        )
        # Commit the turn the run actually made before its proposal is used.
        _append_transcript(invocation, ordinal, assistant)
        if not result.tool_calls:
            # A step that answers in prose has not done the step's work: the
            # text is recorded, and the switch decides what is next.
            cursor["stopped"] = "no_tool_call"
            cursor["step_id"] = step_id
            cursor["attempt"] = attempt_ordinal
            return {"cursor": cursor}
        call = result.tool_calls[0]
        if len(result.tool_calls) != 1 or call.name != draft.capability.id:
            raise ProviderError(
                "unsupported_capability",
                safe_message="the model asked for a tool this plan step does not own.",
            )
        logical_call_id = f"model:{ordinal}:call:0"
        invocation.budget.admit_tool(logical_call_id)
        # The binding is committed before dispatch, so a worker that dies
        # after this point still knows this logical call may have run.
        with invocation.session_factory() as db:
            record_task_call(
                db,
                invocation.claim,
                binding=TaskCallBinding(
                    schema_version=1,
                    plan_revision=plan.revision,
                    step_id=step_id,
                    attempt=attempt_ordinal,
                    logical_call_id=logical_call_id,
                ),
            )
            db.commit()
        return {
            "cursor": {
                **cursor,
                "step_index": index,
                "step_id": step_id,
                "step_capability": draft.capability.id,
                "attempt": attempt_ordinal,
                "logical_call_id": logical_call_id,
                "provider_call_id": call.provider_call_id,
                "arguments_json": json.dumps(call.arguments),
            }
        }

    def dispatch(state: TaskGraphState) -> dict:
        """Run the authorized gateway call and commit its operation as an attempt."""
        invocation = _invocation(state)
        execution = _execution_of(state)
        cursor = _cursor_of(state)
        capability_id = cursor.get("step_capability")
        step_id = cursor.get("step_id")
        if not step_id or not capability_id or not cursor.get("logical_call_id"):
            raise ProviderError(
                "configuration_error",
                safe_message="the dispatched step is not readable.",
            )
        ref = next(
            (
                item
                for item in execution.allowed_capabilities
                if item.id == capability_id
            ),
            None,
        )
        if ref is None:
            raise ProviderError(
                "unsupported_capability",
                safe_message="the capability this step pinned is not in this run's snapshot.",
            )

        gateway = selected_gateway() if callable(selected_gateway) else selected_gateway
        invocation.control.raise_if_stopped()
        try:
            published = asyncio.run(
                gateway.execute_tool(
                    claim=invocation.claim,
                    context=invocation.context,
                    logical_call_id=cursor["logical_call_id"],
                    ref=ref,
                    arguments=json.loads(cursor.get("arguments_json") or "{}"),
                    inputs=tuple(execution.selected_artifacts),
                    control=invocation.control,
                )
            )
        except HTTPException:
            raise ProviderError(
                "unsupported_capability",
                safe_message="the tool authority check rejected this step.",
            ) from None
        except ProviderError:
            raise
        except Exception as refused:  # noqa: BLE001 - keep provider details private.
            raise ProviderError(
                "execution_interrupted",
                safe_message="the step could not be executed safely.",
                cause=refused,
            ) from None
        try:
            published = PublishedToolResult.model_validate(published)
        except (TypeError, ValueError, ValidationError) as invalid:
            raise ProviderError(
                "invalid_response",
                safe_message="the step returned a result this run cannot use.",
                cause=invalid,
            ) from invalid

        ordinal = int(cursor["logical_call_id"].split(":")[1])
        with invocation.session_factory() as db:
            append_transcript(
                db,
                invocation.claim,
                ordinal,
                ChatMessage(
                    role="tool",
                    name=capability_id,
                    tool_call_id=cursor.get("provider_call_id") or f"step:{step_id}",
                    content=_result_message(
                        published, execution.limits.max_model_result_bytes
                    ),
                ),
            )
            record_attempt_result(
                db,
                invocation.claim,
                step_id=step_id,
                attempt=cursor["attempt"],
                operation_id=published.operation_id,
            )
            db.commit()
        cursor["result_json"] = published.model_dump_json()
        cursor["step_index"] = int(cursor.get("step_index", 0)) + 1
        return {"cursor": cursor}

    def switch(state: TaskGraphState) -> dict:
        """Read the durable progress, then let the switcher choose next action.

        Uncertain or interrupted work goes to the wait node instead of stopping:
        Task 15 owns the diagnosis and the durable abort/repeat/continue choice,
        so this graph neither repeats nor skips work that may still be running.
        """
        invocation = _invocation(state)
        execution = _execution_of(state)
        cursor = _cursor_of(state)
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
            recovery_choice = confirmed_recovery_choice(
                db, run_id=invocation.claim.run_id
            )
        published = None
        if cursor.get("result_json"):
            try:
                published = PublishedToolResult.model_validate_json(
                    cursor["result_json"]
                )
            except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
                published = None
        if (
            published is not None
            and _failure_for_tool(published) is not None
            and recovery_choice is None
        ):
            # The step's identity is already durable (binding plus committed
            # operation); what is missing is a settled outcome, and this graph
            # must not read that as a call that never ran: the recovery wait
            # diagnoses from durable rows and never re-dispatches here.
            cursor.pop("stopped", None)
            return {
                "cursor": {
                    **cursor,
                    "next_action": "wait",
                    "awaiting": "the step stopped without a settled result",
                }
            }
        # The Copilot confirmation gate is this graph's own: the `gate` node
        # parks a proposed plan before any step can run, and while a decision
        # is still open `select_action` returns wait from the durable
        # pending_decision. Passing the mode here would re-park a run whose
        # decision the human already resolved, so the caller owns that gate.
        action = select_action(
            progress,
            _assessment_of(cursor),
            policy_allows=True,
            budget_allows=True,
            limits=execution.task_limits,
        )
        if recovery_choice is not None and recovery_choice[0] == "repeat_step":
            # The user authorized repeating that step, so its open uncertainty
            # is being resolved by an explicit choice: the switch advances to
            # the step node instead of parking the same diagnosis again.
            return {
                "cursor": {
                    **cursor,
                    "next_action": "execute",
                    "action_step_id": recovery_choice[1],
                    "awaiting": "the chosen repeat is authorized for that step",
                }
            }
        if action.kind == "finish":
            return {"cursor": {**cursor, "next_action": "finish"}}
        return {
            "cursor": {
                **cursor,
                "next_action": action.kind,
                "action_step_id": action.step_id or "",
                "awaiting": action.reason,
            }
        }

    def verify(state: TaskGraphState) -> dict:
        """Ask the verification authority for one step's verdict and record it.

        The verdict comes from ``tasks.verification`` reading the durable
        records - committed attempt, committed operation, the artifacts that
        operation published and their stored bytes - never from the model's
        words. Recording a failing verdict keeps the step unverified and notes
        the disposition the records support; nothing here can read as a
        completed task. The step's verdict is recorded at most once per
        invocation so a failing verdict cannot spin the switch.
        """
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        step_id = cursor.get("action_step_id")
        recorded = list(cursor.get("verification_recorded") or ())
        inspected = list(cursor.get("verification_inspected") or ())
        if not step_id:
            return {
                "cursor": {
                    **cursor,
                    "verification_recorded": recorded,
                    "verification_inspected": inspected,
                    "advance": "finish",
                }
            }
        if step_id not in inspected:
            inspected.append(step_id)
        assessment = _assessment_of(cursor)
        if assessment is None:
            # An assessment-less cursor cites nothing durable: the verdict
            # must fail rather than pass without cited evidence.
            assessment = TaskAssessment(
                classification="task",
                explanation="This stage cites no evidence of its own.",
            )
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
            has_attempt = any(
                attempt.step_id == step_id for attempt in progress.attempts
            )
            if has_attempt:
                report = verify_step(
                    db,
                    run_id=invocation.claim.run_id,
                    step_id=step_id,
                    assessment=assessment,
                )
                progress = record_verification(
                    db,
                    invocation.claim,
                    step_id=step_id,
                    report=report,
                )
                db.commit()
                recorded.append(step_id)
            plan = read_plan(db, invocation.claim.run_id)
            pending = (
                _pending_step(plan, cursor, progress, _committed_steps(progress))
                if plan is not None
                else None
            )
        advance = "finish"
        if pending is not None:
            advance = "step"
        return {
            "cursor": {
                **cursor,
                "verification_recorded": recorded,
                "verification_inspected": inspected,
                "advance": advance,
            }
        }

    def finish(state: TaskGraphState) -> dict:
        if state.get("finish_reason") or state.get("assistant_response"):
            return {}
        invocation = _invocation(state)
        with invocation.session_factory() as db:
            plan = read_plan(db, invocation.claim.run_id)
        intent = execution_of_intent(state) if plan else ""
        return {
            "finish_reason": "stop",
            # Never an empty message: a run that ends with nothing to present has
            # to say that, not leave the reader with a blank turn.
            "assistant_response": (_presentation(plan, intent) if plan else NO_PLAN_ANSWER)
            or NO_PLAN_ANSWER,
        }

    def _after_assess(state: TaskGraphState) -> str:
        cursor = state.get("cursor") or {}
        if cursor.get("stage") == "resume":
            return "switch"
        classification = cursor.get("classification")
        if classification == "task":
            return "propose"
        if classification in {"clarification", "blocked"}:
            return "wait"
        return "answer"

    def _after_propose(state: TaskGraphState) -> str:
        if (state.get("cursor") or {}).get("plan_json"):
            return "publish"
        # A plan the server refused is a result the person has to be told about,
        # so it goes to `answer`, which says so in words. Routing a plan-less
        # cursor to `finish` completed the run with an empty assistant message,
        # a null task, and the stop code visible nowhere.
        return "answer"

    def _after_publish(state: TaskGraphState) -> str:
        execution = _execution_of(state)
        if execution.task_context.mode == "copilot":
            return "gate"
        if (_cursor_of(state) or {}).get("revision_class") == "expanded_scope":
            # New sources or scope cross what the reviewed plan pinned: they ask
            # the user even in Auto, whose approval only ever covered the
            # original intent.
            return "gate"
        return "switch"

    def _after_switch(state: TaskGraphState) -> str:
        cursor = state.get("cursor") or {}
        if state.get("finish_reason") or cursor.get("stopped"):
            return "finish"
        action = cursor.get("next_action")
        if action in {"execute", "repair"}:
            return "step"
        if action == "revise":
            return "propose"
        if action == "wait":
            return "wait"
        if action == "verify":
            step_id = cursor.get("action_step_id")
            inspected = set(cursor.get("verification_inspected") or ())
            if step_id and step_id not in inspected:
                return "verify"
            # This invocation already inspected that step: asking again would
            # loop, so the run ends with its durable progress published and
            # any failing verdict still owed.
            return "finish"
        return "finish"

    def _after_verify(state: TaskGraphState) -> str:
        return (
            "step" if (state.get("cursor") or {}).get("advance") == "step" else "finish"
        )

    def _after_step(state: TaskGraphState) -> str:
        cursor = state.get("cursor") or {}
        if cursor.get("stopped") or not cursor.get("logical_call_id"):
            return "switch"
        return "dispatch"

    builder = StateGraph(TaskGraphState)
    builder.add_node("assess", assess)
    builder.add_node("propose", propose)
    builder.add_node("publish", publish)
    builder.add_node("gate", gate)
    builder.add_node("wait", wait)
    builder.add_node("answer", answer)
    builder.add_node("step", step)
    builder.add_node("dispatch", dispatch)
    builder.add_node("switch", switch)
    builder.add_node("verify", verify)
    builder.add_node("finish", finish)
    builder.set_entry_point("assess")
    builder.add_conditional_edges(
        "assess",
        _after_assess,
        {"switch": "switch", "answer": "answer", "wait": "wait", "propose": "propose"},
    )
    builder.add_conditional_edges(
        "propose", _after_propose, {"publish": "publish", "answer": "answer"}
    )
    builder.add_conditional_edges(
        "publish", _after_publish, {"gate": "gate", "switch": "switch"}
    )
    builder.add_conditional_edges(
        "switch",
        _after_switch,
        {
            "step": "step",
            "verify": "verify",
            "wait": "wait",
            "propose": "propose",
            "finish": "finish",
        },
    )
    builder.add_conditional_edges(
        "verify",
        _after_verify,
        {"step": "step", "finish": "finish"},
    )
    builder.add_conditional_edges(
        "step",
        _after_step,
        {"dispatch": "dispatch", "switch": "switch"},
    )
    builder.add_edge("dispatch", "switch")
    builder.add_edge("gate", END)
    builder.add_edge("wait", END)
    builder.add_edge("answer", END)
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)


def execution_of_intent(state: TaskGraphState) -> str:
    """The request this run pinned, which is what a plan restates."""
    return _execution_of(state).task_context.original_intent


def _utcnow():
    """The durable clock the task state already uses for waits."""
    from src.openship.runs.tasks.state import _now

    return _now()


def _policy_enabled_ids(invocation, state: TaskGraphState) -> frozenset[str]:
    """Re-read enablement before every model request: policy may have moved.

    A capability whose policy changed since this run pinned its revision is
    taken away, exactly as the maintained v4 loop does, so a plan cannot keep
    a tool the operator revoked while the task ran.
    """
    with invocation.session_factory() as db:
        policy = read_effective_tool_policy(db)
        if not policy.state_available:
            raise ProviderError(
                "unsupported_capability",
                safe_message="the tool policy this run pinned is no longer available.",
            )
        execution = _execution_of(state)
        revision = state.get("tool_policy_revision")
        changed = {
            ref.id
            for ref in execution.allowed_capabilities
            if policy_changed_for_capabilities_since(
                db,
                frozenset({ref.id}),
                revision,
                current_revision=policy.revision,
            )
        }
        return policy.effective_ids - changed


def _next_model_turn_ordinal(invocation) -> int:
    with invocation.session_factory() as db:
        recorded = read_transcript(db, invocation.claim.run_id)
    if not recorded:
        return 0
    return max(entry.model_turn_ordinal for entry in recorded) + 1


def _append_transcript(invocation, ordinal: int, message: ChatMessage) -> None:
    """Commit the turn the run actually made, before its proposal is consumed."""
    with invocation.session_factory() as db:
        append_transcript(db, invocation.claim, ordinal, message)
        db.commit()


def _close_interrupted_calls(invocation, *, step_id: str, below_attempt: int) -> int:
    """Close transcript calls a chosen repeat is about to supersede.

    A worker that died after admitting a call leaves that call's assistant
    turn open, and the transcript machinery refuses to continue a model
    conversation over an unresolved call. The choice says the step runs
    again, so the open turn is closed with what the records prove: no result
    was committed, and nothing here states whether the call ran.
    """
    with invocation.session_factory() as db:
        entries = read_transcript(db, invocation.claim.run_id)
        progress = read_progress(db, invocation.claim.run_id)
    settled = {(attempt.step_id, attempt.attempt) for attempt in progress.attempts}
    owed = {
        binding.logical_call_id
        for binding in progress.call_bindings
        if binding.step_id == step_id
        and binding.attempt < below_attempt
        and (binding.step_id, binding.attempt) not in settled
    }
    if not owed:
        return 0
    resolved = {
        entry.message.tool_call_id
        for entry in entries
        if entry.message.role == "tool" and entry.message.tool_call_id
    }
    closed = 0
    for entry in entries:
        if entry.message.role != "assistant" or not entry.message.tool_calls:
            continue
        if not set(entry.logical_call_ids) & owed:
            continue
        for call in entry.message.tool_calls:
            if call.provider_call_id in resolved:
                continue
            _append_transcript(
                invocation,
                entry.model_turn_ordinal,
                ChatMessage(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.provider_call_id,
                    content=(
                        '{"status":"interrupted","note":"The worker stopped '
                        "before this call's result was committed; the records "
                        'do not say whether it ran."}'
                    ),
                ),
            )
            closed += 1
    return closed


def _step_messages(
    invocation, execution, plan: TaskPlan, step_id: str, intent: str
) -> tuple[ChatMessage, ...]:
    """The recorded transcript plus this step's instruction, nothing invented.

    The graph reads the durable transcript instead of the worker's chat state,
    so a title-generation instruction can never reach a tool's arguments. The
    original request is the one the run pinned in its snapshot.
    """
    with invocation.session_factory() as db:
        recorded = read_transcript(db, invocation.claim.run_id)
    messages = [
        ChatMessage(
            role="user",
            content=(
                f"Task: {execution.task_context.original_intent} "
                f"Plan revision {plan.revision}: "
                + "; ".join(f"{step.id}: {step.intent}" for step in plan.steps)
            ),
        )
    ]
    messages.extend(entry.message for entry in recorded)
    messages.append(
        ChatMessage(
            role="user",
            content=(
                f"Plan step {step_id}: {intent}. Use exactly the authorized tool for this step"
                " with the arguments the published evidence supports."
            ),
        )
    )
    return tuple(messages)
