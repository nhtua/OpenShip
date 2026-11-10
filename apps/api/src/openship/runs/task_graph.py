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

Verification authority (which committed evidence satisfies a plan criterion)
belongs to Task 12, and uncertain-operation diagnosis plus recovery choices to
Task 15. Until verification exists the ``verify`` node records one deferral per
step and advances to the next eligible step, so an approved plan keeps running
without re-prompting; nothing here claims a step is verified or a task complete.
An unsettled attempt is never read as absence of work: the graph stops and keeps
its identity.
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
)
from ..runs.tasks.decisions import park_task_wait
from ..runs.tasks.inference import assess_task, evidence_only_assessment, plan_task
from ..runs.tasks.routing import select_action
from ..runs.tasks.state import (
    read_plan,
    read_progress,
    record_attempt_result,
    record_task_call,
    save_plan,
)
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
        "verification_deferred",
        "advance",
        "awaiting",
        "stage",
    }
)

#: A step may be attempted more than once only within this bound: one initial
#: attempt plus the repairs the per-step ceiling allows. The switcher and the
#: run's budget enforce the ceilings; this is the graph's own stop.
_MAX_STEP_ATTEMPTS = 3


def _cursor_of(state: TaskGraphState) -> dict:
    """Only known cursor keys survive a round trip through a checkpoint."""
    return {key: value for key, value in (state.get("cursor") or {}).items() if key in _CURSOR_KEYS}


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
    model_config = parse_run_model_config(state.get("model_configuration"), invocation.claim.run_id)
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
            "configuration_error", safe_message="the published assessment cannot be read back"
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


def _committed_steps(progress: TaskProgress) -> frozenset[str]:
    """Which plan steps already have a committed terminal attempt outcome."""
    return frozenset(
        attempt.step_id
        for attempt in progress.attempts
        if attempt.status in {"succeeded", "failed", "interrupted"}
    )


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


