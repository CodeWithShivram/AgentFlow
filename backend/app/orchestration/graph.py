"""The workflow orchestration graph (Phase 4, ADR-0025), built with LangGraph.

This first graph assesses; it does not act. Given a snapshot of a workflow and its tasks, it
counts what each task is waiting for and routes to exactly one "next step" node that explains
what has to happen next and who has to do it. The job that runs it (`app/orchestration/job.py`)
stores the result; any later node that changes state must go through the workflow engine
under the workflow lock, like every other status change (ADR-0009).

The state holds identifiers, statuses and counts only (no prompts or agent output), so it can
be checkpointed (Step 4.2). Routing is deterministic: the first matching rule wins.
"""

from collections import Counter
from functools import lru_cache
from typing import Any, Literal, NotRequired, TypedDict

import langsmith
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

GRAPH_VERSION = "orchestration/v1"

# LangGraph depends on LangSmith, which can send traces to an external service when tracing
# environment variables are set. Disabled here for the whole process, whatever the environment.
langsmith.configure(enabled=False)

NextStepKind = Literal[
    "start_planning",
    "complete_plan",
    "approve_plan",
    "start_workflow",
    "resume_workflow",
    "assign_agents",
    "running",
    "awaiting_verification",
    "blocked",
    "finished",
]


class TaskView(TypedDict):
    id: str
    status: str
    required: bool
    assigned: bool


class NextStep(TypedDict):
    kind: NextStepKind
    # Who has to act: people, agents (work in progress) or the platform (not built yet).
    actor: Literal["people", "agents", "platform", "none"]
    summary: str
    # The tasks this step is about (e.g. the ones to assign), at most 50.
    task_ids: list[str]


class OrchestrationState(TypedDict):
    workflow_status: str
    tasks: list[TaskView]
    counts: NotRequired[dict[str, int]]
    next_step: NotRequired[NextStep]


# Tasks people must (re)assign: ready but unassigned, failed attempts awaiting rework, escalated.
_NEEDS_ASSIGNMENT = {"READY", "REWORK_REQUIRED", "ESCALATED"}
_IN_FLIGHT = {"ASSIGNED", "QUEUED", "IN_PROGRESS"}
_AWAITING_VERIFICATION = {"SUBMITTED", "VERIFYING"}
_WORKFLOW_STEPS: dict[str, NextStep] = {
    "PENDING": {
        "kind": "start_planning",
        "actor": "people",
        "summary": "Start planning to analyse the request and draft a plan.",
        "task_ids": [],
    },
    "PLANNING": {
        "kind": "complete_plan",
        "actor": "people",
        "summary": "Review the plan draft or add tasks, then submit the plan.",
        "task_ids": [],
    },
    "AWAITING_APPROVAL": {
        "kind": "approve_plan",
        "actor": "people",
        "summary": "An admin must approve the plan before it can run.",
        "task_ids": [],
    },
    "READY": {
        "kind": "start_workflow",
        "actor": "people",
        "summary": "Start the workflow to begin work.",
        "task_ids": [],
    },
    "PAUSED": {
        "kind": "resume_workflow",
        "actor": "people",
        "summary": "Resume the workflow to continue work.",
        "task_ids": [],
    },
}
_FINISHED = {"COMPLETED", "FAILED", "CANCELLED"}


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _needs_assignment(task: TaskView) -> bool:
    """READY and unassigned, or failed/escalated (those keep their previous assignment)."""
    return task["status"] in _NEEDS_ASSIGNMENT and not (
        task["status"] == "READY" and task["assigned"]
    )


def _ids(state: OrchestrationState, statuses: set[str]) -> list[str]:
    return [t["id"] for t in state["tasks"] if t["status"] in statuses][:50]


def count_tasks(state: OrchestrationState) -> dict[str, Any]:
    """Counts what the tasks are waiting for."""
    tasks = state["tasks"]
    statuses = Counter(t["status"] for t in tasks)
    return {
        "counts": {
            "assign": sum(1 for t in tasks if _needs_assignment(t)),
            "in_flight": sum(statuses[s] for s in _IN_FLIGHT),
            "awaiting_verification": sum(statuses[s] for s in _AWAITING_VERIFICATION),
            "blocked": statuses["BLOCKED"] + statuses["WAITING_APPROVAL"],
            "pending": statuses["PENDING"],
        }
    }


