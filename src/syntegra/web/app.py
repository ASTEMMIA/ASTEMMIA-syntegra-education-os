"""ASGI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import syntegra.governance.models
import syntegra.iam.models  # noqa: F401
from syntegra.platform.db import make_engine, make_sessionmaker
from syntegra.platform.settings import Settings, get_settings
from syntegra.web import problems
from syntegra.web.routers import governance, health, iam


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.env in ("staging", "prod") and settings.dev_auth:
        raise RuntimeError("dev header authentication must be disabled outside dev/test")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url, echo=settings.sql_echo)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Syntegra Education OS API",
        version="0.1.0",
        summary="Phase 0 foundations: governance, tenancy, IAM, audit, policy (CEDA EdTech-003).",
        lifespan=lifespan,
    )
    app.state.settings = settings
    problems.install(app)
    app.include_router(health.router)
    app.include_router(governance.router)
    app.include_router(iam.router)
    return app


app = create_app
