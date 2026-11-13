"""Outbox, relay, worker execution, worker permissions and cleanup against real PostgreSQL.

Worker-side code runs as the restricted worker role (`worker_factory`), as Celery workers do.
"""

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import AgentContext
from app.agents.contract import AgentSubmission, TaskContract
from app.agents.mock import MockAgent
from app.db.roles import WORKER_GRANTS
from app.db.sync_session import session_scope
from app.execution import cleanup, outbox, relay, runner
from app.models import (
    Agent,
    AgentExecution,
    AgentStatus,
    AgentVersion,
    AuthSession,
    AutonomyPolicy,
    ExecutionStatus,
    IdempotencyKey,
    InboxMessage,
    OutboxEvent,
    OutboxStatus,
    RefreshToken,
    SessionRevocationReason,
    TaskDependency,
    TaskStatus,
    User,
    Workflow,
    WorkflowStatus,
    WorkflowTask,
    Workspace,
    WorkspaceInvitation,
    WorkspaceRole,
)
from app.schemas.agents import AgentConfiguration
from app.workflows import engine
from app.workflows.state_machine import Actor

pytestmark = pytest.mark.integration

Factory = sessionmaker[Session]
CONFIG = {
    "schema_version": 1,
    "system_instruction_ref": "workspace://prompts/w/v1",
    "capabilities": ["report_writing"],
    "tools": [],
}


@dataclass(frozen=True)
class World:
    workspace_id: uuid.UUID
    workflow_id: uuid.UUID
    task_ids: list[uuid.UUID]
    agent_id: uuid.UUID
    version_id: uuid.UUID
    user_id: uuid.UUID


def _world(admin: Factory, *, tasks: int = 1, max_parallel: int = 3) -> World:
    """A RUNNING workflow with READY tasks and an ACTIVE agent, built through the engine."""
    with session_scope(admin) as s:
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="h")
        s.add(user)
        s.flush()
        workspace = Workspace(owner_id=user.id, name="W")
        s.add(workspace)
        s.flush()
        agent = Agent(workspace_id=workspace.id, name="Writer X", category="writing")
        s.add(agent)
        s.flush()
        version = AgentVersion(agent_id=agent.id, version=1, configuration=CONFIG)
        s.add(version)
        s.flush()
        agent.active_version_id, agent.status = version.id, AgentStatus.ACTIVE
        workflow = Workflow(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            prompt="p",
            autonomy_policy=AutonomyPolicy.AUTONOMOUS,
            max_parallel_tasks=max_parallel,
        )
        s.add(workflow)
        s.flush()
        rows = [
            WorkflowTask(
                workspace_id=workspace.id,
                workflow_id=workflow.id,
                title=f"t{i}",
                description="d",
                required_skills=["report_writing"],
            )
            for i in range(tasks)
        ]
        s.add_all(rows)
        s.flush()
        for target in (WorkflowStatus.PLANNING, WorkflowStatus.READY, WorkflowStatus.RUNNING):
            engine.transition_workflow(s, workflow, target, actor=Actor.USER)
        # These tests exercise task dispatch, so the prompt-analysis request that starting to
        # plan writes is removed; tests/integration/test_prompt_analysis.py covers it.
        s.execute(
            delete(OutboxEvent).where(
                OutboxEvent.workflow_id == workflow.id,
                OutboxEvent.event_type == outbox.WORKFLOW_ANALYZE,
            )
        )
        s.commit()
        return World(workspace.id, workflow.id, [t.id for t in rows], agent.id, version.id, user.id)


def _locked(s: Session, world: World) -> Workflow:
    workflow = s.execute(
        select(Workflow).where(Workflow.id == world.workflow_id).with_for_update()
    ).scalar_one()
    return workflow


def _task(s: Session, task_id: uuid.UUID) -> WorkflowTask:
    return s.execute(select(WorkflowTask).where(WorkflowTask.id == task_id)).scalar_one()


def _assign(admin: Factory, world: World, task_id: uuid.UUID, *, commit: bool = True) -> None:
    with session_scope(admin) as s:
        workflow = _locked(s, world)
        task = _task(s, task_id)
        task.assigned_agent_id, task.assigned_agent_version_id = world.agent_id, world.version_id
        engine.transition_task(s, workflow, task, TaskStatus.ASSIGNED, actor=Actor.USER)
        if commit:
            s.commit()
        else:
            s.rollback()


