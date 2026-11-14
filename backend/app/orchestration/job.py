"""The orchestration job (ADR-0025): reassesses one workflow when a `workflow.orchestrate`
outbox event is delivered.

One transaction, under the workflow row lock: record the delivery in the inbox (a redelivered
event stops here), snapshot the workflow and its active tasks, run the graph, and store its
assessment. Reading under the lock means every run reflects the committed state at that moment,
so the order in which deliveries arrive does not matter. The assessment never bumps `revision`:
it changes no plan, task or status.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.execution import outbox
from app.models import Workflow
from app.orchestration.graph import GRAPH_VERSION, TaskView, assess
from app.workflows import engine

CONSUMER = "orchestrate_workflow"


class Outcome(StrEnum):
    ASSESSED = "assessed"
    DUPLICATE = "duplicate"  # this delivery was already consumed
    MISSING = "missing"  # the workflow no longer exists


def run(
    session: Session, *, event_id: uuid.UUID, workspace_id: uuid.UUID, workflow_id: uuid.UUID
) -> Outcome:
    if not outbox.record_consumed(session, event_id=event_id, consumer=CONSUMER):
        session.rollback()
        return Outcome.DUPLICATE
    workflow = session.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workflow is None:
        session.commit()  # consumed: nothing to assess
        return Outcome.MISSING
    tasks = [
        TaskView(
            id=str(task.id),
            status=task.status.value,
            required=task.required,
            assigned=task.assigned_agent_version_id is not None,
        )
        for task in engine.list_tasks(session, workflow)
        if task.removed_at is None
    ]
    step = assess(workflow.status.value, tasks)
    workflow.next_step = {
        **step,
        "assessed_at": datetime.now(UTC).isoformat(),
        "graph_version": GRAPH_VERSION,
    }
    session.commit()
    return Outcome.ASSESSED
