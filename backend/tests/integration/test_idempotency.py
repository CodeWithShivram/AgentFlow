"""Idempotency service against real PostgreSQL: replay, conflicts, concurrency, atomicity."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import IdempotencyKey, IdempotencyStatus, User, Workflow, Workspace
from app.services.idempotency import (
    IdempotencyKeyReusedError,
    IdempotencyRequestInProgressError,
    IdempotencyService,
    InvalidIdempotencyKeyError,
    NewRequest,
    StoredResponse,
    UnsafeIdempotentResponseError,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

OPERATION = "workflows.create"
PAYLOAD = {"prompt": "Research AI support platforms", "settings": {"max_parallel_tasks": 3}}


@dataclass(frozen=True)
class Tenant:
    workspace_id: uuid.UUID
    user_id: uuid.UUID


@pytest.fixture
def service() -> IdempotencyService:
    return IdempotencyService(ttl=timedelta(hours=24))


async def _new_tenant(engine: AsyncEngine) -> Tenant:
    async with AsyncSession(engine) as session, session.begin():
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="hash")
        session.add(user)
        await session.flush()
        workspace = Workspace(owner_id=user.id, name="Tenant")
        session.add(workspace)
        await session.flush()
        return Tenant(workspace_id=workspace.id, user_id=user.id)


@pytest.fixture
async def tenant(engine: AsyncEngine) -> AsyncIterator[Tenant]:
    yield await _new_tenant(engine)


async def _create_workflow(
    engine: AsyncEngine,
    service: IdempotencyService,
    tenant: Tenant,
    *,
    key: str = "key-1",
    payload: Any = PAYLOAD,
    before_complete: asyncio.Event | None = None,
) -> NewRequest | StoredResponse:
    """The pattern a business service follows: one transaction for the operation and the key."""
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        outcome = await service.begin(
            session, workspace_id=tenant.workspace_id, operation=OPERATION, key=key, payload=payload
        )
        if isinstance(outcome, StoredResponse):
            return outcome
        workflow = Workflow(
            workspace_id=tenant.workspace_id,
            created_by_user_id=tenant.user_id,
            prompt=payload["prompt"],
            autonomy_policy="approval_required",
        )
        session.add(workflow)
        await session.flush()
        if before_complete is not None:
            await before_complete.wait()
        await service.complete(
            session,
            outcome,
            status_code=201,
            body={"workflow_id": str(workflow.id), "status": "PENDING"},
        )
        return outcome


async def _workflow_count(engine: AsyncEngine, tenant: Tenant) -> int:
    async with AsyncSession(engine) as session:
        statement = select(func.count()).where(Workflow.workspace_id == tenant.workspace_id)
        return (await session.execute(statement)).scalar_one()


async def _key_count(engine: AsyncEngine) -> int:
    async with AsyncSession(engine) as session:
        return (await session.execute(select(func.count(IdempotencyKey.id)))).scalar_one()


async def test_first_request_runs_and_retry_replays_stored_response(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    first = await _create_workflow(engine, service, tenant)
    # Same payload with different key order: still the same request.
    reordered = {"settings": {"max_parallel_tasks": 3}, "prompt": PAYLOAD["prompt"]}
    retry = await _create_workflow(engine, service, tenant, payload=reordered)

    assert isinstance(first, NewRequest)
    assert isinstance(retry, StoredResponse)
    assert retry.status_code == 201
    assert retry.body["status"] == "PENDING"
    assert await _workflow_count(engine, tenant) == 1


async def test_reusing_a_key_with_a_different_payload_is_a_conflict(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    await _create_workflow(engine, service, tenant)

    with pytest.raises(IdempotencyKeyReusedError) as exc_info:
        await _create_workflow(engine, service, tenant, payload={**PAYLOAD, "prompt": "other"})

    assert exc_info.value.status_code == 409
    assert await _workflow_count(engine, tenant) == 1


async def test_keys_are_independent_per_workspace(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    other = await _new_tenant(engine)

    assert isinstance(await _create_workflow(engine, service, tenant), NewRequest)
    assert isinstance(await _create_workflow(engine, service, other), NewRequest)
    assert await _workflow_count(engine, tenant) == 1
    assert await _workflow_count(engine, other) == 1


async def test_concurrent_duplicate_waits_then_replays(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    release_first = asyncio.Event()
    first = asyncio.create_task(
        _create_workflow(engine, service, tenant, before_complete=release_first)
    )
    await asyncio.sleep(0.3)  # first has claimed the key and is mid-operation
    second = asyncio.create_task(_create_workflow(engine, service, tenant))
    await asyncio.sleep(0.5)

    assert not second.done(), "duplicate must wait for the in-flight request"
    release_first.set()

    assert isinstance(await first, NewRequest)
    replay = await second
    assert isinstance(replay, StoredResponse)
    assert replay.status_code == 201
    assert await _workflow_count(engine, tenant) == 1


async def test_many_simultaneous_duplicates_execute_exactly_once(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    outcomes = await asyncio.gather(*(_create_workflow(engine, service, tenant) for _ in range(8)))

    assert sum(isinstance(o, NewRequest) for o in outcomes) == 1
    assert sum(isinstance(o, StoredResponse) for o in outcomes) == 7
    assert await _workflow_count(engine, tenant) == 1


async def test_rolled_back_request_releases_the_key(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    async with AsyncSession(engine) as session:
        outcome = await service.begin(
            session, workspace_id=tenant.workspace_id, operation=OPERATION, key="k", payload=PAYLOAD
        )
        assert isinstance(outcome, NewRequest)
        await session.rollback()

    assert await _key_count(engine) == 0
    assert isinstance(await _create_workflow(engine, service, tenant, key="k"), NewRequest)


async def test_waiting_duplicate_runs_if_the_first_request_rolls_back(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    claimed, release = asyncio.Event(), asyncio.Event()

    async def failing_first() -> None:
        async with AsyncSession(engine) as session:
            await session.begin()
            await service.begin(
                session,
                workspace_id=tenant.workspace_id,
                operation=OPERATION,
                key="k",
                payload=PAYLOAD,
            )
            claimed.set()
            await release.wait()
            await session.rollback()

    first = asyncio.create_task(failing_first())
    await claimed.wait()
    second = asyncio.create_task(_create_workflow(engine, service, tenant, key="k"))
    await asyncio.sleep(0.3)
    assert not second.done()
    release.set()
    await first

    assert isinstance(await second, NewRequest)
    assert await _workflow_count(engine, tenant) == 1


async def test_business_change_and_key_commit_atomically(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    with pytest.raises(IntegrityError):
        async with AsyncSession(engine) as session, session.begin():
            outcome = await service.begin(
                session,
                workspace_id=tenant.workspace_id,
                operation=OPERATION,
                key="k",
                payload=PAYLOAD,
            )
            assert isinstance(outcome, NewRequest)
            await service.complete(session, outcome, status_code=201, body={"ok": True})
            # A later failure in the same transaction (invalid budget) undoes everything.
            session.add(
                Workflow(
                    workspace_id=tenant.workspace_id,
                    created_by_user_id=tenant.user_id,
                    prompt="p",
                    autonomy_policy="approval_required",
                    budget_limit=-1,
                )
            )
            await session.flush()

    assert await _key_count(engine) == 0
    assert await _workflow_count(engine, tenant) == 0


async def test_recorded_client_error_is_replayed(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    body = {"error": {"code": "WORKFLOW_BUDGET_EXCEEDED", "message": "Budget limit reached."}}
    async with AsyncSession(engine) as session, session.begin():
        outcome = await service.begin(
            session, workspace_id=tenant.workspace_id, operation=OPERATION, key="k", payload=PAYLOAD
        )
        assert isinstance(outcome, NewRequest)
        await service.fail(session, outcome, status_code=422, body=body)

    replay = await _create_workflow(engine, service, tenant, key="k")

    assert replay == StoredResponse(status_code=422, body=body)
    async with AsyncSession(engine) as session:
        stored = (await session.execute(select(IdempotencyKey))).scalar_one()
    assert stored.status == IdempotencyStatus.FAILED


async def test_expired_key_can_be_used_again(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    await _create_workflow(engine, service, tenant, key="k")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day'"
            )
        )

    again = await _create_workflow(
        engine, service, tenant, key="k", payload={**PAYLOAD, "prompt": "new"}
    )

    assert isinstance(again, NewRequest)
    assert await _workflow_count(engine, tenant) == 2
    assert await _key_count(engine) == 1


async def test_purge_removes_only_expired_keys(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    for key in ("old-1", "old-2", "fresh"):
        await _create_workflow(engine, service, tenant, key=key)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day' WHERE idempotency_key LIKE 'old-%'"
            )
        )

    async with AsyncSession(engine) as session, session.begin():
        assert await service.purge_expired(session, batch_size=1) == 1
        assert await service.purge_expired(session) == 1
        assert await service.purge_expired(session) == 0

    async with AsyncSession(engine) as session:
        remaining = (await session.execute(select(IdempotencyKey.idempotency_key))).scalars().all()
    assert remaining == ["fresh"]


async def test_committed_processing_record_reports_in_progress(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    async with AsyncSession(engine) as session, session.begin():
        await service.begin(
            session, workspace_id=tenant.workspace_id, operation=OPERATION, key="k", payload=PAYLOAD
        )
        # Caller bug: committing without complete()/fail().

    with pytest.raises(IdempotencyRequestInProgressError):
        await _create_workflow(engine, service, tenant, key="k")


@pytest.mark.parametrize(
    "body",
    [
        {"workflow_id": "x", "access_token": "abc"},
        {"user": {"password": "p"}},
        {"items": [{"apiKey": "k"}]},
        {"headers": {"Authorization": "Bearer x"}},
    ],
)
async def test_responses_with_credentials_are_never_stored(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant, body: dict[str, Any]
) -> None:
    with pytest.raises(UnsafeIdempotentResponseError):
        async with AsyncSession(engine) as session, session.begin():
            outcome = await service.begin(
                session,
                workspace_id=tenant.workspace_id,
                operation=OPERATION,
                key="k",
                payload=PAYLOAD,
            )
            assert isinstance(outcome, NewRequest)
            await service.complete(session, outcome, status_code=201, body=body)

    assert await _key_count(engine) == 0


async def test_usage_counters_are_not_mistaken_for_credentials(
    engine: AsyncEngine, service: IdempotencyService, tenant: Tenant
) -> None:
    async with AsyncSession(engine) as session, session.begin():
        outcome = await service.begin(
            session, workspace_id=tenant.workspace_id, operation=OPERATION, key="k", payload=PAYLOAD
        )
        assert isinstance(outcome, NewRequest)
        await service.complete(
            session,
            outcome,
            status_code=200,
            body={"input_tokens": 10, "token_usage": {"total": 3}},
        )

    assert await _key_count(engine) == 1


@pytest.mark.parametrize("key", ["", "has space", "tab\tkey", "é", "x" * 256])
async def test_invalid_keys_are_rejected(
    session: AsyncSession, service: IdempotencyService, tenant: Tenant, key: str
) -> None:
    with pytest.raises(InvalidIdempotencyKeyError):
        await service.begin(
            session, workspace_id=tenant.workspace_id, operation=OPERATION, key=key, payload=PAYLOAD
        )


@pytest.mark.parametrize(
    ("method", "status_code"), [("complete", 500), ("complete", 404), ("fail", 200), ("fail", 503)]
)
async def test_status_codes_must_match_the_outcome(
    session: AsyncSession,
    service: IdempotencyService,
    tenant: Tenant,
    method: str,
    status_code: int,
) -> None:
    outcome = await service.begin(
        session, workspace_id=tenant.workspace_id, operation=OPERATION, key="k", payload=PAYLOAD
    )
    assert isinstance(outcome, NewRequest)

    with pytest.raises(ValueError):
        await getattr(service, method)(session, outcome, status_code=status_code, body={})
