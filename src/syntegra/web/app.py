"""ASGI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import syntegra.governance.models
import syntegra.hr.models
import syntegra.iam.models
import syntegra.institutions.models
import syntegra.mdm.models  # noqa: F401
from syntegra.iam.oidc import OidcClient
from syntegra.iam.sso import SsoConfig
from syntegra.platform.captcha import build_captcha
from syntegra.platform.crypto import build_field_crypto
from syntegra.platform.db import make_engine, make_sessionmaker
from syntegra.platform.email import build_sender
from syntegra.platform.settings import Settings, get_settings
from syntegra.web import problems
from syntegra.web.auth import build_keys, token_policy
from syntegra.web.routers import (
    addresses,
    auth,
    development,
    education_bodies,
    governance,
    health,
    iam,
    institutions,
    persons,
    private_schools,
    staff,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.env in ("staging", "prod") and settings.dev_auth:
        raise RuntimeError("dev login must be disabled outside dev/test (SYNTEGRA_DEV_AUTH=false)")
    keys = build_keys(settings)  # refuses to start without a real key outside dev/test
    field_crypto = build_field_crypto(
        env=settings.env,
        kek_b64=settings.field_kek_b64.get_secret_value() if settings.field_kek_b64 else None,
        previous_keks_b64=settings.field_previous_keks_b64,
        blind_index_key_b64=settings.blind_index_key_b64.get_secret_value()
        if settings.blind_index_key_b64
        else None,
    )

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
    app.state.field_crypto = field_crypto
    app.state.captcha = build_captcha(settings)  # refuses the dev stand-in outside dev/test
    app.state.email_sender = build_sender(settings)
    app.state.sso = SsoConfig(
        client=OidcClient(),
        cipher=field_crypto.cipher,
        redirect_uri=settings.redirect_uri,
        client_secrets={k: v.get_secret_value() for k, v in settings.oidc_client_secrets.items()},
        authenticator_label=settings.authenticator_label,
    )
    problems.install(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(auth.jwks_router)
    app.include_router(auth.sso_router)
    if settings.dev_auth and settings.env in ("dev", "test"):
        app.include_router(auth.dev_router)
    app.include_router(governance.router)
    app.include_router(iam.router)
    app.include_router(persons.router)
    app.include_router(institutions.router)
    app.include_router(education_bodies.router)
    app.include_router(addresses.router)
    app.include_router(staff.router)
    app.include_router(development.router)
    app.include_router(private_schools.public)
    app.include_router(private_schools.router)
    return app


app = create_app
