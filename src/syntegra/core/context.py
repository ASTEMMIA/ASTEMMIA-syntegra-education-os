"""Request context: who, which tenant, which active scope, for what purpose (CEDA §8, §10.3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

from syntegra.core.errors import TenantIsolationError
from syntegra.core.ids import uuid7


@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: uuid.UUID
    scope_node_id: uuid.UUID
    user_id: uuid.UUID | None
    on_behalf_of: uuid.UUID | None = None
    purpose: str | None = None
    device_id: str | None = None
    ip: str | None = None
    correlation_id: uuid.UUID = field(default_factory=uuid7)
    # System jobs (relay, verifier, migrations) may read across the active scope
    # but never across tenants unless explicitly using platform_context().
    system: bool = False

    def with_purpose(self, purpose: str) -> RequestContext:
        return replace(self, purpose=purpose)


_current: ContextVar[RequestContext | None] = ContextVar("syntegra_request_context", default=None)


def current_context() -> RequestContext:
    ctx = _current.get()
    if ctx is None:
        raise TenantIsolationError("no request context: tenant-scoped access is not allowed")
    return ctx


def maybe_context() -> RequestContext | None:
    return _current.get()


@contextmanager
def use_context(ctx: RequestContext) -> Iterator[RequestContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
