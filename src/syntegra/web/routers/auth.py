"""Sign-in endpoints. Exempt from Idempotency-Key, as OAuth token endpoints are (ADR-007)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field

from syntegra.core.errors import Unauthenticated
from syntegra.iam import auth as iam_auth
from syntegra.iam import oidc
from syntegra.iam import sso as iam_sso
from syntegra.iam.auth import TokenPair, TokenPolicy
from syntegra.iam.sso import SsoConfig
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


def _token_body(pair: TokenPair) -> dict[str, Any]:
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
    return body


def _token_response(pair: TokenPair) -> JSONResponse:
    return JSONResponse(_token_body(pair), headers=NO_STORE)


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


# --------------------------------------------------------------------------- SSO and MFA

sso_router = APIRouter(prefix="/v1/auth", tags=["auth: single sign-on and MFA"])


def get_sso(request: Request) -> SsoConfig:
    return request.app.state.sso  # type: ignore[no-any-return]


SsoDep = Annotated[SsoConfig, Depends(get_sso)]


class MfaChallengeOut(Model):
    mfa_required: bool = True
    mfa_token: str
    expires_in: int
    enrolment_required: bool
    next_action: str


class MfaCodeIn(Model):
    mfa_token: str = Field(min_length=20, max_length=4096)
    code: str = Field(min_length=6, max_length=32, examples=["492039", "k7pq-3mzt-w9xa"])


class MfaTokenIn(Model):
    mfa_token: str = Field(min_length=20, max_length=4096)


def _challenge_response(c: iam_sso.MfaChallenge) -> JSONResponse:
    from datetime import UTC, datetime

    body = MfaChallengeOut(
        mfa_token=c.token,
        expires_in=max(0, int((c.expires_at - datetime.now(UTC)).total_seconds())),
        enrolment_required=c.enrolment_required,
        next_action="Set up an authenticator app (POST /v1/auth/mfa/enrolment)."
        if c.enrolment_required
        else "Enter the code from your authenticator app (POST /v1/auth/mfa/verify).",
    ).model_dump(mode="json")
    return JSONResponse(body, headers=NO_STORE)


def _rejected(r: iam_sso.MfaRejected) -> Unauthenticated:
    if r.reason == "locked":
        return Unauthenticated(
            "too many wrong codes", next_action="Wait 15 minutes, then start signing in again."
        )
    return Unauthenticated("that code is not right", next_action="Check the code and try again.")


@sso_router.get("/providers", summary="Sign-in options for an organisation")
async def providers(
    request: Request, tenant: Annotated[str, Query(min_length=2, max_length=32)]
) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as session:
        found = await iam_sso.identity_providers(session, tenant)
    return {"tenant": tenant.upper(), "providers": [p.public() for p in found]}


@sso_router.get(
    "/oidc/{idp}/start",
    status_code=302,
    summary="Begin single sign-on: redirects to the identity provider",
)
async def sso_start(
    idp: str,
    request: Request,
    sso: SsoDep,
    tenant: Annotated[str, Query(min_length=2, max_length=32)],
    login_hint: Annotated[str | None, Query(max_length=254)] = None,
) -> RedirectResponse:
    async with request.app.state.sessionmaker() as session, session.begin():
        url = await iam_sso.start_sso(
            session, sso, tenant_code=tenant, idp_key=idp, login_hint=login_hint
        )
    return RedirectResponse(url, status_code=302, headers=NO_STORE)


@sso_router.get(
    "/oidc/callback",
    summary="Where the identity provider returns: tokens, or an MFA challenge",
    responses={200: {"description": "TokenOut, or MfaChallengeOut when MFA is needed"}},
)
async def sso_callback(
    request: Request,
    policy: PolicyDep,
    sso: SsoDep,
    state: Annotated[str, Query(min_length=16, max_length=256)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
) -> JSONResponse:
    if error or not code:
        # access_denied, login_required, …: the person cancelled or the provider refused.
        raise Unauthenticated(oidc.FAILED, next_action="Start signing in again.")
    ip, ua = _client(request)
    async with request.app.state.sessionmaker() as session, session.begin():
        result = await iam_sso.finish_sso(
            session, policy, sso, state=state, code=code, ip=ip, user_agent=ua
        )
    if isinstance(result, iam_sso.MfaChallenge):
        return _challenge_response(result)
    return _token_response(result)


@sso_router.post("/mfa/verify", response_model=TokenOut, summary="Finish sign-in with MFA")
async def mfa_verify(
    body: MfaCodeIn, request: Request, policy: PolicyDep, sso: SsoDep
) -> JSONResponse:
    ip, ua = _client(request)
    async with request.app.state.sessionmaker() as session, session.begin():
        result = await iam_sso.verify_mfa(
            session, policy, sso, mfa_token=body.mfa_token, code=body.code, ip=ip, user_agent=ua
        )
        # Committed first, so failed attempts count toward the lockout.
    if isinstance(result, iam_sso.MfaRejected):
        raise _rejected(result)
    return _token_response(result)


@sso_router.post("/mfa/enrolment", summary="Start setting up an authenticator app")
async def mfa_enrol(
    body: MfaTokenIn, request: Request, policy: PolicyDep, sso: SsoDep
) -> JSONResponse:
    async with request.app.state.sessionmaker() as session, session.begin():
        e = await iam_sso.start_enrolment(session, policy, sso, mfa_token=body.mfa_token)
    return JSONResponse(
        {
            "secret": e.secret,
            "otpauth_uri": e.otpauth_uri,
            "next_action": "Scan the QR code (or type the key) into your authenticator app, "
            "then send the 6-digit code it shows to /v1/auth/mfa/enrolment/confirm.",
        },
        headers=NO_STORE,
    )


@sso_router.post(
    "/mfa/enrolment/confirm",
    summary="Confirm the authenticator app; returns tokens and recovery codes",
)
async def mfa_enrol_confirm(
    body: MfaCodeIn, request: Request, policy: PolicyDep, sso: SsoDep
) -> JSONResponse:
    ip, ua = _client(request)
    async with request.app.state.sessionmaker() as session, session.begin():
        result = await iam_sso.confirm_enrolment(
            session, policy, sso, mfa_token=body.mfa_token, code=body.code, ip=ip, user_agent=ua
        )
    if isinstance(result, iam_sso.MfaRejected):
        raise _rejected(result)
    body_out = _token_body(result.tokens)
    body_out["recovery_codes"] = result.recovery_codes
    body_out["next_action"] = (
        "Save these recovery codes somewhere safe. Each works once if you lose your phone."
    )
    return JSONResponse(body_out, headers=NO_STORE)


@sso_router.post("/mfa/recovery-codes", summary="Replace your recovery codes")
async def mfa_recovery_codes(request: Request, policy: PolicyDep) -> JSONResponse:
    principal = authenticate(request, policy)
    async with request.app.state.sessionmaker() as session, session.begin():
        await iam_auth.check_session(session, principal.claims)
        codes = await iam_sso.new_recovery_codes(
            session, user_id=principal.user_id, amr=principal.claims.amr
        )
    return JSONResponse(
        {"recovery_codes": codes, "next_action": "Your old recovery codes no longer work."},
        headers=NO_STORE,
    )
