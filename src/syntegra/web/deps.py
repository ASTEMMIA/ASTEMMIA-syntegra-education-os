"""Request-scoped dependencies: context resolution, unit of work, permission checks."""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

import orjson
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Forbidden, PreconditionRequired, Unauthenticated, ValidationFailed
from syntegra.governance import api as governance
from syntegra.iam import api as iam
from syntegra.iam.domain import Decision
from syntegra.platform.models import IdempotencyRecord
from syntegra.platform.settings import Settings
from syntegra.web.auth import Principal, authenticate


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@dataclass
class Uow:
    session: AsyncSession
    ctx: RequestContext
    principal: Principal

    async def require(self, permission: str, scope_id: uuid.UUID | None = None) -> Decision:
        decision = await iam.check(
            self.session,
            user_id=self.principal.user_id,
            permission=permission,
            target_scope_id=scope_id or self.ctx.scope_node_id,
        )
        return decision.require()


async def unit_of_work(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> AsyncIterator[Uow]:
    """Layer 1 of §10.3: resolve user, tenant and active scope into a request context."""
    principal = authenticate(request, settings)
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        tenant_root = await governance.tenant_root_scope(session, principal.tenant_id)
        if not await governance.is_within(session, tenant_root, principal.active_scope_id):
            raise Forbidden("the active scope is outside your organisation")
        grants = await iam.grants_for(session, principal.user_id)
        ancestors = await governance.ancestor_ids(session, principal.active_scope_id)
        if not any(g.scope_node_id in ancestors for g in grants):
            raise Unauthenticated(
                "you have no role at this scope", next_action="Switch to a scope you work in."
            )
        ctx = RequestContext(
            tenant_id=principal.tenant_id,
            scope_node_id=principal.active_scope_id,
            user_id=principal.user_id,
            ip=request.client.host if request.client else None,
            device_id=request.headers.get("X-Device-Id"),
            purpose=request.headers.get("X-Purpose"),
        )
        with use_context(ctx):
            yield Uow(session, ctx, principal)


UowDep = Annotated[Uow, Depends(unit_of_work)]


# --------------------------------------------------------------------------- idempotency


def _request_hash(body: Any) -> bytes:
    return hashlib.sha256(orjson.dumps(body, option=orjson.OPT_SORT_KEYS)).digest()


async def replay(uow: Uow, request: Request, body: Any) -> tuple[str, bytes, JSONResponse | None]:
    """Idempotency-Key is required on POST (§10.4). Returns a stored response on retry."""
    key = request.headers.get("Idempotency-Key")
    if not key or len(key) > 128:
        raise PreconditionRequired(
            "Idempotency-Key header is required on POST",
            next_action="Send a unique key (e.g. a UUID) and reuse it when retrying.",
        )
    digest = _request_hash(body)
    existing = (
        await uow.session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == uow.ctx.tenant_id,
                IdempotencyRecord.user_id == uow.principal.user_id,
                IdempotencyRecord.idem_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        return key, digest, None
    if existing.request_hash != digest:
        raise ValidationFailed(
            "this Idempotency-Key was already used with a different request",
            next_action="Use a new key for a new request.",
        )
    return (
        key,
        digest,
        JSONResponse(
            orjson.loads(existing.response_body),
            status_code=existing.status_code,
            headers={"Idempotent-Replayed": "true"},
        ),
    )


def remember(uow: Uow, key: str, digest: bytes, status: int, body: Any) -> JSONResponse:
    raw = orjson.dumps(body)
    uow.session.add(
        IdempotencyRecord(
            tenant_id=uow.ctx.tenant_id,
            user_id=uow.principal.user_id,
            idem_key=key,
            request_hash=digest,
            status_code=status,
            response_body=raw,
        )
    )
    return JSONResponse(orjson.loads(raw), status_code=status)


# --------------------------------------------------------------------------- pagination

MAX_LIMIT = 200


def encode_cursor(last_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(last_id.bytes).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> uuid.UUID | None:
    if not cursor:
        return None
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except ValueError as exc:
        raise ValidationFailed(
            "cursor is not valid", next_action="Start again from the first page."
        ) from exc