def route(state: OrchestrationState) -> str:
    status = state["workflow_status"]
    if status in _FINISHED:
        return "finished"
    if status in _WORKFLOW_STEPS:
        return "workflow_step"
    counts = state["counts"]
    if counts["assign"]:
        return "assign_agents"
    if counts["in_flight"]:
        return "running"
    if counts["awaiting_verification"]:
        return "awaiting_verification"
    return "blocked"


def finished(state: OrchestrationState) -> dict[str, Any]:
    status = state["workflow_status"].lower()
    return {
        "next_step": NextStep(
            kind="finished", actor="none", summary=f"The workflow is {status}.", task_ids=[]
        )
    }


def workflow_step(state: OrchestrationState) -> dict[str, Any]:
    return {"next_step": dict(_WORKFLOW_STEPS[state["workflow_status"]])}


def _also(counts: dict[str, int]) -> str:
    notes = []
    if counts["in_flight"]:
        notes.append(f"{_plural(counts['in_flight'], 'task')} running")
    if counts["awaiting_verification"]:
        notes.append(f"{counts['awaiting_verification']} awaiting verification")
    return f" ({', '.join(notes)})" if notes else ""


def assign_agents(state: OrchestrationState) -> dict[str, Any]:
    counts = state["counts"]
    return {
        "next_step": NextStep(
            kind="assign_agents",
            actor="people",
            summary=f"Assign agents to {_plural(counts['assign'], 'task')}{_also(counts)}.",
            task_ids=[t["id"] for t in state["tasks"] if _needs_assignment(t)][:50],
        )
    }


def running(state: OrchestrationState) -> dict[str, Any]:
    counts = state["counts"]
    waiting = counts["awaiting_verification"]
    extra = f"; {waiting} awaiting verification" if waiting else ""
    return {
        "next_step": NextStep(
            kind="running",
            actor="agents",
            summary=f"Agents are working on {_plural(counts['in_flight'], 'task')}{extra}.",
            task_ids=_ids(state, _IN_FLIGHT),
        )
    }


def awaiting_verification(state: OrchestrationState) -> dict[str, Any]:
    count = state["counts"]["awaiting_verification"]
    return {
        "next_step": NextStep(
            kind="awaiting_verification",
            actor="platform",
            summary=(
                f"{_plural(count, 'task')} submitted and awaiting verification, which arrives in "
                "Phase 5."
            ),
            task_ids=_ids(state, _AWAITING_VERIFICATION),
        )
    }


def blocked(state: OrchestrationState) -> dict[str, Any]:
    counts = state["counts"]
    if counts["blocked"]:
        summary = f"{_plural(counts['blocked'], 'task')} blocked; check their dependencies."
    elif counts["pending"]:
        summary = f"{_plural(counts['pending'], 'task')} waiting for their dependencies."
    else:
        summary = "Nothing can progress; add or reassign tasks."
    return {
        "next_step": NextStep(
            kind="blocked",
            actor="people",
            summary=summary,
            task_ids=_ids(state, {"BLOCKED", "WAITING_APPROVAL", "PENDING"}),
        )
    }


_TERMINALS = {
    "finished": finished,
    "workflow_step": workflow_step,
    "assign_agents": assign_agents,
    "running": running,
    "awaiting_verification": awaiting_verification,
    "blocked": blocked,
}


@lru_cache(maxsize=1)
def build_graph() -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(OrchestrationState)
    graph.add_node("count_tasks", count_tasks)
    for name, node in _TERMINALS.items():
        graph.add_node(name, node)
        graph.add_edge(name, END)
    graph.add_edge(START, "count_tasks")
    graph.add_conditional_edges("count_tasks", route, {name: name for name in _TERMINALS})
    return graph.compile()


def assess(workflow_status: str, tasks: list[TaskView]) -> NextStep:
    """Runs the graph on a snapshot and returns the next step."""
    result = build_graph().invoke({"workflow_status": workflow_status, "tasks": tasks})
    next_step: NextStep = result["next_step"]
    return next_step
