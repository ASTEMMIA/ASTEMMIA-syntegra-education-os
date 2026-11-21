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
from syntegra.web.auth import build_keys, token_policy
from syntegra.web.routers import auth, governance, health, iam


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.env in ("staging", "prod") and settings.dev_auth:
        raise RuntimeError("dev login must be disabled outside dev/test (SYNTEGRA_DEV_AUTH=false)")
    keys = build_keys(settings)  # refuses to start without a real key outside dev/test

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
    app.state.token_policy = token_policy(settings, keys)
    problems.install(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(auth.jwks_router)
    if settings.dev_auth and settings.env in ("dev", "test"):
        app.include_router(auth.dev_router)
    app.include_router(governance.router)
    app.include_router(iam.router)
    return app


app = create_app
