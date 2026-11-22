from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from syntegra.core.clock import today
from syntegra.core.errors import Forbidden, NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.iam import api as iam
from syntegra.iam.baseline import PERMISSIONS, ROLES
from syntegra.iam.domain import evaluate
from syntegra.platform.audit_store import verify_day
from syntegra.platform.models import AuditEvent
from syntegra.web.deps import UowDep
from syntegra.web.schemas import AuditEventOut, AuditVerifyOut, ChainBreakOut, GrantOut, MeOut

router = APIRouter(prefix="/v1", tags=["iam", "audit"])


@router.get("/me", response_model=MeOut)
async def me(uow: UowDep) -> MeOut:
    grants = await iam.grants_for(uow.session, uow.principal.user_id)
    ancestors = await governance.ancestor_ids(uow.session, uow.ctx.scope_node_id)
    here: dict[str, str] = {}
    for code, perm in PERMISSIONS.items():
        d = evaluate(perm, grants, ancestors, today())
        if d.allowed and d.cell is not None:
            here[code] = d.cell.value
    names = await iam.user_names(uow.session, uow.ctx.tenant_id, [uow.principal.user_id])
    scopes = {
        s: (await governance.get_scope_node(uow.session, s)).name
        for s in {g.scope_node_id for g in grants}
    }
    return MeOut(
        user_id=uow.principal.user_id,
        display_name=names.get(uow.principal.user_id),
        tenant_id=uow.ctx.tenant_id,
        active_scope_id=uow.ctx.scope_node_id,
        grants=[
            GrantOut(
                grant_id=g.grant_id,
                role=g.role_code,
                scope_node_id=g.scope_node_id,
                valid_from=g.valid_from,
                valid_to=g.valid_to,
                clearance_cap=int(g.clearance_cap),
                role_name=ROLES.get(g.role_code),
                scope_name=scopes.get(g.scope_node_id),
            )
            for g in grants
        ],
        permissions_here=here,
    )


@router.get("/audit/verify", response_model=AuditVerifyOut)
async def verify(uow: UowDep, day: date | None = None) -> AuditVerifyOut:
    await uow.require("audit:view")
    d = day or today()
    count, breaks = await verify_day(uow.session, uow.ctx.tenant_id, d)
    return AuditVerifyOut(
        tenant_id=uow.ctx.tenant_id,
        date=d,
        events=count,
        intact=not breaks,
        breaks=[ChainBreakOut(seq=b.seq, reason=b.reason) for b in breaks],
    )


@router.get("/audit/history/{entity_type}/{entity_id}", response_model=list[AuditEventOut])
async def history(entity_type: str, entity_id: uuid.UUID, uow: UowDep) -> list[AuditEventOut]:
    """'Last updated by, when, change history' for the audit-aware UX (§9.1)."""
    await uow.require("audit:view")
    rows = (
        (
            await uow.session.execute(
                select(AuditEvent)
                .where(
                    AuditEvent.tenant_id == uow.ctx.tenant_id,
                    AuditEvent.entity_type == entity_type,
                    AuditEvent.entity_id == entity_id,
                )
                .order_by(AuditEvent.chain_date, AuditEvent.seq)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NotFound("no history for this record")
    return [
        AuditEventOut(
            seq=r.seq,
            chain_date=r.chain_date,
            occurred_at=r.occurred_at.isoformat() + "Z",
            actor_id=r.actor_id,
            action=r.action,
            changed_fields=r.changed_fields,
        )
        for r in rows
    ]


@router.get("/users/names", summary="Display names for user ids (up to 200)")
async def user_names(uow: UowDep, ids: str) -> dict[str, str]:
    try:
        wanted = [uuid.UUID(x) for x in ids.split(",") if x.strip()][:200]
    except ValueError as exc:
        raise ValidationFailed("ids are comma-separated user ids") from exc
    names = await iam.user_names(uow.session, uow.ctx.tenant_id, wanted)
    return {str(k): v for k, v in names.items()}


@router.get("/users", summary="Find users by name or login (to assign work or hand a file)")
async def find_users(uow: UowDep, q: str) -> dict[str, Any]:
    for perm in ("user:manage", "records:admin", "file:manage"):
        if (
            await iam.check(
                uow.session,
                user_id=uow.principal.user_id,
                permission=perm,
                target_scope_id=uow.ctx.scope_node_id,
            )
        ).allowed:
            break
    else:
        raise Forbidden("you cannot look up users here")
    found = await iam.search_users(uow.session, uow.ctx.tenant_id, q)
    return {
        "items": [
            {"id": str(u.id), "display_name": u.display_name, "login": u.login} for u in found
        ]
    }