def _dispatch_events(admin: Factory, task_id: uuid.UUID | None = None) -> list[OutboxEvent]:
    with session_scope(admin) as s:
        statement = select(OutboxEvent).where(OutboxEvent.event_type == outbox.TASK_DISPATCH)
        if task_id is not None:
            statement = statement.where(OutboxEvent.task_id == task_id)
        return list(s.execute(statement.order_by(OutboxEvent.created_at)).scalars())


def _claim(worker: Factory, event: OutboxEvent) -> runner.Claim | runner.Skipped:
    with session_scope(worker) as s:
        return runner.claim(
            s,
            event_id=event.id,
            workspace_id=event.workspace_id,
            workflow_id=uuid.UUID(event.payload["workflow_id"]),
            task_id=uuid.UUID(event.payload["task_id"]),
            agent_version_id=uuid.UUID(event.payload["agent_version_id"]),
        )


def _finish(worker: Factory, claim: runner.Claim, failure: runner.Failure | None = None) -> str:
    outcome = failure or runner.run_agent(claim)
    with session_scope(worker) as s:
        return runner.finish(s, claim, outcome)


def _status(admin: Factory, task_id: uuid.UUID) -> TaskStatus:
    with session_scope(admin) as s:
        return _task(s, task_id).status


def _scalar(admin: Factory, statement: Any) -> Any:
    with session_scope(admin) as s:
        return s.execute(statement).scalar_one()


# --- transactional outbox ------------------------------------------------------------------------


def test_dispatch_event_commits_with_the_assignment(admin_factory: Factory) -> None:
    world = _world(admin_factory)

    _assign(admin_factory, world, world.task_ids[0])

    (event,) = _dispatch_events(admin_factory)
    assert event.status == OutboxStatus.PENDING and event.attempts == 0
    assert event.payload == {
        "workspace_id": str(world.workspace_id),
        "workflow_id": str(world.workflow_id),
        "task_id": str(world.task_ids[0]),
        "agent_version_id": str(world.version_id),
        "attempt_count": 0,
    }
    assert _status(admin_factory, world.task_ids[0]) == TaskStatus.ASSIGNED


def test_dispatch_event_rolls_back_with_the_assignment(admin_factory: Factory) -> None:
    world = _world(admin_factory)

    _assign(admin_factory, world, world.task_ids[0], commit=False)

    assert _dispatch_events(admin_factory) == []
    assert _status(admin_factory, world.task_ids[0]) == TaskStatus.READY


def _pending_events(admin: Factory, count: int) -> list[uuid.UUID]:
    world = _world(admin)
    with session_scope(admin) as s:
        ids = [
            outbox.enqueue(s, "test.event", workspace_id=world.workspace_id, payload={"n": i})
            for i in range(count)
        ]
        s.commit()
    return ids


