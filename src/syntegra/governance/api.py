"""Governance application services and queries. Other packages import only this and events."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import utcnow
from syntegra.core.context import maybe_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import uuid7
from syntegra.governance import events
from syntegra.governance.domain import (
    ClosureRow,
    GovernanceLevel,
    Ownership,
    child_path,
    closure_rows_for_new_node,
    validate_child_level,
    validate_tenant_level,
)
from syntegra.governance.models import (
    ConfigSetting,
    PolicyRule,
    ScopeClosure,
    ScopeNode,
    Tenant,
    subtree_of,
)
from syntegra.governance.policy.engine import (
    RuleSpec,
    RuleTestCase,
    check_publishable,
    check_tightens,
    detect_conflicts,
)
from syntegra.platform.outbox import publish
from syntegra.platform.session import PLATFORM_BYPASS

__all__ = [
    "ScopeNodeView",
    "TenantView",
    "ancestor_ids",
    "apply_structure",
    "create_child_tenant",
    "create_policy_rule",
    "create_root_tenant",
    "create_scope_node",
    "get_scope_node",
    "get_tenant",
    "is_within",
    "list_children",
    "lowest_common_ancestor",
    "policy_rule_owner",
    "publish_policy_rule",
    "resolve_config",
    "scope_node_by_code",
    "set_config",
    "subtree_of",
    "tenant_by_code",
    "tenant_root_scope",
]


@dataclass(frozen=True, slots=True)
class TenantView:
    id: uuid.UUID
    code: str
    name: str
    kind: str
    root_scope_node_id: uuid.UUID
    residency_region: str


@dataclass(frozen=True, slots=True)
class ScopeNodeView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    parent_id: uuid.UUID | None
    level: int
    code: str
    name: str
    path: str
    local_label: str | None = None


def _node_view(n: ScopeNode) -> ScopeNodeView:
    return ScopeNodeView(
        n.id, n.tenant_id, n.parent_id, n.level, n.code, n.name, n.path, n.local_label
    )


def _actor() -> uuid.UUID | None:
    ctx = maybe_context()
    return ctx.user_id if ctx else None


def _correlation() -> uuid.UUID | None:
    ctx = maybe_context()
    return ctx.correlation_id if ctx else None


async def _ancestry(session: AsyncSession, node_id: uuid.UUID) -> list[ClosureRow]:
    rows = (
        await session.execute(select(ScopeClosure).where(ScopeClosure.descendant_id == node_id))
    ).scalars()
    return [ClosureRow(r.ancestor_id, r.descendant_id, r.depth) for r in rows]


async def get_scope_node(session: AsyncSession, node_id: uuid.UUID) -> ScopeNodeView:
    node = await session.get(ScopeNode, node_id)
    if node is None:
        raise NotFound("scope node not found")
    return _node_view(node)


async def tenant_root_scope(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.root_scope_node_id is None:
        raise NotFound("tenant not found")
    return tenant.root_scope_node_id


async def tenant_by_code(session: AsyncSession, code: str) -> TenantView | None:
    tenant = (await session.execute(select(Tenant).where(Tenant.code == code))).scalar_one_or_none()
    if tenant is None or tenant.root_scope_node_id is None or tenant.status != "active":
        return None
    return TenantView(
        tenant.id,
        tenant.code,
        tenant.name,
        tenant.kind,
        tenant.root_scope_node_id,
        tenant.residency_region,
    )


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> TenantView:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.root_scope_node_id is None:
        raise NotFound("tenant not found")
    return TenantView(
        tenant.id,
        tenant.code,
        tenant.name,
        tenant.kind,
        tenant.root_scope_node_id,
        tenant.residency_region,
    )


async def ancestor_ids(session: AsyncSession, node_id: uuid.UUID) -> set[uuid.UUID]:
    """The node itself and every ancestor, across tenant boundaries."""
    return {r.ancestor_id for r in await _ancestry(session, node_id)}


async def scope_node_by_code(
    session: AsyncSession, tenant_id: uuid.UUID, code: str
) -> ScopeNodeView | None:
    node = (
        await session.execute(
            select(ScopeNode).where(ScopeNode.tenant_id == tenant_id, ScopeNode.code == code)
        )
    ).scalar_one_or_none()
    return _node_view(node) if node else None


async def lowest_common_ancestor(session: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> uuid.UUID:
    """The deepest scope that contains both nodes, e.g. the LGEA of two schools in it."""
    a_rows = {r.ancestor_id: r.depth for r in await _ancestry(session, a)}
    b_ids = {r.ancestor_id for r in await _ancestry(session, b)}
    shared = [(depth, anc) for anc, depth in a_rows.items() if anc in b_ids]
    if not shared:
        raise NotFound("these scopes have no common ancestor")
    return min(shared)[1]


async def is_within(session: AsyncSession, ancestor_id: uuid.UUID, node_id: uuid.UUID) -> bool:
    row = await session.get(ScopeClosure, (ancestor_id, node_id))
    return row is not None


async def _insert_node(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    parent: ScopeNode | None,
    level: GovernanceLevel,
    code: str,
    name: str,
    local_label: str | None,
    is_root: bool,
) -> ScopeNode:
    validate_child_level(GovernanceLevel(parent.level) if parent else None, level)
    node_id = uuid7()
    node = ScopeNode(
        id=node_id,
        tenant_id=tenant_id,
        parent_id=parent.id if parent else None,
        level=int(level),
        code=code,
        name=name,
        local_label=local_label,
        path=child_path(parent.path if parent else None, node_id),
        is_tenant_root=is_root,
    )
    session.add(node)
    await session.flush()  # no ORM relationships: flush parents before FK children
    ancestry = await _ancestry(session, parent.id) if parent else []
    for row in closure_rows_for_new_node(node_id, ancestry):
        session.add(
            ScopeClosure(
                ancestor_id=row.ancestor_id, descendant_id=row.descendant_id, depth=row.depth
            )
        )
    return node


async def _create_tenant(
    session: AsyncSession,
    *,
    parent_node: ScopeNode | None,
    code: str,
    name: str,
    level: GovernanceLevel,
    country: str | None,
    residency_region: str,
    ownership: Ownership,
    time_zone: str,
    local_label: str | None,
) -> TenantView:
    kind = validate_tenant_level(level)
    if (await session.execute(select(Tenant.id).where(Tenant.code == code))).first():
        raise Conflict(f"tenant code {code} is already in use", next_action="Choose another code.")
    if country is not None and (len(country) != 2 or not country.isupper()):
        raise ValidationFailed("country must be an ISO 3166-1 alpha-2 code, e.g. NG")
    parent_tenant: Tenant | None = None
    if parent_node is not None:
        parent_tenant = await session.get(Tenant, parent_node.tenant_id)
        assert parent_tenant is not None
        if parent_tenant.residency_region != residency_region and level >= GovernanceLevel.REGION:
            raise ValidationFailed(
                "a sub-national tenant must stay in its parent's residency region",
                next_action="Learner-level rows never cross a residency boundary (§2).",
            )
    tenant = Tenant(
        id=uuid7(),
        code=code,
        name=name,
        kind=kind.value,
        parent_tenant_id=parent_tenant.id if parent_tenant else None,
        country=country or (parent_tenant.country if parent_tenant else None),
        residency_region=residency_region,
        ownership=ownership.value,
        time_zone=time_zone,
    )
    session.add(tenant)
    await session.flush()
    node = await _insert_node(
        session,
        tenant_id=tenant.id,
        parent=parent_node,
        level=level,
        code=code,
        name=name,
        local_label=local_label,
        is_root=True,
    )
    tenant.root_scope_node_id = node.id
    publish(
        session,
        EventEnvelope(
            event_type=events.TENANT_CREATED,
            tenant_id=tenant.id,
            scope_node_id=node.id,
            aggregate_type="tenant",
            aggregate_id=tenant.id,
            actor_id=_actor(),
            correlation_id=_correlation(),
            payload={
                "kind": kind.value,
                "level": int(level),
                "parent_tenant_id": str(tenant.parent_tenant_id)
                if tenant.parent_tenant_id
                else None,
            },
        ),
    )
    await session.flush()
    return TenantView(
        tenant.id, tenant.code, tenant.name, tenant.kind, node.id, tenant.residency_region
    )


async def create_root_tenant(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    level: GovernanceLevel = GovernanceLevel.GLOBAL,
    country: str | None = None,
    residency_region: str,
    time_zone: str = "UTC",
) -> TenantView:
    return await _create_tenant(
        session,
        parent_node=None,
        code=code,
        name=name,
        level=level,
        country=country,
        residency_region=residency_region,
        ownership=Ownership.NOT_APPLICABLE,
        time_zone=time_zone,
        local_label=None,
    )


async def create_child_tenant(
    session: AsyncSession,
    *,
    parent_scope_id: uuid.UUID,
    code: str,
    name: str,
    level: GovernanceLevel,
    country: str | None = None,
    residency_region: str,
    ownership: Ownership = Ownership.PUBLIC,
    time_zone: str = "UTC",
    local_label: str | None = None,
) -> TenantView:
    parent = await session.get(ScopeNode, parent_scope_id)
    if parent is None:
        raise NotFound("parent scope node not found")
    return await _create_tenant(
        session,
        parent_node=parent,
        code=code,
        name=name,
        level=level,
        country=country,
        residency_region=residency_region,
        ownership=ownership,
        time_zone=time_zone,
        local_label=local_label,
    )


async def create_scope_node(
    session: AsyncSession,
    *,
    parent_id: uuid.UUID,
    level: GovernanceLevel,
    code: str,
    name: str,
    local_label: str | None = None,
) -> ScopeNodeView:
    """A scope inside the parent's tenant (local authority, campus, unit, class)."""
    parent = await session.get(ScopeNode, parent_id)
    if parent is None:
        raise NotFound("parent scope node not found")
    if level in (GovernanceLevel.GLOBAL, GovernanceLevel.COUNTRY):
        raise ValidationFailed("global and country levels must be created as tenants")
    dup = await session.execute(
        select(ScopeNode.id).where(ScopeNode.tenant_id == parent.tenant_id, ScopeNode.code == code)
    )
    if dup.first():
        raise Conflict(
            f"scope code {code} already exists in this tenant", next_action="Choose another code."
        )
    node = await _insert_node(
        session,
        tenant_id=parent.tenant_id,
        parent=parent,
        level=level,
        code=code,
        name=name,
        local_label=local_label,
        is_root=False,
    )
    publish(
        session,
        EventEnvelope(
            event_type=events.SCOPE_NODE_CREATED,
            tenant_id=parent.tenant_id,
            scope_node_id=node.id,
            aggregate_type="scope_node",
            aggregate_id=node.id,
            actor_id=_actor(),
            correlation_id=_correlation(),
            payload={"level": int(level), "parent_id": str(parent.id)},
        ),
    )
    await session.flush()
    return _node_view(node)


