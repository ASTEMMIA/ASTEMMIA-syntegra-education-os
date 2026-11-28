"""Sign-in endpoints. Exempt from Idempotency-Key, as OAuth token endpoints are (ADR-007)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.core.errors import Unauthenticated
from syntegra.iam import auth as iam_auth
from syntegra.iam.auth import TokenPair, TokenPolicy
from syntegra.web.auth import authenticate
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1/auth", tags=["auth"])
# Dev login lives on its own router so the app can leave it out entirely outside dev/test.
dev_router = APIRouter(prefix="/v1/auth", tags=["auth"])
jwks_router = APIRouter(tags=["auth"])

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def get_policy(request: Request) -> TokenPolicy:
    return request.app.state.token_policy  # type: ignore[no-any-return]


PolicyDep = Annotated[TokenPolicy, Depends(get_policy)]


class DevLoginIn(Model):
    tenant_code: str = Field(min_length=2, max_length=32, examples=["INS-NG-000101"])
    login: str = Field(min_length=1, max_length=255, examples=["head@gcu.test"])


class RefreshIn(Model):
    refresh_token: str = Field(min_length=10, max_length=128)


class TokenOut(Model):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - RFC 6750 scheme name
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID


def _token_response(pair: TokenPair) -> JSONResponse:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    body: dict[str, Any] = TokenOut(
        access_token=pair.access_token,
        expires_in=max(0, int((pair.access_expires_at - now).total_seconds())),
        refresh_token=pair.refresh_token,
        refresh_expires_in=max(0, int((pair.refresh_expires_at - now).total_seconds())),
        session_id=pair.session_id,
        user_id=pair.user_id,
        tenant_id=pair.tenant_id,
    ).model_dump(mode="json")
    return JSONResponse(body, headers=NO_STORE)


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None, request.headers.get("User-Agent"))


@dev_router.post(
    "/dev-login", response_model=TokenOut, summary="Development sign-in (dev/test only)"
)
async def dev_login(body: DevLoginIn, request: Request, policy: PolicyDep) -> JSONResponse:
    ip, ua = _client(request)
    async with request.app.state.sessionmaker() as session, session.begin():
        pair = await iam_auth.dev_login(
            session, policy, tenant_code=body.tenant_code, login=body.login, ip=ip, user_agent=ua
        )
    return _token_response(pair)


@router.post("/refresh", response_model=TokenOut, summary="Rotate a refresh token")
async def refresh(body: RefreshIn, request: Request, policy: PolicyDep) -> JSONResponse:
    async with request.app.state.sessionmaker() as session, session.begin():
        result = await iam_auth.rotate_refresh(session, policy, body.refresh_token)
        # Committed first, so a reuse-triggered revocation sticks even though we answer 401.
    if isinstance(result, iam_auth.RefreshRejected):
        raise Unauthenticated("your session has ended", next_action="Sign in again.")
    return _token_response(result)


@router.post("/logout", status_code=204, summary="Sign out (revokes the session)")
async def logout(request: Request, policy: PolicyDep) -> JSONResponse:
    principal = authenticate(request, policy)
    async with request.app.state.sessionmaker() as session, session.begin():
        await iam_auth.sign_out(session, principal.session_id)
    return JSONResponse(None, status_code=204, headers=NO_STORE)


@jwks_router.get("/.well-known/jwks.json", summary="Public keys that verify access tokens")
async def jwks(policy: PolicyDep) -> JSONResponse:
    return JSONResponse(policy.keys.jwks(), headers={"Cache-Control": "public, max-age=300"})