def _bound_step(progress: TaskProgress, step_id: str, attempt: int) -> TaskCallBinding | None:
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
            assessment = assess_task(_stage(invocation, state, execution), progress, corrections=1)
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
        """Persist the validated plan, then present it before any execution."""
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        plan = _plan_of(cursor)
        if plan is None:
            raise ProviderError("configuration_error", safe_message="no plan draft was published.")
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
            save_plan(db, invocation.claim, plan, expected_revision=progress.current_revision)
            db.commit()
        intent = execution_of_intent(state)
        return {
            "cursor": {**cursor, "plan_revision": plan.revision, "step_index": 0},
            "assistant_response": _presentation(plan, intent),
            "expected_revision": plan.revision,
        }

    def gate(state: TaskGraphState) -> dict:
        """A Copilot run parks its plan until the exact decision is confirmed."""
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        with invocation.session_factory() as db:
            plan = read_plan(db, invocation.claim.run_id)
        prompt = _presentation(plan, execution_of_intent(state)) if plan else "Confirm the plan for this task."
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
        """A clarification or blocker parks focused input, never a guess."""
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
        if progress.pending_decision is not None:
            # The request is already open and delivered: re-presenting it is
            # the caller's job, and a second record would invent a new ask.
            return {"cursor": {**cursor, "parked": progress.pending_decision.kind}, "parked": progress.pending_decision.kind}
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
        return {
            "assistant_response": assessment.explanation if assessment else "",
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
        if plan is None:
            raise ProviderError(
                "configuration_error", safe_message="no saved plan tells this run what to execute."
            )
        if cursor.get("plan_revision") and cursor["plan_revision"] != plan.revision:
            # A checkpoint drafted for a plan this run is no longer on stopped
            # here: the record decides, so a stale draft cannot drive another
            # plan's steps.
            return {"cursor": {**cursor, "stopped": "plan_revision_stale"}}
        committed = _committed_steps(progress)
        target = _pending_step(plan, cursor, progress, committed)
        if target is None:
            raise ProviderError("configuration_error", safe_message="no step is eligible to execute.")
        index, step_id = target
        draft = next((item for item in plan.steps if item.id == step_id), None)
        if draft is None or draft.capability is None:
            raise ProviderError(
                "configuration_error",
                safe_message="the step this plan proposed has no authorized capability.",
            )

        attempt_ordinal = sum(1 for attempt in progress.attempts if attempt.step_id == step_id) + 1
        already_bound = _bound_step(progress, step_id, attempt_ordinal)
        if already_bound is not None:
            # The call this step attempted is recorded but has no outcome: the
            # work may have run, so repeating it is not this graph's to decide.
            cursor["stopped"] = "dispatch_uncertain"
            cursor["logical_call_id"] = already_bound.logical_call_id
            return {"cursor": cursor}
        if attempt_ordinal > _MAX_STEP_ATTEMPTS:
            raise ProviderError(
                "budget_exceeded", safe_message="this step already carries its bounded attempts."
            )

        enabled = _policy_enabled_ids(invocation, state)
        definitions = tuple(
            tool for tool in task_tool_definitions(execution, enabled) if tool.name == draft.capability.id
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
            raise ProviderError("configuration_error", safe_message="the dispatched step is not readable.")
        ref = next(
            (item for item in execution.allowed_capabilities if item.id == capability_id),
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
                    content=_result_message(published, execution.limits.max_model_result_bytes),
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

        Uncertain or interrupted work stops here: Task 15 owns the diagnosis
        and the durable abort/repeat/continue choice, so this graph neither
        repeats nor skips it.
        """
        invocation = _invocation(state)
        execution = _execution_of(state)
        cursor = _cursor_of(state)
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
        published = None
        if cursor.get("result_json"):
            try:
                published = PublishedToolResult.model_validate_json(cursor["result_json"])
            except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
                published = None
        if published is not None and _failure_for_tool(published) is not None:
            # The step's identity is already durable (binding plus committed
            # operation); what is missing is a settled outcome, and this graph
            # must not read that as a call that never ran.
            return {
                "cursor": {**cursor, "stopped": "uncertain", "awaiting": "recovery"},
                "finish_reason": "blocked",
                "assistant_response": (
                    "A step stopped without a settled result. Recovery needs the durable "
                    "choice this run has not been given yet."
                ),
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
        """Note that one step still needs its evidence inspected, then advance.

        Task 12 owns the verdict (does committed evidence satisfy this step's
        criteria). Until it exists this node records exactly one deferral per
        step per invocation and moves on only to the next eligible step durable
        progress has not committed a result for. It never writes a verdict, so
        nothing here can read as verified work or a completed task.
        """
        invocation = _invocation(state)
        cursor = _cursor_of(state)
        step_id = cursor.get("action_step_id")
        deferred = list(cursor.get("verification_deferred") or ())
        if step_id and step_id not in deferred:
            deferred.append(step_id)
        with invocation.session_factory() as db:
            progress = read_progress(db, invocation.claim.run_id)
            plan = read_plan(db, invocation.claim.run_id)
        advance = "finish"
        if plan is not None and _pending_step(plan, cursor, progress, _committed_steps(progress)):
            advance = "step"
        return {"cursor": {**cursor, "verification_deferred": deferred, "advance": advance}}

    def finish(state: TaskGraphState) -> dict:
        if state.get("finish_reason") or state.get("assistant_response"):
            return {}
        invocation = _invocation(state)
        with invocation.session_factory() as db:
            plan = read_plan(db, invocation.claim.run_id)
        intent = execution_of_intent(state) if plan else ""
        return {"finish_reason": "stop", "assistant_response": _presentation(plan, intent) if plan else ""}

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
        return "publish" if (state.get("cursor") or {}).get("plan_json") else "finish"

    def _after_publish(state: TaskGraphState) -> str:
        execution = _execution_of(state)
        if execution.task_context.mode == "copilot":
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
            deferred = set(cursor.get("verification_deferred") or ())
            if step_id and step_id not in deferred:
                return "verify"
            # That step's evidence was already deferred once this invocation
            # ran: asking again would loop, so the run ends with its durable
            # progress published and verification still owed.
            return "finish"
        return "finish"

    def _after_verify(state: TaskGraphState) -> str:
        return "step" if (state.get("cursor") or {}).get("advance") == "step" else "finish"

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
    builder.add_conditional_edges("propose", _after_propose, {"publish": "publish", "finish": "finish"})
    builder.add_conditional_edges("publish", _after_publish, {"gate": "gate", "switch": "switch"})
    builder.add_conditional_edges(
        "switch",
        _after_switch,
        {"step": "step", "verify": "verify", "wait": "wait", "propose": "propose", "finish": "finish"},
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


def _step_messages(invocation, execution, plan: TaskPlan, step_id: str, intent: str) -> tuple[ChatMessage, ...]:
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
