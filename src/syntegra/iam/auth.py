"""Sign-in, refresh rotation and sign-out services (CEDA §8.3).

Every sign-in method finishes in ``_open_session``, so the dev login and the future OIDC/MFA
login share issuance, rotation, revocation and audit.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import utcnow
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Unauthenticated
from syntegra.core.events import EventEnvelope
from syntegra.governance import api as governance
from syntegra.iam import events
from syntegra.iam.models import AppUser, AuthSession, RefreshToken
from syntegra.iam.tokens import (
    AccessClaims,
    KeySet,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
)
from syntegra.platform.outbox import publish

# One message for every sign-in failure, so the response never reveals which accounts exist.
SIGN_IN_FAILED = "sign-in failed"


@dataclass(frozen=True, slots=True)
class TokenPolicy:
    keys: KeySet
    issuer: str
    audience: str
    access_ttl: int
    refresh_ttl: int
    session_max: int


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    session_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RefreshRejected:
    reason: str  # unknown | expired | reused | revoked


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)


def _new_refresh(
    session: AsyncSession, auth: AuthSession, policy: TokenPolicy
) -> tuple[str, RefreshToken]:
    raw, digest = new_refresh_token()
    expires = min(utcnow() + timedelta(seconds=policy.refresh_ttl), auth.absolute_expires_at)
    row = RefreshToken(
        tenant_id=auth.tenant_id, session_id=auth.id, token_hash=digest, expires_at=expires
    )
    session.add(row)
    return raw, row


def _access(auth: AuthSession, policy: TokenPolicy) -> tuple[str, datetime]:
    return issue_access_token(
        policy.keys,
        issuer=policy.issuer,
        audience=policy.audience,
        user_id=auth.user_id,
        tenant_id=auth.tenant_id,
        session_id=auth.id,
        amr=auth.amr.split(),
        ttl_seconds=policy.access_ttl,
    )


async def _open_session(
    session: AsyncSession,
    policy: TokenPolicy,
    user: AppUser,
    *,
    method: str,
    amr: list[str],
    ip: str | None,
    user_agent: str | None,
) -> TokenPair:
    root = await governance.tenant_root_scope(session, user.tenant_id)
    ctx = RequestContext(user.tenant_id, root, user.id, purpose="sign-in", ip=ip)
    with use_context(ctx):
        auth = AuthSession(
            tenant_id=user.tenant_id,
            user_id=user.id,
            method=method,
            amr=" ".join(amr),
            absolute_expires_at=utcnow() + timedelta(seconds=policy.session_max),
            ip=ip,
            user_agent=(user_agent or "")[:255] or None,
        )
        session.add(auth)
        await session.flush()
        raw, row = _new_refresh(session, auth, policy)
        publish(
            session,
            EventEnvelope(
                event_type=events.SESSION_STARTED,
                tenant_id=user.tenant_id,
                scope_node_id=root,
                aggregate_type="app_user",
                aggregate_id=user.id,
                actor_id=user.id,
                correlation_id=ctx.correlation_id,
                payload={"session_id": str(auth.id), "method": method},
            ),
        )
        await session.flush()
    token, exp = _access(auth, policy)
    return TokenPair(token, exp, raw, _aware(row.expires_at), auth.id, user.id, user.tenant_id)


async def dev_login(
    session: AsyncSession,
    policy: TokenPolicy,
    *,
    tenant_code: str,
    login: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair:
    """Development sign-in: the login name is the only proof. The web layer exposes this
    only in dev/test, and the app refuses to start with it enabled anywhere else."""
    tenant = await governance.tenant_by_code(session, tenant_code)
    if tenant is None:
        raise Unauthenticated(SIGN_IN_FAILED, next_action="Check the organisation code and login.")
    user = (
        await session.execute(
            select(AppUser).where(
                AppUser.tenant_id == tenant.id, AppUser.login == login.strip().lower()
            )
        )
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        raise Unauthenticated(SIGN_IN_FAILED, next_action="Check the organisation code and login.")
    return await _open_session(
        session, policy, user, method="dev", amr=["dev"], ip=ip, user_agent=user_agent
    )


async def _revoke(session: AsyncSession, auth: AuthSession, reason: str) -> None:
    if auth.revoked_at is not None:
        return
    root = await governance.tenant_root_scope(session, auth.tenant_id)
    with use_context(RequestContext(auth.tenant_id, root, auth.user_id, purpose="sign-out")):
        auth.revoked_at = utcnow()
        auth.revoke_reason = reason
        publish(
            session,
            EventEnvelope(
                event_type=events.SESSION_REVOKED,
                tenant_id=auth.tenant_id,
                scope_node_id=root,
                aggregate_type="app_user",
                aggregate_id=auth.user_id,
                actor_id=auth.user_id,
                payload={"session_id": str(auth.id), "reason": reason},
            ),
        )
        await session.flush()


async def rotate_refresh(
    session: AsyncSession, policy: TokenPolicy, raw_refresh: str
) -> TokenPair | RefreshRejected:
    """Exchange a refresh token for a new pair. A spent token revokes the whole session.

    Returns ``RefreshRejected`` rather than raising, so the caller can commit a revocation
    before answering 401.
    """
    row = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == hash_refresh_token(raw_refresh))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return RefreshRejected("unknown")
    auth = await session.get(AuthSession, row.session_id, with_for_update=True)
    assert auth is not None
    now = utcnow()
    if auth.revoked_at is not None:
        return RefreshRejected("revoked")
    if row.used_at is not None:
        await _revoke(session, auth, "refresh_token_reuse")  # likely theft: kill the family
        return RefreshRejected("reused")
    if row.expires_at <= now or auth.absolute_expires_at <= now:
        return RefreshRejected("expired")
    user = await session.get(AppUser, auth.user_id)
    if user is None or user.status != "active":
        await _revoke(session, auth, "user_inactive")
        return RefreshRejected("revoked")
    root = await governance.tenant_root_scope(session, auth.tenant_id)
    with use_context(RequestContext(auth.tenant_id, root, auth.user_id, purpose="token-refresh")):
        raw, new_row = _new_refresh(session, auth, policy)
        await session.flush()
        row.used_at = now
        row.replaced_by_id = new_row.id
        await session.flush()
    token, exp = _access(auth, policy)
    return TokenPair(
        token, exp, raw, _aware(new_row.expires_at), auth.id, auth.user_id, auth.tenant_id
    )


async def sign_out(session: AsyncSession, session_id: uuid.UUID) -> None:
    auth = await session.get(AuthSession, session_id)
    if auth is not None:
        await _revoke(session, auth, "signed_out")


async def check_session(session: AsyncSession, claims: AccessClaims) -> None:
    """Per-request check: a revoked session or deactivated user stops working at once,
    without waiting for the access token to expire."""
    auth = await session.get(AuthSession, claims.session_id)
    now = utcnow()
    if (
        auth is None
        or auth.revoked_at is not None
        or auth.absolute_expires_at <= now
        or auth.user_id != claims.user_id
        or auth.tenant_id != claims.tenant_id
    ):
        raise Unauthenticated("your session has ended", next_action="Sign in again.")
    user = await session.get(AppUser, claims.user_id)
    if user is None or user.status != "active":
        raise Unauthenticated(
            "your account is not active", next_action="Contact your administrator."
        )


# --------------------------------------------------------------------------- email one-time code

EMAIL_CODE_MINUTES = 10
EMAIL_CODE_ATTEMPTS = 5
EMAIL_CODES_PER_HOUR = 5
EMAIL_CODE_ROLES = frozenset({"PAR"})  # who may sign in this way: parents and guardians
CODE_SENT = "If that address belongs to a parent account, a sign-in code is on its way."


def _code_hash(row_id: uuid.UUID, code: str) -> bytes:
    return hashlib.sha256(f"{row_id}:{code.strip()}".encode()).digest()


async def _email_user(session: AsyncSession, tenant_code: str, email: str) -> AppUser | None:
    """An active account whose every current role may sign in by email code."""
    from syntegra.iam.models import AccessGrant, Role

    tenant = await governance.tenant_by_code(session, tenant_code.strip().upper())
    if tenant is None:
        return None
    user = (
        await session.execute(
            select(AppUser).where(
                AppUser.tenant_id == tenant.id, AppUser.login == email.strip().lower()
            )
        )
    ).scalar_one_or_none()
    if user is None or user.status != "active":
        return None
    roles = {
        r
        for r in (
            await session.execute(
                select(Role.code)
                .join(AccessGrant, AccessGrant.role_id == Role.id)
                .where(AccessGrant.user_id == user.id, AccessGrant.revoked_at.is_(None))
            )
        ).scalars()
    }
    return user if roles and roles <= EMAIL_CODE_ROLES else None


async def request_email_code(
    session: AsyncSession, *, tenant_code: str, email: str, ip: str | None = None
) -> None:
    """Send a 6-digit code to a parent's email. Says nothing about whether the account exists,
    and sends at most a few codes an hour."""
    from syntegra.iam.models import EmailSignInCode
    from syntegra.platform.email import queue_email

    user = await _email_user(session, tenant_code, email)
    if user is None:
        return
    since = utcnow() - timedelta(hours=1)
    recent = (
        await session.execute(
            select(EmailSignInCode.id).where(
                EmailSignInCode.user_id == user.id, EmailSignInCode.created_at >= since
            )
        )
    ).all()
    if len(recent) >= EMAIL_CODES_PER_HOUR:
        return
    code = f"{secrets.randbelow(10**6):06d}"
    row = EmailSignInCode(
        tenant_id=user.tenant_id,
        user_id=user.id,
        code_hash=b"",
        expires_at=utcnow() + timedelta(minutes=EMAIL_CODE_MINUTES),
        ip=ip,
    )
    session.add(row)
    await session.flush()
    row.code_hash = _code_hash(row.id, code)
    queue_email(
        session,
        tenant_id=user.tenant_id,
        purpose="sign-in-code",
        to=user.login,
        subject="Your Syntegra sign-in code",  # not the code: subjects are kept after sending
        body=(
            f"Dear {user.display_name},\n\nYour code to sign in is {code}.\n"
            f"It works once, for {EMAIL_CODE_MINUTES} minutes.\n\n"
            "If you did not ask for it, ignore this email; nobody can sign in without the code."
        ),
    )


@dataclass(frozen=True, slots=True)
class CodeRejected:
    reason: str


async def verify_email_code(
    session: AsyncSession,
    policy: TokenPolicy,
    *,
    tenant_code: str,
    email: str,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair | CodeRejected:
    """Exchange a code for a session. A wrong code counts towards the limit; the caller
    commits before answering, so the count sticks."""
    from syntegra.iam.models import EmailSignInCode

    user = await _email_user(session, tenant_code, email)
    if user is None:
        return CodeRejected("that code is not right")
    row = (
        await session.execute(
            select(EmailSignInCode)
            .where(
                EmailSignInCode.user_id == user.id,
                EmailSignInCode.used_at.is_(None),
                EmailSignInCode.expires_at > utcnow(),
            )
            .order_by(EmailSignInCode.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return CodeRejected("the code has expired")
    if row.attempts >= EMAIL_CODE_ATTEMPTS:
        return CodeRejected("too many wrong codes")
    if not hmac.compare_digest(row.code_hash, _code_hash(row.id, code)):
        row.attempts += 1
        await session.flush()
        return CodeRejected("that code is not right")
    row.used_at = utcnow()
    return await _open_session(
        session, policy, user, method="email_code", amr=["otp"], ip=ip, user_agent=user_agent
    )
