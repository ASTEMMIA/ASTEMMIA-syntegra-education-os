"""Authentication at the edge (§8.3).

Production uses OIDC / OAuth 2.1 short-lived JWTs (5-15 min) from the federated IdP; that
verifier is a Phase 0 open item (ADR-005). Until then only the dev header authenticator
exists and it refuses to run outside dev/test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request

from syntegra.core.errors import Unauthenticated
from syntegra.platform.settings import Settings


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    active_scope_id: uuid.UUID


def _uuid(request: Request, header: str) -> uuid.UUID:
    raw = request.headers.get(header)
    if not raw:
        raise Unauthenticated(f"missing {header}", next_action="Sign in again.")
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise Unauthenticated(f"{header} is not a valid id") from exc


def authenticate(request: Request, settings: Settings) -> Principal:
    if settings.dev_auth and settings.env in ("dev", "test"):
        return Principal(
            user_id=_uuid(request, "X-Dev-User-Id"),
            tenant_id=_uuid(request, "X-Tenant-Id"),
            active_scope_id=_uuid(request, "X-Scope-Node-Id"),
        )
    raise Unauthenticated(
        "OIDC authentication is not configured", next_action="Contact the platform team."
    )
