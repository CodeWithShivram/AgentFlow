"""Synchronous SQLAlchemy for Celery workers (OD-9, ADR-0010). The API keeps its async engine.

Each worker process builds its own engine on first use: connections are never shared across
processes (a prefork child that inherits a parent's engine discards it without closing the
parent's connections).
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

_process_state: dict[str, object] = {}


def create_worker_engine(settings: Settings, url: URL | None = None) -> Engine:
    return create_engine(
        url or settings.worker_database_url,
        pool_size=2,
        max_overflow=2,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "connect_timeout": settings.db_connect_timeout_seconds,
            "application_name": f"{settings.service_name}-worker",
        },
    )


def create_worker_session_factory(engine: Engine) -> sessionmaker[Session]:
    # Same session semantics as the API: no autoflush, objects usable after commit.
    return sessionmaker(engine, expire_on_commit=False, autoflush=False)


def get_worker_session_factory() -> sessionmaker[Session]:
    """The session factory of the current process, created lazily and per process."""
    pid = os.getpid()
    if _process_state.get("pid") != pid:
        inherited = _process_state.get("engine")
        if isinstance(inherited, Engine):
            inherited.dispose(close=False)  # do not close the parent's connections
        engine = create_worker_engine(get_settings())
        _process_state.update(pid=pid, engine=engine, factory=create_worker_session_factory(engine))
    factory = _process_state["factory"]
    if not isinstance(factory, sessionmaker):  # set together with pid above
        raise RuntimeError("worker session factory was not initialised")
    return factory


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One worker unit of work: rolls back on any exception and always closes the session."""
    session = factory()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
