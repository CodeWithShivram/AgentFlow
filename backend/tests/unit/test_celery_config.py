"""Celery configuration, routing, schedule and message shape (no broker needed)."""

import json
import uuid
from typing import Any

import pytest
from celery.schedules import crontab
from pydantic import ValidationError

from app.execution import outbox
from app.worker import tasks
from app.worker.celery_app import EXECUTION_QUEUE, MAINTENANCE_QUEUE, celery_app
from tests.conftest import make_settings

conf = celery_app.conf


def test_only_json_is_produced_or_accepted() -> None:
    assert conf.task_serializer == "json"
    assert conf.result_serializer == "json"
    assert list(conf.accept_content) == ["json"]


def test_at_least_once_acknowledgement_and_prefetch() -> None:
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_ignore_result is True
    # Redis must not redeliver a message while its task can still be running.
    assert conf.broker_transport_options["visibility_timeout"] > tasks.EXECUTION_TIME_LIMIT


def test_queues_and_routes() -> None:
    assert {q.name for q in conf.task_queues} == {EXECUTION_QUEUE, MAINTENANCE_QUEUE}
    assert conf.task_routes == {
        "agentflow.execute_task": {"queue": EXECUTION_QUEUE},
        "agentflow.relay_outbox": {"queue": MAINTENANCE_QUEUE},
        "agentflow.cleanup": {"queue": MAINTENANCE_QUEUE},
    }
    assert {"agentflow.execute_task", "agentflow.relay_outbox", "agentflow.cleanup"} <= set(
        celery_app.tasks
    )


def test_beat_runs_cleanup_hourly_and_the_relay_every_few_seconds() -> None:
    schedule = conf.beat_schedule
    cleanup = schedule["cleanup-hourly"]
    assert cleanup["task"] == "agentflow.cleanup"
    assert isinstance(cleanup["schedule"], crontab)
    assert cleanup["schedule"].minute == {0}
    assert cleanup["schedule"].hour == set(range(24))
    assert schedule["relay-outbox"] == {"task": "agentflow.relay_outbox", "schedule": 5.0}


def test_execution_time_limits_follow_the_default_agent_timeout() -> None:
    task = celery_app.tasks["agentflow.execute_task"]
    assert task.soft_time_limit == 600
    assert task.time_limit > task.soft_time_limit


def test_broker_url_uses_its_own_redis_database() -> None:
    settings = make_settings(celery_broker_redis_db=3, redis_db=0)

    assert settings.celery_broker_url.endswith("/3")


def test_published_message_carries_identifiers_only(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    monkeypatch.setattr(celery_app, "send_task", lambda name, **kw: sent.update(name=name, **kw))
    event_id = uuid.uuid4()
    payload = {
        "workspace_id": str(uuid.uuid4()),
        "workflow_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "agent_version_id": str(uuid.uuid4()),
        "attempt_count": 0,
    }

    tasks.publish_event(outbox.ClaimedEvent(event_id, outbox.TASK_DISPATCH, payload, 1))

    assert sent["name"] == "agentflow.execute_task"
    assert sent["queue"] == EXECUTION_QUEUE
    assert sent["task_id"] == str(event_id)
    assert sent["retry"] is False
    assert sent["kwargs"] == {**payload, "event_id": str(event_id)}
    assert len(json.dumps(sent["kwargs"])) < 400  # small, identifiers only


def test_unknown_event_types_are_not_published() -> None:
    with pytest.raises(ValueError, match="no route"):
        tasks.publish_event(outbox.ClaimedEvent(uuid.uuid4(), "unknown.event", {}, 1))


def test_production_rejects_a_placeholder_worker_password() -> None:
    with pytest.raises(ValidationError, match="WORKER_DB_PASSWORD"):
        make_settings(
            app_env="production",
            cors_allowed_origins=["https://app.example.com"],
            worker_db_password="change-me",
        )


def test_worker_url_requires_a_password() -> None:
    with pytest.raises(ValueError, match="WORKER_DB_PASSWORD"):
        _ = make_settings().worker_database_url
    url = make_settings(worker_db_password="w" * 20).worker_database_url
    assert url.username == "agentflow_worker" and url.drivername == "postgresql+psycopg"