async def list_children(
    session: AsyncSession, parent_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> Sequence[ScopeNodeView]:
    stmt = (
        select(ScopeNode)
        .where(ScopeNode.parent_id == parent_id)
        .order_by(ScopeNode.id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(ScopeNode.id > after)
    return [_node_view(n) for n in (await session.execute(stmt)).scalars()]


# --------------------------------------------------------------------------- policy


def _spec(rule: PolicyRule) -> RuleSpec:
    return RuleSpec(
        rule_key=rule.rule_key,
        owner_scope_id=rule.owner_scope_id,
        expression=rule.expression,
        valid_from=rule.valid_from,
        valid_to=rule.valid_to,
        parameters=rule.parameters,
        strictness=rule.strictness,  # type: ignore[arg-type]
        tests=tuple(
            RuleTestCase(t["name"], t["facts"], bool(t["expect"])) for t in rule.test_cases
        ),
        mandatory_below=rule.mandatory_below,
    )


async def create_policy_rule(
    session: AsyncSession,
    *,
    owner_scope_id: uuid.UUID,
    rule_key: str,
    domain: str,
    title: str,
    expression: str,
    parameters: dict[str, Any],
    strictness: dict[str, str],
    test_cases: list[dict[str, Any]],
    valid_from: date,
    valid_to: date | None = None,
    mandatory_below: bool = True,
) -> uuid.UUID:
    owner = await session.get(ScopeNode, owner_scope_id)
    if owner is None:
        raise NotFound("owning scope not found")
    from syntegra.governance.policy.dsl import compile_rule

    compile_rule(expression)  # reject unsafe or invalid expressions at draft time
    latest = (
        await session.execute(
            select(PolicyRule.version)
            .where(PolicyRule.owner_scope_id == owner_scope_id, PolicyRule.rule_key == rule_key)
            .order_by(PolicyRule.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    rule = PolicyRule(
        tenant_id=owner.tenant_id,
        owner_scope_id=owner_scope_id,
        rule_key=rule_key,
        domain=domain,
        title=title,
        expression=expression,
        parameters=parameters,
        strictness=strictness,
        test_cases=test_cases,
        valid_from=valid_from,
        valid_to=valid_to,
        mandatory_below=mandatory_below,
        version=(latest or 0) + 1,
        status="draft",
    )
    session.add(rule)
    await session.flush()
    return rule.id


async def policy_rule_owner(session: AsyncSession, rule_id: uuid.UUID) -> uuid.UUID:
    rule = await session.get(PolicyRule, rule_id)
    if rule is None:
        raise NotFound("policy rule not found")
    return rule.owner_scope_id


async def publish_policy_rule(session: AsyncSession, rule_id: uuid.UUID) -> None:
    rule = await session.get(PolicyRule, rule_id)
    if rule is None:
        raise NotFound("policy rule not found")
    if rule.status != "draft":
        raise Conflict("only draft rules can be published")
    spec = _spec(rule)
    check_publishable(spec)

    # Tightening: compare with the nearest published rule of the same key above this scope.
    ancestors = (
        await session.execute(
            select(ScopeClosure.ancestor_id, ScopeClosure.depth)
            .where(ScopeClosure.descendant_id == rule.owner_scope_id, ScopeClosure.depth > 0)
            .order_by(ScopeClosure.depth)
        )
    ).all()
    for ancestor_id, _depth in ancestors:
        parent_rule = (
            (
                await session.execute(
                    select(PolicyRule).where(
                        PolicyRule.owner_scope_id == ancestor_id,
                        PolicyRule.rule_key == rule.rule_key,
                        PolicyRule.status == "published",
                    )
                )
            )
            .scalars()
            .first()
        )
        if parent_rule is not None:
            check_tightens(_spec(parent_rule), spec)
            break

    same_scope = (
        await session.execute(
            select(PolicyRule).where(
                PolicyRule.owner_scope_id == rule.owner_scope_id,
                PolicyRule.rule_key == rule.rule_key,
                PolicyRule.status == "published",
            )
        )
    ).scalars()
    detect_conflicts(spec, (_spec(r) for r in same_scope))

    rule.status = "published"
    rule.published_at = utcnow()
    rule.published_by = _actor()
    publish(
        session,
        EventEnvelope(
            event_type=events.POLICY_PUBLISHED,
            tenant_id=rule.tenant_id,
            scope_node_id=rule.owner_scope_id,
            aggregate_type="policy_rule",
            aggregate_id=rule.id,
            actor_id=_actor(),
            correlation_id=_correlation(),
            payload={"rule_key": rule.rule_key, "version": rule.version, "domain": rule.domain},
        ),
    )
    await session.flush()


# --------------------------------------------------------------------------- configuration (P4)


async def set_config(
    session: AsyncSession,
    *,
    scope_node_id: uuid.UUID,
    key: str,
    value: Any,
    valid_from: date | None = None,
    locked_below: bool = False,
) -> uuid.UUID:
    """Write a new version of a setting at a scope in the caller's tenant (ctx-checked)."""
    from datetime import timedelta

    from syntegra.core.clock import today

    start = valid_from or today()
    # A locked ancestor setting cannot be overridden below its owner.
    resolved = await _resolve(session, scope_node_id, key, start)
    if resolved is not None and resolved.locked_below and resolved.scope_node_id != scope_node_id:
        raise ValidationFailed(
            f"{key} is fixed by a higher level", next_action="Request a policy exception."
        )
    current = (
        (
            await session.execute(
                select(ConfigSetting).where(
                    ConfigSetting.scope_node_id == scope_node_id,
                    ConfigSetting.setting_key == key,
                    ConfigSetting.valid_to.is_(None),
                )
            )
        )
        .scalars()
        .first()
    )
    if current is not None:
        if start <= current.valid_from:
            raise Conflict("a newer version already starts on or after this date")
        current.valid_to = start - timedelta(days=1)
    row = ConfigSetting(
        scope_node_id=scope_node_id,
        setting_key=key,
        value=value,
        valid_from=start,
        locked_below=locked_below,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _resolve(
    session: AsyncSession, scope_node_id: uuid.UUID, key: str, on: date
) -> ConfigSetting | None:
    # Configuration is inherited from ancestor scopes, which may belong to parent tenants.
    # This is the one sanctioned cross-tenant read: restricted to the ancestor chain of the
    # node, to the configuration table, and to the requested key.
    ancestry = (
        await session.execute(
            select(ScopeClosure.ancestor_id, ScopeClosure.depth).where(
                ScopeClosure.descendant_id == scope_node_id
            )
        )
    ).all()
    depth = {a: d for a, d in ancestry}
    rows = (
        (
            await session.execute(
                select(ConfigSetting)
                .where(
                    ConfigSetting.scope_node_id.in_(list(depth)),
                    ConfigSetting.setting_key == key,
                    ConfigSetting.valid_from <= on,
                    (ConfigSetting.valid_to.is_(None)) | (ConfigSetting.valid_to >= on),
                )
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    # A locked ancestor wins over anything below it; otherwise nearest wins.
    locked = [r for r in rows if r.locked_below]
    if locked:
        return max(locked, key=lambda r: depth[r.scope_node_id])
    return min(rows, key=lambda r: depth[r.scope_node_id])


async def resolve_config(
    session: AsyncSession, scope_node_id: uuid.UUID, key: str, on: date | None = None
) -> Any:
    from syntegra.core.clock import today

    row = await _resolve(session, scope_node_id, key, on or today())
    return None if row is None else row.value


# --------------------------------------------------------------------------- structure (P4)


async def apply_structure(
    session: AsyncSession,
    structure: Any,
    *,
    platform_scope_id: uuid.UUID,
    residency_region: str,
) -> tuple[TenantView, dict[str, uuid.UUID], list[str]]:
    """Build a jurisdiction from its structure file. Idempotent: existing items are kept.

    Returns the jurisdiction tenant, the scope id of each body by code, and what was created.
    """
    from syntegra.core.context import RequestContext, use_context
    from syntegra.governance.structure import Structure

    st: Structure = structure
    created: list[str] = []
    country = await tenant_by_code(session, st.country.code)
    if country is None:
        country = await create_child_tenant(
            session,
            parent_scope_id=platform_scope_id,
            code=st.country.code,
            name=st.country.name,
            level=GovernanceLevel.COUNTRY,
            country=st.country.code,
            residency_region=residency_region,
            time_zone=st.country.time_zone,
            local_label=st.country.label,
        )
        created.append(st.country.name)
    tenant = country
    if st.jurisdiction is not None:
        j = st.jurisdiction
        found = await tenant_by_code(session, j.code)
        if found is None:
            found = await create_child_tenant(
                session,
                parent_scope_id=country.root_scope_node_id,
                code=j.code,
                name=j.name,
                level=GovernanceLevel.REGION,
                country=st.country.code,
                residency_region=country.residency_region,
                time_zone=j.time_zone or st.country.time_zone,
                local_label=j.label,
            )
            created.append(j.name)
        tenant = found

    bodies: dict[str, uuid.UUID] = {}
    with use_context(
        RequestContext(
            tenant.id, tenant.root_scope_node_id, None, system=True, purpose="apply-structure"
        )
    ):

        async def node(
            parent: uuid.UUID, level: GovernanceLevel, code: str, name: str, label: str
        ) -> uuid.UUID:
            existing = await scope_node_by_code(session, tenant.id, code)
            if existing is not None:
                return existing.id
            made = await create_scope_node(
                session, parent_id=parent, level=level, code=code, name=name, local_label=label
            )
            created.append(code)
            return made.id

        for b in st.bodies:
            body_id = await node(tenant.root_scope_node_id, st.body_level, b.code, b.name, b.label)
            bodies[b.code] = body_id
            if b.units is not None:
                for unit in b.units.names:
                    await node(
                        body_id,
                        st.unit_level,
                        b.units.code_for(unit),
                        unit + b.units.name_suffix,
                        b.units.label,
                    )
            if (
                b.registers is not None
                and await resolve_config(session, body_id, "register.levels") is None
            ):
                await set_config(
                    session, scope_node_id=body_id, key="register.levels", value=list(b.registers)
                )
                created.append(f"{b.code} register levels")
    return tenant, bodies, created
