"""Single sign-on and multi-factor authentication (CEDA §8.3, ADR-016).

    start_sso ──► identity provider ──► finish_sso ──┬─► tokens (no MFA needed, or the
                                                     │    provider's own MFA is trusted)
                                                     └─► MFA challenge ──► verify_mfa ──► tokens
                                                              │
                                                              └─► first time: start_enrolment
                                                                  ──► confirm_enrolment ──► tokens
                                                                      (+ ten recovery codes)

Accounts are never created by signing in: an administrator (or an approved registration) creates
the user first, and single sign-on only proves who they are. Every route ends in the same
``_open_session`` as the dev login, so tokens, rotation, revocation and audit are shared.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import utcnow
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, NotFound, Unauthenticated
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import uuid7
from syntegra.governance import api as governance
from syntegra.iam import events, mfa, oidc
from syntegra.iam.auth import SIGN_IN_FAILED, TokenPair, TokenPolicy, _open_session
from syntegra.iam.models import AppUser, MfaCredential, OidcFlow, RecoveryCode
from syntegra.iam.tokens import issue_mfa_token, verify_mfa_token
from syntegra.platform.crypto import FieldCipher
from syntegra.platform.outbox import publish

PROVIDERS_KEY = "auth.identity_providers"
FLOW_TTL = timedelta(minutes=10)
CHALLENGE_TTL_SECONDS = 300
MAX_FAILED = 5
LOCKOUT = timedelta(minutes=15)

_MFA_TABLE, _MFA_COLUMN = "iam.mfa_credential", "secret_enc"
_FLOW_TABLE, _FLOW_COLUMN = "iam.oidc_flow", "verifier_enc"


@dataclass(frozen=True, slots=True)
class SsoConfig:
    client: oidc.OidcClient
    cipher: FieldCipher
    redirect_uri: str
    client_secrets: Mapping[str, str] = field(default_factory=dict)
    authenticator_label: str = "Syntegra Education"


@dataclass(frozen=True, slots=True)
class MfaChallenge:
    """The first factor passed; a second is needed before tokens are issued."""

    token: str
    expires_at: datetime
    enrolment_required: bool


@dataclass(frozen=True, slots=True)
class MfaRejected:
    """Returned rather than raised, so the failed-attempt count is committed before the 401."""

    reason: str  # wrong_code | locked


@dataclass(frozen=True, slots=True)
class Enrolment:
    secret: str
    otpauth_uri: str


@dataclass(frozen=True, slots=True)
class Enrolled:
    tokens: TokenPair
    recovery_codes: list[str]


# --------------------------------------------------------------------------- providers


async def _tenant(session: AsyncSession, tenant_code: str) -> governance.TenantView:
    tenant = await governance.tenant_by_code(session, tenant_code.strip().upper())
    if tenant is None:
        raise Unauthenticated(SIGN_IN_FAILED, next_action="Check the organisation code.")
    return tenant


async def _providers(
    session: AsyncSession, tenant_id: uuid.UUID, root: uuid.UUID
) -> list[oidc.IdentityProvider]:
    # Inherited: a state's provider serves every school tenant beneath the state.
    with use_context(RequestContext(tenant_id, root, None, system=True, purpose="sign-in")):
        return oidc.parse_providers(await governance.resolve_config(session, root, PROVIDERS_KEY))


async def identity_providers(
    session: AsyncSession, tenant_code: str
) -> list[oidc.IdentityProvider]:
    tenant = await _tenant(session, tenant_code)
    return await _providers(session, tenant.id, tenant.root_scope_node_id)


async def set_identity_providers(
    session: AsyncSession, tenant_code: str, value: list[dict[str, object]]
) -> list[oidc.IdentityProvider]:
    """Replace an organisation's sign-in options (checked first). Schools and other tenants
    beneath it inherit them unless they set their own."""
    providers = oidc.parse_providers(value)
    tenant = await governance.tenant_by_code(session, tenant_code.strip().upper())
    if tenant is None:
        raise NotFound(f"{tenant_code} is not on the system")
    root = tenant.root_scope_node_id
    with use_context(RequestContext(tenant.id, root, None, system=True, purpose="configure-sso")):
        await governance.set_config(session, scope_node_id=root, key=PROVIDERS_KEY, value=value)
    return providers


def _pick(providers: list[oidc.IdentityProvider], key: str) -> oidc.IdentityProvider:
    for p in providers:
        if p.key == key.lower():
            return p
    raise NotFound(
        "this sign-in option is not available for the organisation",
        next_action="Choose one of the options on the sign-in page.",
    )


# --------------------------------------------------------------------------- SSO


async def start_sso(
    session: AsyncSession,
    sso: SsoConfig,
    *,
    tenant_code: str,
    idp_key: str,
    login_hint: str | None = None,
) -> str:
    """Record the attempt and return the identity provider URL to send the browser to."""
    tenant = await _tenant(session, tenant_code)
    idp = _pick(await _providers(session, tenant.id, tenant.root_scope_node_id), idp_key)
    discovery = await sso.client.discover(idp)
    state, nonce = oidc.new_state(), oidc.new_nonce()
    verifier, challenge = oidc.new_pkce()
    flow_id = uuid7()
    session.add(
        OidcFlow(
            id=flow_id,
            tenant_id=tenant.id,
            idp_key=idp.key,
            state_hash=oidc.hash_state(state),
            nonce=nonce,
            verifier_enc=sso.cipher.encrypt(
                verifier,
                tenant_id=tenant.id,
                table=_FLOW_TABLE,
                column=_FLOW_COLUMN,
                row_id=flow_id,
            ),
            expires_at=utcnow() + FLOW_TTL,
        )
    )
    await session.flush()
    return sso.client.authorization_url(
        idp,
        discovery,
        redirect_uri=sso.redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        login_hint=login_hint,
    )


async def finish_sso(
    session: AsyncSession,
    policy: TokenPolicy,
    sso: SsoConfig,
    *,
    state: str,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair | MfaChallenge:
    """Handle the identity provider's redirect back: check everything, then sign in or ask
    for the second factor."""
    again = "Start signing in again."
    flow = (
        await session.execute(
            select(OidcFlow).where(OidcFlow.state_hash == oidc.hash_state(state)).with_for_update()
        )
    ).scalar_one_or_none()
    now = utcnow()
    if flow is None or flow.used_at is not None or flow.expires_at <= now:
        raise Unauthenticated(oidc.FAILED, next_action=again)
    flow.used_at = now  # one redirect, one use
    root = await governance.tenant_root_scope(session, flow.tenant_id)
    idp = _pick(await _providers(session, flow.tenant_id, root), flow.idp_key)
    verifier = sso.cipher.decrypt(
        flow.verifier_enc,
        tenant_id=flow.tenant_id,
        table=_FLOW_TABLE,
        column=_FLOW_COLUMN,
        row_id=flow.id,
    )
    discovery = await sso.client.discover(idp)
    secret = sso.client_secrets.get(idp.client_secret_ref) if idp.client_secret_ref else None
    if idp.client_secret_ref and secret is None:
        raise Unauthenticated(
            oidc.FAILED, next_action="Single sign-on is not fully set up. Tell your administrator."
        )
    id_token = await sso.client.exchange_code(
        idp,
        discovery,
        code=code,
        code_verifier=verifier,
        redirect_uri=sso.redirect_uri,
        client_secret=secret,
    )
    claims = await sso.client.validate_id_token(idp, discovery, id_token, nonce=flow.nonce)
    user = await _link_user(session, idp, claims, flow.tenant_id, root)
    await session.flush()
    if not user.mfa_required:
        return await _open_session(
            session, policy, user, method="oidc", amr=["fed"], ip=ip, user_agent=user_agent
        )
    if idp.trust_idp_mfa and claims.provider_mfa:
        flow.completed_at = now
        return await _open_session(
            session, policy, user, method="oidc", amr=["fed", "mfa"], ip=ip, user_agent=user_agent
        )
    return await _challenge(session, policy, user, flow)


async def _link_user(
    session: AsyncSession,
    idp: oidc.IdentityProvider,
    claims: oidc.IdTokenClaims,
    tenant_id: uuid.UUID,
    root: uuid.UUID,
) -> AppUser:
    no_account = Unauthenticated(
        SIGN_IN_FAILED,
        next_action="Your account is not set up for this organisation. Ask your administrator.",
    )
    key = oidc.subject_key(claims.issuer, claims.subject)
    user = (
        await session.execute(
            select(AppUser).where(AppUser.tenant_id == tenant_id, AppUser.idp_subject == key)
        )
    ).scalar_one_or_none()
    if user is None and idp.link_by_email and claims.email:
        domain = claims.email.rpartition("@")[2]
        # With email_domains set, the provider must be authoritative for the domain (e.g. the
        # state's own Microsoft 365). Otherwise the provider must say it verified the address.
        trusted = domain in idp.email_domains if idp.email_domains else claims.email_verified
        if trusted:
            user = (
                await session.execute(
                    select(AppUser).where(
                        AppUser.tenant_id == tenant_id,
                        AppUser.login == claims.email,
                        AppUser.idp_subject.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if user is not None:
                with use_context(RequestContext(tenant_id, root, user.id, purpose="sign-in")):
                    user.idp_subject = key
                    publish(
                        session,
                        EventEnvelope(
                            event_type=events.SSO_LINKED,
                            tenant_id=tenant_id,
                            scope_node_id=root,
                            aggregate_type="app_user",
                            aggregate_id=user.id,
                            actor_id=user.id,
                            payload={"idp": idp.key},
                        ),
                    )
                    await session.flush()
    if user is None or user.status != "active":
        raise no_account
    return user


# --------------------------------------------------------------------------- MFA


async def _credential(session: AsyncSession, user_id: uuid.UUID) -> MfaCredential | None:
    return (
        await session.execute(
            select(MfaCredential)
            .where(MfaCredential.user_id == user_id, MfaCredential.kind == "totp")
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _challenge(
    session: AsyncSession, policy: TokenPolicy, user: AppUser, flow: OidcFlow
) -> MfaChallenge:
    cred = await _credential(session, user.id)
    token, exp = issue_mfa_token(
        policy.keys,
        issuer=policy.issuer,
        user_id=user.id,
        tenant_id=user.tenant_id,
        flow_id=flow.id,
        amr=["fed"],
        ttl_seconds=CHALLENGE_TTL_SECONDS,
    )
    return MfaChallenge(token, exp, enrolment_required=cred is None or cred.confirmed_at is None)


async def _from_challenge(
    session: AsyncSession, policy: TokenPolicy, mfa_token: str
) -> tuple[AppUser, OidcFlow, uuid.UUID]:
    claims = verify_mfa_token(policy.keys, mfa_token, issuer=policy.issuer)
    expired = Unauthenticated(
        "this sign-in attempt has expired", next_action="Start signing in again."
    )
    flow = await session.get(OidcFlow, claims.flow_id, with_for_update=True)
    if flow is None or flow.completed_at is not None or flow.tenant_id != claims.tenant_id:
        raise expired
    user = await session.get(AppUser, claims.user_id)
    if user is None or user.status != "active" or user.tenant_id != claims.tenant_id:
        raise expired
    root = await governance.tenant_root_scope(session, user.tenant_id)
    return user, flow, root


def _ctx(user: AppUser, root: uuid.UUID, purpose: str) -> RequestContext:
    return RequestContext(user.tenant_id, root, user.id, purpose=purpose)


def _secret(sso: SsoConfig, cred: MfaCredential) -> str:
    return sso.cipher.decrypt(
        cred.secret_enc,
        tenant_id=cred.tenant_id,
        table=_MFA_TABLE,
        column=_MFA_COLUMN,
        row_id=cred.id,
    )


def _locked(cred: MfaCredential) -> bool:
    return cred.locked_until is not None and cred.locked_until > utcnow()


def _failed(cred: MfaCredential) -> MfaRejected:
    cred.failed_attempts += 1
    if cred.failed_attempts >= MAX_FAILED:
        cred.failed_attempts = 0
        cred.locked_until = utcnow() + LOCKOUT
        return MfaRejected("locked")
    return MfaRejected("wrong_code")


async def verify_mfa(
    session: AsyncSession,
    policy: TokenPolicy,
    sso: SsoConfig,
    *,
    mfa_token: str,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> TokenPair | MfaRejected:
    """Finish sign-in with a code from the authenticator app, or a recovery code."""
    user, flow, root = await _from_challenge(session, policy, mfa_token)
    cred = await _credential(session, user.id)
    if cred is None or cred.confirmed_at is None:
        raise Conflict(
            "no authenticator app is set up yet", next_action="Set up your authenticator app."
        )
    if _locked(cred):
        return MfaRejected("locked")
    with use_context(_ctx(user, root, "sign-in")):
        amr: list[str] | None = None
        if mfa.looks_like_totp(code):
            matched = mfa.verify_totp(_secret(sso, cred), code, last_step=cred.last_step)
            if matched is not None:
                cred.last_step = matched
                amr = ["fed", "otp", "mfa"]
        else:
            rc = (
                await session.execute(
                    select(RecoveryCode)
                    .where(
                        RecoveryCode.user_id == user.id,
                        RecoveryCode.code_hash == mfa.hash_recovery_code(code),
                        RecoveryCode.used_at.is_(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if rc is not None:
                rc.used_at = utcnow()
                amr = ["fed", "rec", "mfa"]
        if amr is None:
            out = _failed(cred)
            await session.flush()
            return out
        cred.failed_attempts = 0
        cred.last_used_at = utcnow()
        flow.completed_at = utcnow()
        await session.flush()
    return await _open_session(
        session, policy, user, method="oidc", amr=amr, ip=ip, user_agent=user_agent
    )


async def start_enrolment(
    session: AsyncSession, policy: TokenPolicy, sso: SsoConfig, *, mfa_token: str
) -> Enrolment:
    """A new secret for the authenticator app, shown once as a QR code / setup key."""
    user, _flow, root = await _from_challenge(session, policy, mfa_token)
    cred = await _credential(session, user.id)
    if cred is not None and cred.confirmed_at is not None:
        raise Conflict(
            "an authenticator app is already set up",
            next_action="Use it, or a recovery code. To replace it, ask your administrator.",
        )
    secret = mfa.new_totp_secret()
    with use_context(_ctx(user, root, "mfa-enrolment")):
        if cred is None:
            cred = MfaCredential(id=uuid7(), tenant_id=user.tenant_id, user_id=user.id)
            session.add(cred)
        cred.secret_enc = sso.cipher.encrypt(
            secret, tenant_id=user.tenant_id, table=_MFA_TABLE, column=_MFA_COLUMN, row_id=cred.id
        )
        cred.last_step = None
        await session.flush()
    return Enrolment(
        secret, mfa.otpauth_uri(secret, account=user.login, issuer=sso.authenticator_label)
    )


async def confirm_enrolment(
    session: AsyncSession,
    policy: TokenPolicy,
    sso: SsoConfig,
    *,
    mfa_token: str,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Enrolled | MfaRejected:
    """The first code from the app proves it works; sign-in completes with recovery codes."""
    user, flow, root = await _from_challenge(session, policy, mfa_token)
    cred = await _credential(session, user.id)
    if cred is None or cred.confirmed_at is not None:
        raise Conflict("start setting up the authenticator app first")
    if _locked(cred):
        return MfaRejected("locked")
    with use_context(_ctx(user, root, "mfa-enrolment")):
        matched = mfa.verify_totp(_secret(sso, cred), code, last_step=cred.last_step)
        if matched is None:
            out = _failed(cred)
            await session.flush()
            return out
        now = utcnow()
        cred.confirmed_at = cred.last_used_at = now
        cred.last_step = matched
        cred.failed_attempts = 0
        codes = await _replace_recovery_codes(session, user)
        publish(
            session,
            EventEnvelope(
                event_type=events.MFA_ENROLLED,
                tenant_id=user.tenant_id,
                scope_node_id=root,
                aggregate_type="app_user",
                aggregate_id=user.id,
                actor_id=user.id,
                payload={"kind": "totp"},
            ),
        )
        flow.completed_at = now
        await session.flush()
    tokens = await _open_session(
        session,
        policy,
        user,
        method="oidc",
        amr=["fed", "otp", "mfa"],
        ip=ip,
        user_agent=user_agent,
    )
    return Enrolled(tokens, codes)


async def _replace_recovery_codes(session: AsyncSession, user: AppUser) -> list[str]:
    old = (
        (await session.execute(select(RecoveryCode).where(RecoveryCode.user_id == user.id)))
        .scalars()
        .all()
    )
    for row in old:
        await session.delete(row)
    await session.flush()
    codes = mfa.new_recovery_codes()
    for c in codes:
        session.add(
            RecoveryCode(
                tenant_id=user.tenant_id, user_id=user.id, code_hash=mfa.hash_recovery_code(c)
            )
        )
    await session.flush()
    return codes


async def new_recovery_codes(
    session: AsyncSession, *, user_id: uuid.UUID, amr: tuple[str, ...]
) -> list[str]:
    """Replace a signed-in user's recovery codes. Needs a session that passed MFA."""
    if "mfa" not in amr:
        raise Unauthenticated(
            "this needs a sign-in that used your second factor", next_action="Sign in again."
        )
    user = await session.get(AppUser, user_id)
    cred = await _credential(session, user_id)
    if user is None or cred is None or cred.confirmed_at is None:
        raise Conflict("no authenticator app is set up")
    root = await governance.tenant_root_scope(session, user.tenant_id)
    with use_context(_ctx(user, root, "mfa-recovery-codes")):
        return await _replace_recovery_codes(session, user)


async def reset_mfa(
    session: AsyncSession, *, user_id: uuid.UUID, reason: str, actor_id: uuid.UUID | None = None
) -> None:
    """Administrator reset for a lost phone: removes the app and recovery codes, so the user
    sets up a new app at their next sign-in. Audited, with the reason."""
    user = await session.get(AppUser, user_id)
    if user is None:
        raise NotFound("user not found")
    root = await governance.tenant_root_scope(session, user.tenant_id)
    with use_context(RequestContext(user.tenant_id, root, actor_id, purpose="mfa-reset")):
        cred = await _credential(session, user_id)
        if cred is not None:
            await session.delete(cred)
        for rc in (
            (await session.execute(select(RecoveryCode).where(RecoveryCode.user_id == user_id)))
            .scalars()
            .all()
        ):
            await session.delete(rc)
        await session.flush()
        publish(
            session,
            EventEnvelope(
                event_type=events.MFA_RESET,
                tenant_id=user.tenant_id,
                scope_node_id=root,
                aggregate_type="app_user",
                aggregate_id=user.id,
                actor_id=actor_id,
                payload={"reason": reason[:200]},
            ),
        )
        await session.flush()
