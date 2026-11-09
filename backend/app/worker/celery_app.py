"""Celery application (ADR-0010). Workers and Beat run as their own processes:

    uv run celery -A app.worker.celery_app worker --pool=solo -Q execution,maintenance  # Windows
    uv run celery -A app.worker.celery_app beat --schedule=<state file>

Messages are JSON and carry identifiers only. Delivery is at-least-once (late acknowledgement,
redelivery when a worker dies), so every task must be idempotent.
"""

from typing import Any

from celery import Celery, signals
from celery.schedules import crontab
from kombu.entity import Queue

from app.core.config import get_settings
from app.core.logging import configure_logging

EXECUTION_QUEUE = "execution"
MAINTENANCE_QUEUE = "maintenance"
RELAY_INTERVAL_SECONDS = 5.0

settings = get_settings()

celery_app = Celery("agentflow", broker=settings.celery_broker_url, include=["app.worker.tasks"])
celery_app.conf.update(
    # JSON only: never pickle, never accept other content types.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Results are not used: state lives in PostgreSQL.
    task_ignore_result=True,
    # At-least-once: acknowledge after the task finishes; redeliver if the worker process dies.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One message at a time per worker process, so long tasks don't hold others back.
    worker_prefetch_multiplier=1,
    # Redis redelivers unacknowledged messages after this many seconds; longer than any task.
    broker_transport_options={"visibility_timeout": 3600},
    broker_connection_retry_on_startup=True,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_default_queue=EXECUTION_QUEUE,
    task_queues=(Queue(EXECUTION_QUEUE), Queue(MAINTENANCE_QUEUE)),
    task_routes={
        "agentflow.execute_task": {"queue": EXECUTION_QUEUE},
        "agentflow.relay_outbox": {"queue": MAINTENANCE_QUEUE},
        "agentflow.cleanup": {"queue": MAINTENANCE_QUEUE},
    },
    beat_schedule={
        "relay-outbox": {"task": "agentflow.relay_outbox", "schedule": RELAY_INTERVAL_SECONDS},
        "cleanup-hourly": {"task": "agentflow.cleanup", "schedule": crontab(minute=0)},
    },
    timezone="UTC",
    enable_utc=True,
    worker_hijack_root_logger=False,
)


@signals.setup_logging.connect
def _use_application_logging(**_: Any) -> None:
    """JSON logs with secret redaction, as in the API (instead of Celery's own logging)."""
    configure_logging(settings)