def test_concurrent_relays_never_claim_the_same_event(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    ids = _pending_events(admin_factory, 30)
    barrier = threading.Barrier(3)
    claims: list[list[uuid.UUID]] = []

    def claim(owner: str) -> None:
        barrier.wait()
        with session_scope(worker_factory) as s:
            claimed = outbox.claim_batch(s, owner=owner, batch_size=12, lease_seconds=60)
        claims.append([event.id for event in claimed])

    threads = [threading.Thread(target=claim, args=(f"relay-{i}",)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    flat = [event_id for batch in claims for event_id in batch]
    assert len(flat) == len(set(flat)) == 30
    assert set(flat) == set(ids)


def test_expired_lease_is_reclaimed_and_the_old_owner_cannot_overwrite(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    (event_id,) = _pending_events(admin_factory, 1)
    with session_scope(worker_factory) as s:
        assert len(outbox.claim_batch(s, owner="relay-a", batch_size=10, lease_seconds=60)) == 1
    with session_scope(worker_factory) as s:  # relay-b: lease still valid, nothing to claim
        assert outbox.claim_batch(s, owner="relay-b", batch_size=10, lease_seconds=60) == []
    with session_scope(admin_factory) as s:  # relay-a "crashes"; its lease runs out
        s.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        s.commit()

    with session_scope(worker_factory) as s:
        (reclaimed,) = outbox.claim_batch(s, owner="relay-b", batch_size=10, lease_seconds=60)
    with session_scope(worker_factory) as s:
        stale_ack = outbox.mark_published(s, event_id, owner="relay-a")
    with session_scope(worker_factory) as s:
        fresh_ack = outbox.mark_published(s, event_id, owner="relay-b")

    assert reclaimed.id == event_id and reclaimed.attempts == 2
    assert stale_ack is False and fresh_ack is True
    assert _scalar(admin_factory, select(OutboxEvent.status).where(OutboxEvent.id == event_id)) == (
        OutboxStatus.PUBLISHED
    )


def test_publish_happens_outside_any_open_transaction(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    (event_id,) = _pending_events(admin_factory, 1)
    seen: dict[str, Any] = {}

    def publish(event: outbox.ClaimedEvent) -> None:
        # Another connection already sees the claim: it was committed before publishing.
        seen["status"] = _scalar(
            admin_factory, select(OutboxEvent.status).where(OutboxEvent.id == event.id)
        )
        seen["idle_in_transaction"] = _scalar(
            admin_factory,
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE usename = current_user AND state = 'idle in transaction' "
                "AND pid <> pg_backend_pid()"
            ),
        )

    report = relay.relay_once(
        worker_factory, publish, batch_size=10, lease_seconds=60, max_attempts=3
    )

    assert report.published == 1
    assert seen == {"status": OutboxStatus.PROCESSING, "idle_in_transaction": 0}
    event = _scalar(admin_factory, select(OutboxEvent).where(OutboxEvent.id == event_id))
    assert event.status == OutboxStatus.PUBLISHED and event.published_at is not None


def test_broker_failure_keeps_the_event_and_eventually_marks_it_failed(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    (event_id,) = _pending_events(admin_factory, 1)

    def broken(event: outbox.ClaimedEvent) -> None:
        raise ConnectionError("cannot reach redis://:s3cret-broker-pw@127.0.0.1:6379/1")

    first = relay.relay_once(
        worker_factory, broken, batch_size=10, lease_seconds=60, max_attempts=2
    )
    event = _scalar(admin_factory, select(OutboxEvent).where(OutboxEvent.id == event_id))
    assert first.failed == 1
    assert event.status == OutboxStatus.PENDING and event.attempts == 1
    assert event.available_at > datetime.now(UTC)  # backoff before the next attempt
    assert "s3cret-broker-pw" not in (event.last_error or "")
    assert event.last_error is not None and event.last_error.startswith("ConnectionError")

    assert (
        relay.relay_once(
            worker_factory, broken, batch_size=10, lease_seconds=60, max_attempts=2
        ).claimed
        == 0
    )
    with session_scope(admin_factory) as s:
        s.execute(update(OutboxEvent).values(available_at=func.now()))
        s.commit()
    relay.relay_once(worker_factory, broken, batch_size=10, lease_seconds=60, max_attempts=2)

    event = _scalar(admin_factory, select(OutboxEvent).where(OutboxEvent.id == event_id))
    assert event.status == OutboxStatus.FAILED and event.attempts == 2  # kept, not lost


# --- worker execution ----------------------------------------------------------------------------


def test_dispatched_task_runs_and_is_submitted_not_completed(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _assign(admin_factory, world, world.task_ids[0])
    (event,) = _dispatch_events(admin_factory)

    claim = _claim(worker_factory, event)
    assert isinstance(claim, runner.Claim)
    assert _status(admin_factory, world.task_ids[0]) == TaskStatus.IN_PROGRESS
    assert _finish(worker_factory, claim) == "submitted"

    assert _status(admin_factory, world.task_ids[0]) == TaskStatus.SUBMITTED
    execution = _scalar(admin_factory, select(AgentExecution))
    assert execution.status == ExecutionStatus.SUCCEEDED
    assert execution.agent_version_id == world.version_id and execution.attempt_number == 1
    assert execution.dispatch_event_id == event.id and execution.completed_at is not None


def test_duplicate_delivery_executes_once(admin_factory: Factory, worker_factory: Factory) -> None:
    world = _world(admin_factory)
    _assign(admin_factory, world, world.task_ids[0])
    (event,) = _dispatch_events(admin_factory)
    results: list[Any] = []
    barrier = threading.Barrier(4)

    def deliver() -> None:
        barrier.wait()
        results.append(_claim(worker_factory, event))

    threads = [threading.Thread(target=deliver) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(isinstance(r, runner.Claim) for r in results) == 1
    assert results.count(runner.Skipped.DUPLICATE) == 3
    assert _scalar(admin_factory, select(func.count()).select_from(AgentExecution)) == 1
    assert _scalar(admin_factory, select(func.count()).select_from(InboxMessage)) == 1
    claim = next(r for r in results if isinstance(r, runner.Claim))
    assert _finish(worker_factory, claim) == "submitted"
    assert _finish(worker_factory, claim) == "already_finished"  # redelivered finish is a no-op


def test_an_old_dispatch_for_a_task_that_moved_on_is_ignored(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _assign(admin_factory, world, world.task_ids[0])
    (event,) = _dispatch_events(admin_factory)
    claim = _claim(worker_factory, event)
    assert isinstance(claim, runner.Claim)

    copy = OutboxEvent(
        id=uuid.uuid4(),
        workspace_id=event.workspace_id,
        event_type=event.event_type,
        payload=event.payload,
    )
    assert _claim(worker_factory, copy) == runner.Skipped.STALE


def test_failed_attempts_are_kept_and_bounded(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    task_id = world.task_ids[0]
    failure = runner.Failure("EXECUTION_FAILED", "RuntimeError")

    for attempt in range(1, 4):
        if attempt == 1:
            _assign(admin_factory, world, task_id)
        else:  # retry: REWORK_REQUIRED -> ASSIGNED counts an attempt
            with session_scope(admin_factory) as s:
                workflow, task = _locked(s, world), _task(s, task_id)
                task.attempt_count += 1
                engine.transition_task(s, workflow, task, TaskStatus.ASSIGNED, actor=Actor.USER)
                s.commit()
        event = _dispatch_events(admin_factory, task_id)[-1]
        claim = _claim(worker_factory, event)
        assert isinstance(claim, runner.Claim) and claim.attempt_number == attempt
        outcome = _finish(worker_factory, claim, failure)
        assert outcome == ("rework_required" if attempt < 3 else "failed")

    with session_scope(admin_factory) as s:
        attempts = s.execute(
            select(AgentExecution.attempt_number, AgentExecution.status).order_by(
                AgentExecution.attempt_number
            )
        ).all()
        workflow_status = s.execute(select(Workflow.status)).scalar_one()
    assert attempts == [
        (1, ExecutionStatus.FAILED),
        (2, ExecutionStatus.FAILED),
        (3, ExecutionStatus.FAILED),
    ]
    assert workflow_status == WorkflowStatus.FAILED  # a required task failed for good


def test_parallel_limit_defers_and_a_finished_task_dispatches_the_next(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, tasks=2, max_parallel=1)
    first, second = world.task_ids
    _assign(admin_factory, world, first)
    _assign(admin_factory, world, second)
    first_event, second_event = _dispatch_events(admin_factory)

    running = _claim(worker_factory, first_event)
    assert isinstance(running, runner.Claim)
    assert _claim(worker_factory, second_event) == runner.Skipped.DEFERRED
    assert _status(admin_factory, second) == TaskStatus.ASSIGNED

    _finish(worker_factory, running)  # frees the slot: a new dispatch is written for `second`
    redispatch = _dispatch_events(admin_factory, second)[-1]
    assert redispatch.id != second_event.id
    assert isinstance(_claim(worker_factory, redispatch), runner.Claim)


def test_no_dispatch_while_paused_and_resume_redispatches(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _assign(admin_factory, world, world.task_ids[0])
    (event,) = _dispatch_events(admin_factory)
    with session_scope(admin_factory) as s:
        engine.transition_workflow(s, _locked(s, world), WorkflowStatus.PAUSED, actor=Actor.USER)
        s.commit()

    assert _claim(worker_factory, event) == runner.Skipped.NOT_RUNNING
    with session_scope(admin_factory) as s:
        engine.transition_workflow(s, _locked(s, world), WorkflowStatus.RUNNING, actor=Actor.USER)
        s.commit()

    assert isinstance(_claim(worker_factory, _dispatch_events(admin_factory)[-1]), runner.Claim)


def test_task_cancelled_while_running_records_a_cancelled_attempt(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, tasks=2)
    _assign(admin_factory, world, world.task_ids[0])
    claim = _claim(worker_factory, _dispatch_events(admin_factory)[0])
    assert isinstance(claim, runner.Claim)
    with session_scope(admin_factory) as s:
        engine.transition_task(
            s,
            _locked(s, world),
            _task(s, world.task_ids[0]),
            TaskStatus.CANCELLED,
            actor=Actor.USER,
        )
        s.commit()

    assert _finish(worker_factory, claim) == "cancelled"
    assert _scalar(admin_factory, select(AgentExecution.status)) == ExecutionStatus.CANCELLED


# --- worker sessions and permissions -------------------------------------------------------------


def test_worker_session_rolls_back_and_closes_on_error(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    (event_id,) = _pending_events(admin_factory, 1)
    engine_ = worker_factory.kw["bind"]

    with pytest.raises(RuntimeError), session_scope(worker_factory) as s:
        s.execute(update(OutboxEvent).values(last_error="should not persist"))
        raise RuntimeError("boom")

    assert engine_.pool.checkedout() == 0
    assert (
        _scalar(admin_factory, select(OutboxEvent.last_error).where(OutboxEvent.id == event_id))
        is None
    )


def test_worker_role_is_restricted(worker_factory: Factory, worker_role: tuple[str, str]) -> None:
    role, _ = worker_role
    with session_scope(worker_factory) as s:
        attributes = s.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = :r"
            ),
            {"r": role},
        ).one()
        owned = s.execute(
            text("SELECT count(*) FROM pg_tables WHERE tableowner = :r"), {"r": role}
        ).scalar_one()
        grants = s.execute(
            text(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = :r"
            ),
            {"r": role},
        ).all()

    assert tuple(attributes) == (False, False, False, False)
    assert owned == 0
    assert {(t, p) for t, p in grants} == {(t, p) for t, ps in WORKER_GRANTS.items() for p in ps}


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT password_hash FROM users",
        "DELETE FROM workflows",
        "UPDATE agent_versions SET version = version",
        "INSERT INTO workspace_members (workspace_id, user_id, role) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'OWNER')",
        "CREATE TABLE worker_was_here (id int)",
        "DROP TABLE outbox_events",
    ],
)
def test_worker_role_cannot_exceed_its_grants(worker_factory: Factory, statement: str) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied|must be owner"),
        session_scope(worker_factory) as s,
    ):
        s.execute(text(statement))


# --- cleanup -------------------------------------------------------------------------------------


def _seed_cleanup_data(admin: Factory) -> dict[str, uuid.UUID]:
    world = _world(admin)
    now = datetime.now(UTC)
    ids: dict[str, uuid.UUID] = {}
    with session_scope(admin) as s:

        def session_row(
            name: str, expires: datetime, reason: SessionRevocationReason | None = None
        ) -> AuthSession:
            row = AuthSession(
                user_id=world.user_id,
                csrf_token_hash="c" * 64,
                expires_at=expires,
                revoked_at=now if reason else None,
                revoked_reason=reason,
            )
            s.add(row)
            s.flush()
            ids[name] = row.id
            return row

        session_row("old_session", now - timedelta(days=40))
        session_row("live_session", now + timedelta(days=1))
        session_row(
            "reuse_session", now - timedelta(days=40), SessionRevocationReason.REFRESH_TOKEN_REUSE
        )
        session_row(
            "ancient_reuse_session",
            now - timedelta(days=100),
            SessionRevocationReason.REFRESH_TOKEN_REUSE,
        )
        for name, expires in (
            ("old_token", now - timedelta(days=40)),
            ("recent_token", now - timedelta(days=1)),
        ):
            token = RefreshToken(
                session_id=ids["live_session"],
                token_hash=uuid.uuid4().hex * 2,
                expires_at=expires,
            )
            s.add(token)
            s.flush()
            ids[name] = token.id
        for name, expires in (
            ("expired_key", now - timedelta(seconds=1)),
            ("live_key", now + timedelta(hours=1)),
        ):
            key = IdempotencyKey(
                workspace_id=world.workspace_id,
                operation="op",
                idempotency_key=name,
                request_hash="a" * 64,
                created_at=now - timedelta(days=2),
                expires_at=expires,
            )
            s.add(key)
            s.flush()
            ids[name] = key.id
        for name, expires, accepted in (
            ("old_invitation", now - timedelta(days=40), False),
            ("accepted_invitation", now - timedelta(days=400), True),
            ("pending_invitation", now - timedelta(days=1), False),
        ):
            invitation = WorkspaceInvitation(
                workspace_id=world.workspace_id,
                email=f"{name}@example.com",
                role=WorkspaceRole.MEMBER,
                token_hash=uuid.uuid4().hex * 2,
                invited_by_user_id=world.user_id,
                expires_at=expires,
                accepted_at=now if accepted else None,
                accepted_by_user_id=world.user_id if accepted else None,
            )
            s.add(invitation)
            s.flush()
            ids[name] = invitation.id
        s.commit()
    return ids


def _remaining(admin: Factory) -> set[uuid.UUID]:
    with session_scope(admin) as s:
        found: set[uuid.UUID] = set()
        for model in (AuthSession, RefreshToken, IdempotencyKey, WorkspaceInvitation):
            found |= set(s.execute(select(model.id)).scalars())
        return found


def test_cleanup_removes_only_expired_records_and_keeps_security_history(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    ids = _seed_cleanup_data(admin_factory)

    first = cleanup.run_cleanup(worker_factory, batch_size=100, max_batches=10)
    second = cleanup.run_cleanup(worker_factory, batch_size=100, max_batches=10)

    kept = _remaining(admin_factory)
    for name in (
        "live_session",
        "reuse_session",
        "recent_token",
        "live_key",
        "accepted_invitation",
        "pending_invitation",
    ):
        assert ids[name] in kept, name
    for name in (
        "old_session",
        "ancient_reuse_session",
        "old_token",
        "expired_key",
        "old_invitation",
    ):
        assert ids[name] not in kept, name
    assert first["auth_sessions"] == 2 and first["idempotency_keys"] == 1
    assert first["workspace_invitations"] == 1
    assert set(second.values()) == {0}  # idempotent


def test_cleanup_is_bounded_per_run(admin_factory: Factory, worker_factory: Factory) -> None:
    world = _world(admin_factory)
    with session_scope(admin_factory) as s:
        for i in range(7):
            s.add(
                IdempotencyKey(
                    workspace_id=world.workspace_id,
                    operation="op",
                    idempotency_key=f"k{i}",
                    request_hash="a" * 64,
                    created_at=datetime.now(UTC) - timedelta(days=2),
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
        s.commit()

    first = cleanup.run_cleanup(worker_factory, batch_size=2, max_batches=2)
    rest = cleanup.run_cleanup(worker_factory, batch_size=10, max_batches=1)

    assert first["idempotency_keys"] == 4 and rest["idempotency_keys"] == 3


def test_concurrent_cleanup_runs_do_not_conflict(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    ids = _seed_cleanup_data(admin_factory)
    errors: list[BaseException] = []
    totals: list[dict[str, int]] = []
    barrier = threading.Barrier(3)

    def run() -> None:
        barrier.wait()
        try:
            totals.append(cleanup.run_cleanup(worker_factory, batch_size=1, max_batches=10))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sum(t["auth_sessions"] for t in totals) == 2  # each row deleted exactly once
    assert ids["accepted_invitation"] in _remaining(admin_factory)


# --- end to end through Redis and a real Celery worker --------------------------------------------


def test_dispatch_reaches_a_real_worker_through_redis(
    admin_factory: Factory,
    worker_factory: Factory,
    api_settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from celery.contrib.testing.worker import start_worker

    from app.worker import tasks
    from app.worker.celery_app import EXECUTION_QUEUE, celery_app

    # A separate Redis database for this test's broker traffic; the worker uses the test DB.
    broker_url = api_settings.model_copy(update={"celery_broker_redis_db": 14}).celery_broker_url
    monkeypatch.setattr(celery_app.conf, "broker_url", broker_url)
    monkeypatch.setattr(tasks, "get_worker_session_factory", lambda: worker_factory)
    world = _world(admin_factory)
    _assign(admin_factory, world, world.task_ids[0])

    try:
        with start_worker(
            celery_app,
            pool="solo",
            perform_ping_check=False,
            shutdown_timeout=10,
            queues=[EXECUTION_QUEUE],
        ):
            report = relay.relay_once(
                worker_factory, tasks.publish_event, batch_size=10, lease_seconds=60, max_attempts=3
            )
            assert report.published == 1
            deadline = time.monotonic() + 30
            while _status(admin_factory, world.task_ids[0]) != TaskStatus.SUBMITTED:
                assert time.monotonic() < deadline, "worker did not process the dispatch"
                time.sleep(0.2)
    finally:
        with celery_app.connection_for_write() as connection:
            connection.default_channel.queue_purge(EXECUTION_QUEUE)

    execution = _scalar(admin_factory, select(AgentExecution))
    event = _scalar(admin_factory, select(OutboxEvent))
    assert execution.status == ExecutionStatus.SUCCEEDED
    assert execution.dispatch_event_id == event.id
    assert event.status == OutboxStatus.PUBLISHED


# --- task contract and submissions (Step 3.1, ADR-0018) ------------------------------------------


class _ForgetfulAgent(MockAgent):
    """Leaves out its claim for the first acceptance criterion."""

    def execute(self, contract: TaskContract, context: AgentContext) -> AgentSubmission:
        submission = super().execute(contract, context)
        return submission.model_copy(update={"criteria": submission.criteria[1:]})


def _describe_task(admin: Factory, world: World) -> None:
    with session_scope(admin) as s:
        task = _task(s, world.task_ids[0])
        task.description = "Context for the agent."
        task.deliverables = ["Report"]
        task.input_references = ["doc://brief"]
        task.expected_output_schema = {"type": "object"}
        task.acceptance_criteria = [
            {"id": "c1", "description": "Has a summary."},
            {"id": "c2", "description": "Cites sources."},
        ]
        s.add(
            TaskDependency(
                task_id=world.task_ids[0],
                depends_on_task_id=world.task_ids[1],
                workflow_id=world.workflow_id,
            )
        )
        s.commit()


def test_the_contract_is_built_from_platform_data_and_the_submission_is_stored(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, tasks=2)
    _describe_task(admin_factory, world)
    _assign(admin_factory, world, world.task_ids[0])
    (event,) = _dispatch_events(admin_factory)
    claim = _claim(worker_factory, event)
    assert isinstance(claim, runner.Claim)

    contract = runner.build_contract(claim, AgentConfiguration.model_validate(CONFIG))
    assert contract.task_id == world.task_ids[0] and contract.workflow_id == world.workflow_id
    assert (contract.objective, contract.context) == ("t0", "Context for the agent.")
    assert contract.deliverables == ["Report"] and contract.input_references == ["doc://brief"]
    assert contract.output_schema == {"type": "object"}
    assert [c.id for c in contract.acceptance_criteria] == ["c1", "c2"]
    assert contract.dependencies == [world.task_ids[1]]
    assert contract.retry_policy.attempt_number == 1 and contract.retry_policy.max_attempts == 3

    assert _finish(worker_factory, claim) == "submitted"
    execution = _scalar(admin_factory, select(AgentExecution))
    submission = AgentSubmission.model_validate(execution.submission)
    assert execution.result_summary == submission.summary
    assert [c.criterion_id for c in submission.criteria] == ["c1", "c2"]
    assert submission.outputs == {"Report": "Mock content for: Report"}
    assert _status(admin_factory, world.task_ids[0]) == TaskStatus.SUBMITTED


def test_an_invalid_submission_is_a_failed_attempt_without_a_stored_submission(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory, tasks=2)
    _describe_task(admin_factory, world)
    _assign(admin_factory, world, world.task_ids[0])
    (event,) = _dispatch_events(admin_factory)
    claim = _claim(worker_factory, event)
    assert isinstance(claim, runner.Claim)

    with pytest.raises(runner.InvalidSubmissionError) as error:
        runner.run_agent(claim, _ForgetfulAgent)
    assert error.value.issues == ["MISSING_CRITERION_CLAIM"]
    failure = runner.Failure("INVALID_SUBMISSION", f"Invalid submission: {error.value}")
    assert _finish(worker_factory, claim, failure) == "rework_required"

    execution = _scalar(admin_factory, select(AgentExecution))
    assert execution.status == ExecutionStatus.FAILED and execution.submission is None
    assert execution.error_code == "INVALID_SUBMISSION"
    assert execution.error_summary == "Invalid submission: MISSING_CRITERION_CLAIM"


def test_only_a_successful_attempt_can_hold_a_submission(
    admin_factory: Factory, worker_factory: Factory
) -> None:
    world = _world(admin_factory)
    _assign(admin_factory, world, world.task_ids[0])
    (event,) = _dispatch_events(admin_factory)
    claim = _claim(worker_factory, event)
    assert isinstance(claim, runner.Claim)
    _finish(worker_factory, claim, runner.Failure("EXECUTION_FAILED", "RuntimeError"))

    with session_scope(admin_factory) as s, pytest.raises(IntegrityError):
        s.execute(update(AgentExecution).values(submission={"summary": "forged"}))
        s.flush()
