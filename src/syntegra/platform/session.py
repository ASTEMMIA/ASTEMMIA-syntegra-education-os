"""The Syntegra ORM session: tenancy guard, write stamping, P7 guard and audit-in-transaction.

Implements the four-layer isolation of CEDA §10.3 (layers 1-2 here; layer 3 is per-region
clusters and the separate safeguarding DB user; layer 4 is the contract tests).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Any, cast

from sqlalchemy import Select, Table, and_, event, inspect, literal, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.orm.attributes import get_history

from syntegra.core.audit import event_hash, genesis_hash, state_hash
from syntegra.core.clock import utcnow
from syntegra.core.context import RequestContext, maybe_context
from syntegra.core.errors import InvariantViolation, TenantIsolationError
from syntegra.core.ids import uuid7
from syntegra.platform.db import Audited, Evidential, Permanent, TenantScoped, UUIDBinary
from syntegra.platform.models import AuditChainHead, AuditEvent

# Execution option that lifts the tenancy filter. Only platform jobs (outbox relay, audit
# verifier, migrations) may set it; lint bans it outside syntegra.platform.
PLATFORM_BYPASS = "syntegra_platform_bypass"

# Provided by governance: returns a SELECT of descendant scope ids for an ancestor id.
_scope_subtree: Callable[[uuid.UUID], Select[Any]] | None = None


def register_scope_subtree(fn: Callable[[uuid.UUID], Select[Any]]) -> None:
    global _scope_subtree
    _scope_subtree = fn


def _subtree(scope_id: uuid.UUID) -> Select[Any]:
    if _scope_subtree is None:
        raise RuntimeError("scope closure provider not registered (import syntegra.governance)")
    return _scope_subtree(scope_id)


class SyntegraSession(Session):
    pass


# --------------------------------------------------------------------------- reads


@event.listens_for(SyntegraSession, "do_orm_execute")
def _tenant_filter(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get(PLATFORM_BYPASS):
        return
    if not any(issubclass(m.class_, TenantScoped) for m in state.all_mappers):
        return
    ctx = maybe_context()
    if ctx is None:
        raise TenantIsolationError("tenant-scoped query issued without a request context")
    # Plain (non-lambda) criteria per mapped class: the values are bound fresh on every
    # statement, so nothing about one tenant can be cached into another tenant's query.
    options = []
    for mapper in state.all_mappers:
        cls = mapper.class_
        if issubclass(cls, TenantScoped):
            options.append(
                with_loader_criteria(
                    cls,
                    and_(
                        cls.tenant_id == ctx.tenant_id,
                        cls.scope_node_id.in_(_subtree(ctx.scope_node_id)),
                    ),
                    include_aliases=True,
                )
            )
    state.statement = state.statement.options(*options)


# --------------------------------------------------------------------------- writes


def _snapshot(obj: Any, *, before: bool) -> dict[str, Any]:
    mapper = inspect(obj).mapper
    excluded: frozenset[str] = getattr(obj, "__audit_exclude__", frozenset())
    out: dict[str, Any] = {}
    for attr in mapper.column_attrs:
        if attr.key in excluded:
            continue
        if before:
            hist = get_history(obj, attr.key)
            if hist.deleted:
                out[attr.key] = hist.deleted[0]
            elif hist.unchanged:
                out[attr.key] = hist.unchanged[0]
            else:
                out[attr.key] = getattr(obj, attr.key)
        else:
            out[attr.key] = getattr(obj, attr.key)
    return out


def _changed_fields(obj: Any) -> list[str]:
    mapper = inspect(obj).mapper
    excluded: frozenset[str] = getattr(obj, "__audit_exclude__", frozenset())
    return sorted(
        a.key
        for a in mapper.column_attrs
        if a.key not in excluded and get_history(obj, a.key).has_changes()
    )


def _stamp_and_check(
    session: Session, obj: TenantScoped, ctx: RequestContext | None, *, new: bool
) -> None:
    if ctx is None:
        raise TenantIsolationError(f"write to {type(obj).__name__} without a request context")
    if new:
        now = utcnow()
        obj.created_at = obj.created_at or now
        obj.updated_at = obj.updated_at or now
        obj.row_version = obj.row_version or 1
        if obj.tenant_id is None:
            obj.tenant_id = ctx.tenant_id
        if obj.scope_node_id is None:
            obj.scope_node_id = ctx.scope_node_id
        obj.created_by = obj.created_by or ctx.user_id
    if obj.tenant_id != ctx.tenant_id and not ctx.system:
        raise TenantIsolationError("cross-tenant write refused")
    if not ctx.system and obj.scope_node_id != ctx.scope_node_id:
        with session.no_autoflush:
            inside = session.execute(
                select(literal(1)).where(
                    literal(obj.scope_node_id, UUIDBinary()).in_(_subtree(ctx.scope_node_id))
                )
            ).first()
        if inside is None:
            raise TenantIsolationError("write outside the active scope subtree refused")
    obj.updated_by = ctx.user_id


def _upsert_head(session: Session, tenant_id: uuid.UUID, chain_date: date) -> Any:
    values = {
        "tenant_id": tenant_id,
        "chain_date": chain_date,
        "last_seq": 0,
        "last_hash": genesis_hash(tenant_id, chain_date),
    }
    table = cast(Table, AuditChainHead.__table__)
    if session.get_bind().dialect.name == "mysql":
        stmt = mysql_insert(table).values(values)
        return stmt.on_duplicate_key_update(last_seq=table.c.last_seq)
    return sqlite_insert(table).values(values).on_conflict_do_nothing()


class _ChainCursor:
    """Hands out (seq, prev_hash) for tenant-day chains within one flush."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.heads: dict[tuple[uuid.UUID, date], AuditChainHead] = {}

    def _lock_head(self, tenant_id: uuid.UUID, chain_date: date) -> AuditChainHead | None:
        return self.session.execute(
            select(AuditChainHead)
            .where(AuditChainHead.tenant_id == tenant_id, AuditChainHead.chain_date == chain_date)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def next(self, tenant_id: uuid.UUID, chain_date: date) -> AuditChainHead:
        key = (tenant_id, chain_date)
        head = self.heads.get(key)
        if head is None:
            with self.session.no_autoflush:
                # One statement that creates the head if absent AND takes an exclusive row
                # lock either way (MySQL: INSERT ... ON DUPLICATE KEY UPDATE). No shared lock
                # is ever upgraded, so first writers of a tenant-day cannot deadlock, and no
                # second connection is needed.
                self.session.execute(_upsert_head(self.session, tenant_id, chain_date))
                head = self._lock_head(tenant_id, chain_date)
                if head is None:
                    raise RuntimeError("audit chain head could not be created")
            self.heads[key] = head
        return head


def append_audit(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    scope_node_id: uuid.UUID | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    changed_fields: list[str] | None = None,
    cursor: _ChainCursor | None = None,
) -> AuditEvent:
    """Append one event to the tenant-day chain inside the caller's transaction."""
    ctx = maybe_context()
    cursor = cursor or _ChainCursor(session)
    now = utcnow()
    head = cursor.next(tenant_id, now.date())
    ev = AuditEvent(
        chain_date=now.date(),
        tenant_id=tenant_id,
        seq=head.last_seq + 1,
        occurred_at=now,
        actor_id=ctx.user_id if ctx else None,
        on_behalf_of=ctx.on_behalf_of if ctx else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        scope_node_id=scope_node_id,
        purpose=ctx.purpose if ctx else None,
        before_hash=state_hash(before),
        after_hash=state_hash(after),
        ip=ctx.ip if ctx else None,
        device_id=ctx.device_id if ctx else None,
        correlation_id=ctx.correlation_id if ctx else None,
        changed_fields=changed_fields,
        prev_hash=head.last_hash,
    )
    ev.event_hash = event_hash(ev.prev_hash, ev.hash_view())
    head.last_seq = ev.seq
    head.last_hash = ev.event_hash
    session.add(ev)
    return ev


@event.listens_for(SyntegraSession, "before_flush")
def _before_flush(session: Session, flush_context: Any, instances: Any) -> None:
    ctx = maybe_context()
    cursor = _ChainCursor(session)
    pending: list[
        tuple[str, Any, dict[str, Any] | None, dict[str, Any] | None, list[str] | None]
    ] = []

    for obj in list(session.new):
        if isinstance(obj, TenantScoped):
            _stamp_and_check(session, obj, ctx, new=True)
        if isinstance(obj, Audited):
            pending.append(("create", obj, None, None, None))

    for obj in list(session.dirty):
        if not session.is_modified(obj, include_collections=False):
            continue
        if isinstance(obj, Evidential):
            raise InvariantViolation(
                f"{type(obj).__name__} is evidential and cannot be changed (P7)",
                next_action="Record a correction as a new version or reversing entry.",
            )
        if isinstance(obj, TenantScoped):
            _stamp_and_check(session, obj, ctx, new=False)
        if isinstance(obj, Audited) and (changed := _changed_fields(obj)):
            # Only audited fields count: counters and read markers are excluded (ADR-030).
            pending.append(("update", obj, _snapshot(obj, before=True), None, changed))

    for obj in list(session.deleted):
        if isinstance(obj, Evidential):
            raise InvariantViolation(
                f"{type(obj).__name__} is evidential and cannot be deleted (P7)"
            )
        if isinstance(obj, Permanent):
            raise InvariantViolation(
                f"{type(obj).__name__} is kept permanently and cannot be deleted"
            )
        if isinstance(obj, TenantScoped):
            _stamp_and_check(session, obj, ctx, new=False)
        if isinstance(obj, Audited):
            pending.append(("delete", obj, _snapshot(obj, before=True), None, None))

    for action, obj, before, _, fields in pending:
        if action == "create" and getattr(obj, "id", None) is None:
            obj.id = uuid7()  # assign now so the audit event can reference it
        after = None if action == "delete" else _snapshot(obj, before=False)
        append_audit(
            session,
            tenant_id=obj.audit_tenant_id(),
            action=action,
            entity_type=obj.__audit_entity__ or obj.__tablename__,
            entity_id=getattr(obj, "id", None),
            scope_node_id=obj.audit_scope_node_id(),
            before=before,
            after=after,
            changed_fields=fields,
            cursor=cursor,
        )
