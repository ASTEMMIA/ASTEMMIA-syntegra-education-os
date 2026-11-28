"""Authentication at the edge (§8.3, §10.1).

Every API request carries ``Authorization: Bearer <access token>``. Tokens are always issued
by Syntegra and verified here, whichever sign-in method produced them (dev login now,
OIDC + MFA next). The active scope is chosen per request with ``X-Scope-Node-Id``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request

from syntegra.core.errors import Unauthenticated, ValidationFailed
from syntegra.iam.auth import TokenPolicy
from syntegra.iam.tokens import AccessClaims, KeySet, verify_access_token
from syntegra.platform.settings import Settings


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    session_id: uuid.UUID
    requested_scope_id: uuid.UUID | None
    claims: AccessClaims


def build_keys(settings: Settings) -> KeySet:
    if settings.token_signing_key_pem is not None:
        return KeySet.from_pem(
            settings.token_signing_key_pem.get_secret_value(), settings.token_verify_keys_pem
        )
    if settings.env in ("dev", "test"):
        return KeySet.generate()
    raise RuntimeError("SYNTEGRA_TOKEN_SIGNING_KEY_PEM is required outside dev/test")


def token_policy(settings: Settings, keys: KeySet) -> TokenPolicy:
    return TokenPolicy(
        keys=keys,
        issuer=settings.token_issuer,
        audience=settings.token_audience,
        access_ttl=settings.access_token_ttl_seconds,
        refresh_ttl=settings.refresh_token_ttl_seconds,
        session_max=settings.session_max_seconds,
    )


def bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthenticated("sign-in required", next_action="Sign in and send the access token.")
    return token.strip()


def authenticate(request: Request, policy: TokenPolicy) -> Principal:
    claims = verify_access_token(
        policy.keys, bearer_token(request), issuer=policy.issuer, audience=policy.audience
    )
    raw_scope = request.headers.get("X-Scope-Node-Id")
    try:
        scope = uuid.UUID(raw_scope) if raw_scope else None
    except ValueError as exc:
        raise ValidationFailed("X-Scope-Node-Id is not a valid id") from exc
    return Principal(claims.user_id, claims.tenant_id, claims.session_id, scope, claims)
