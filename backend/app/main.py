import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.cookies import CSRF_HEADER
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.origin import OriginCheckMiddleware
from app.core.redis import create_redis_client
from app.core.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from app.db.session import (
    create_engine,
    create_session_factory,
    ensure_psycopg_compatible_event_loop,
)

logger = logging.getLogger("agentflow.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    ensure_psycopg_compatible_event_loop()
    engine = create_engine(settings)
    redis = create_redis_client(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.redis = redis
    logger.info("application started")
    try:
        yield
    finally:
        try:
            await redis.aclose()
        finally:
            await engine.dispose()
            logger.info("application stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory. Run with: uvicorn app.main:create_app --factory"""
    settings = settings or get_settings()
    configure_logging(settings)
    docs = settings.api_docs_enabled
    app = FastAPI(
        title="AgentFlow API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    # Added innermost first: requests pass RequestId -> CORS -> OriginCheck -> routes.
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.cors_allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER, REQUEST_ID_HEADER, "Idempotency-Key"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        max_age=600,
    )
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router)
    return app
