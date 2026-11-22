"""IAM application services. Other packages import only this module and events."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today, utcnow
from syntegra.core.context import maybe_context
from syntegra.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.governance import api as governance
from syntegra.iam import events
from syntegra.iam.baseline import BASELINE, PERMISSIONS, ROLES
from syntegra.iam.domain import (
    Cell,
    Clearance,
    Decision,
    GrantFacts,
    evaluate,
    role_clearance,
    validate_custom_role,
)
from syntegra.iam.models import AccessGrant, AppUser, Role, RolePermission
from syntegra.platform.outbox import publish

__all__ = [
    "Decision",
    "check",
    "clone_role",
    "create_user",
    "grant_role",
    "grants_for",
    "revoke_grant",
    "seed_builtin_roles",
    "user_by_login",
]


@dataclass(frozen=True, slots=True)
class UserView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    login: str
    display_name: str
    status: str


def _actor() -> uuid.UUID | None:
    ctx = maybe_context()
    return ctx.user_id if ctx else None


async def seed_builtin_roles(session: AsyncSession) -> int:
    """Idempotently insert the §21 baseline roles. Returns how many were created."""
    existing = set(
        (
            await session.execute(select(Role.code).where(Role.tenant_id.is_(None), Role.builtin))
        ).scalars()
    )
    created = 0
    await _sync_builtin_permissions(session, existing)
    for code, name in ROLES.items():
        if code in existing:
            continue
        perms = BASELINE[code]
        role = Role(
            tenant_id=None,
            code=code,
            name=name,
            builtin=True,
            clearance=int(role_clearance(perms, PERMISSIONS)),
        )
        session.add(role)
        await session.flush()
        for perm, cell in perms.items():
            session.add(RolePermission(role_id=role.id, permission=perm, cell=cell.value))
        created += 1
    await session.flush()
    return created


async def _sync_builtin_permissions(session: AsyncSession, existing: set[str]) -> None:
    """Give existing built-in roles the permissions added to the baseline since they were
    seeded (e.g. Staff 360, ADR-024). Never removes a permission."""
    for code in existing & set(ROLES):
        role = (
            await session.execute(
                select(Role).where(Role.tenant_id.is_(None), Role.builtin, Role.code == code)
            )
        ).scalar_one()
        have = set(
            (
                await session.execute(
                    select(RolePermission.permission).where(RolePermission.role_id == role.id)
                )
            ).scalars()
        )
        missing = {p: c for p, c in BASELINE[code].items() if p not in have}
        for perm, cell in missing.items():
            session.add(RolePermission(role_id=role.id, permission=perm, cell=cell.value))
        if missing:
            role.clearance = int(role_clearance(BASELINE[code], PERMISSIONS))
    await session.flush()


async def _role(session: AsyncSession, code: str, tenant_id: uuid.UUID) -> Role:
    role = (
        (
            await session.execute(
                select(Role)
                .where(
                    Role.code == code, or_(Role.tenant_id == tenant_id, Role.tenant_id.is_(None))
                )
                .order_by(Role.tenant_id.is_(None))  # tenant-specific first
            )
        )
        .scalars()
        .first()
    )
    if role is None:
        raise NotFound(f"role {code} not found", next_action="Seed built-in roles or clone one.")
    return role


async def _role_permissions(
    session: AsyncSession, role_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Cell]]:
    out: dict[uuid.UUID, dict[str, Cell]] = {r: {} for r in role_ids}
    if not role_ids:
        return out
    rows = await session.execute(select(RolePermission).where(RolePermission.role_id.in_(role_ids)))
    for rp in rows.scalars():
        out[rp.role_id][rp.permission] = Cell(rp.cell)
    return out


async def create_user(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    login: str,
    display_name: str,
    idp_subject: str | None = None,
) -> UserView:
    login = login.strip().lower()
    if not login:
        raise ValidationFailed("login is required")
    dup = await session.execute(
        select(AppUser.id).where(AppUser.tenant_id == tenant_id, AppUser.login == login)
    )
    if dup.first():
        raise Conflict(
            "a user with this login already exists",
            next_action="Find and update that user instead.",
        )
    user = AppUser(
        tenant_id=tenant_id, login=login, display_name=display_name, idp_subject=idp_subject
    )
    session.add(user)
    await session.flush()
    tenant_root = await _tenant_root(session, tenant_id)
    publish(
        session,
        EventEnvelope(
            event_type=events.USER_CREATED,
            tenant_id=tenant_id,
            scope_node_id=tenant_root,
            aggregate_type="app_user",
            aggregate_id=user.id,
            actor_id=_actor(),
        ),
    )
    return UserView(user.id, user.tenant_id, user.login, user.display_name, user.status)


async def _tenant_root(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    return await governance.tenant_root_scope(session, tenant_id)


async def user_by_login(session: AsyncSession, tenant_id: uuid.UUID, login: str) -> UserView | None:
    u = (
        await session.execute(
            select(AppUser).where(AppUser.tenant_id == tenant_id, AppUser.login == login.lower())
        )
    ).scalar_one_or_none()
    return UserView(u.id, u.tenant_id, u.login, u.display_name, u.status) if u else None


async def grant_role(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    role_code: str,
    scope_node_id: uuid.UUID,
    valid_from: date | None = None,
    valid_to: date | None = None,
    clearance_cap: Clearance | None = None,
) -> uuid.UUID:
    user = await session.get(AppUser, user_id)
    if user is None:
        raise NotFound("user not found")
    root = await _tenant_root(session, user.tenant_id)
    if not await governance.is_within(session, root, scope_node_id):
        raise ValidationFailed(
            "a grant must sit inside the user's own tenant",
            next_action="Parent scopes see child data only through dataset grants (§2).",
        )
    role = await _role(session, role_code, user.tenant_id)
    cap = Clearance(role.clearance) if clearance_cap is None else clearance_cap
    if cap > role.clearance:
        raise ValidationFailed("a clearance cap cannot exceed the role's clearance")
    start = valid_from or today()
    if valid_to is not None and valid_to < start:
        raise ValidationFailed("valid_to is before valid_from")
    grant = AccessGrant(
        tenant_id=user.tenant_id,
        user_id=user_id,
        role_id=role.id,
        scope_node_id=scope_node_id,
        clearance_cap=int(cap),
        valid_from=start,
        valid_to=valid_to,
        granted_by=_actor(),
    )
    session.add(grant)
    await session.flush()
    publish(
        session,
        EventEnvelope(
            event_type=events.GRANT_ADDED,
            tenant_id=user.tenant_id,
            scope_node_id=scope_node_id,
            aggregate_type="app_user",
            aggregate_id=user_id,
            actor_id=_actor(),
            payload={"grant_id": str(grant.id), "role": role.code},
        ),
    )
    return grant.id


async def revoke_grant(session: AsyncSession, grant_id: uuid.UUID) -> None:
    grant = await session.get(AccessGrant, grant_id)
    if grant is None or grant.revoked_at is not None:
        raise NotFound("active grant not found")
    grant.revoked_at = utcnow()
    grant.valid_to = today()
    publish(
        session,
        EventEnvelope(
            event_type=events.GRANT_REVOKED,
            tenant_id=grant.tenant_id,
            scope_node_id=grant.scope_node_id,
            aggregate_type="app_user",
            aggregate_id=grant.user_id,
            actor_id=_actor(),
            payload={"grant_id": str(grant.id)},
        ),
    )
    await session.flush()


async def grants_for(session: AsyncSession, user_id: uuid.UUID) -> list[GrantFacts]:
    rows = (
        await session.execute(
            select(AccessGrant, Role.code)
            .join(Role, Role.id == AccessGrant.role_id)
            .where(AccessGrant.user_id == user_id, AccessGrant.revoked_at.is_(None))
        )
    ).all()
    perms = await _role_permissions(session, [g.role_id for g, _ in rows])
    return [
        GrantFacts(
            grant_id=g.id,
            role_code=code,
            permissions=perms[g.role_id],
            scope_node_id=g.scope_node_id,
            valid_from=g.valid_from,
            valid_to=g.valid_to,
            clearance_cap=Clearance(g.clearance_cap),
        )
        for g, code in rows
    ]


async def check(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    permission: str,
    target_scope_id: uuid.UUID,
    on: date | None = None,
) -> Decision:
    perm = PERMISSIONS.get(permission)
    if perm is None:
        raise ValueError(f"unknown permission {permission}")
    ancestors = await governance.ancestor_ids(session, target_scope_id)
    return evaluate(perm, await grants_for(session, user_id), ancestors, on or today())


async def effective_clearance(
    session: AsyncSession, user_id: uuid.UUID, on: date | None = None
) -> Clearance:
    d = on or today()
    return max(
        (g.clearance_cap for g in await grants_for(session, user_id) if g.active_on(d)),
        default=Clearance.PUBLIC,
    )


async def clone_role(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_code: str,
    new_code: str,
    name: str,
    permissions: Mapping[str, str] | None,
    creator_id: uuid.UUID,
) -> uuid.UUID:
    source = await _role(session, source_code, tenant_id)
    src_perms = (await _role_permissions(session, [source.id]))[source.id]
    chosen = {k: Cell(v) for k, v in permissions.items()} if permissions is not None else src_perms
    creator_clearance = await effective_clearance(session, creator_id)
    clearance = validate_custom_role(
        source_builtin=source.builtin,
        editing_builtin=False,
        permissions=chosen,
        catalogue=PERMISSIONS,
        creator_clearance=creator_clearance,
    )
    if new_code in ROLES:
        raise Conflict("custom roles cannot reuse a built-in role code")
    role = Role(
        tenant_id=tenant_id,
        code=new_code,
        name=name,
        builtin=False,
        clearance=int(clearance),
        cloned_from_id=source.id,
    )
    session.add(role)
    await session.flush()
    for perm, cell in chosen.items():
        session.add(RolePermission(role_id=role.id, permission=perm, cell=cell.value))
    publish(
        session,
        EventEnvelope(
            event_type=events.ROLE_CLONED,
            tenant_id=tenant_id,
            scope_node_id=await _tenant_root(session, tenant_id),
            aggregate_type="role",
            aggregate_id=role.id,
            actor_id=creator_id,
            payload={"source": source.code, "code": new_code},
        ),
    )
    await session.flush()
    return role.id


async def assert_can_edit_role(session: AsyncSession, role_id: uuid.UUID) -> None:
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFound("role not found")
    if role.builtin:
        raise Forbidden(
            "built-in roles cannot be edited", next_action="Clone the role and edit the copy."
        )
