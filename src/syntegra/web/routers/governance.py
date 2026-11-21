from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from syntegra.core.errors import Forbidden, NotFound
from syntegra.governance import api as governance
from syntegra.governance.policy.engine import RuleSpec, RuleTestCase, run_tests
from syntegra.web.deps import MAX_LIMIT, UowDep, decode_cursor, encode_cursor, remember, replay
from syntegra.web.schemas import (
    CreatedOut,
    Page,
    PolicyRuleCreate,
    PolicyRuleTestRequest,
    ScopeNodeCreate,
    ScopeNodeOut,
    TenantCreate,
    TenantOut,
)

router = APIRouter(prefix="/v1", tags=["governance"])


async def _visible(uow: UowDep, node_id: uuid.UUID) -> None:
    if not await governance.is_within(uow.session, uow.ctx.scope_node_id, node_id):
        # Finding a record never grants reading it (P6): same answer as "absent".
        raise NotFound("scope node not found")


@router.post("/tenants", status_code=201, response_model=TenantOut)
async def create_tenant(body: TenantCreate, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await _visible(uow, body.parent_scope_id)
    await uow.require("tenant:manage", body.parent_scope_id)
    parent_tenant = await governance.get_tenant(uow.session, uow.ctx.tenant_id)
    view = await governance.create_child_tenant(
        uow.session,
        parent_scope_id=body.parent_scope_id,
        code=body.code,
        name=body.name,
        level=body.level,
        country=body.country,
        residency_region=parent_tenant.residency_region,
        ownership=body.ownership,
        time_zone=body.time_zone,
        local_label=body.local_label,
    )
    return remember(
        uow,
        key,
        digest,
        201,
        TenantOut.model_validate(view, from_attributes=True).model_dump(mode="json"),
    )


@router.post("/scope-nodes", status_code=201, response_model=ScopeNodeOut)
async def create_scope_node(body: ScopeNodeCreate, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await _visible(uow, body.parent_id)
    parent = await governance.get_scope_node(uow.session, body.parent_id)
    if parent.tenant_id != uow.ctx.tenant_id:
        raise Forbidden(
            "you can only add scopes inside your own organisation",
            next_action="Create a child tenant instead.",
        )
    await uow.require("scope_node:manage", body.parent_id)
    view = await governance.create_scope_node(
        uow.session,
        parent_id=body.parent_id,
        level=body.level,
        code=body.code,
        name=body.name,
        local_label=body.local_label,
    )
    return remember(
        uow,
        key,
        digest,
        201,
        ScopeNodeOut.model_validate(view, from_attributes=True).model_dump(mode="json"),
    )


@router.get("/scope-nodes/{node_id}", response_model=ScopeNodeOut)
async def get_scope_node(node_id: uuid.UUID, uow: UowDep) -> ScopeNodeOut:
    await _visible(uow, node_id)
    return ScopeNodeOut.model_validate(
        await governance.get_scope_node(uow.session, node_id), from_attributes=True
    )


@router.get("/scope-nodes/{node_id}/children", response_model=Page[ScopeNodeOut])
async def list_children(
    node_id: uuid.UUID,
    uow: UowDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> Page[ScopeNodeOut]:
    await _visible(uow, node_id)
    items = await governance.list_children(
        uow.session, node_id, after=decode_cursor(cursor), limit=limit + 1
    )
    page = items[:limit]
    return Page[ScopeNodeOut](
        items=[ScopeNodeOut.model_validate(i, from_attributes=True) for i in page],
        next_cursor=encode_cursor(page[-1].id) if len(items) > limit else None,
    )


@router.post("/policy-rules", status_code=201, response_model=CreatedOut)
async def create_policy_rule(body: PolicyRuleCreate, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await _visible(uow, body.owner_scope_id)
    await uow.require("policy:publish", body.owner_scope_id)
    rule_id = await governance.create_policy_rule(
        uow.session,
        owner_scope_id=body.owner_scope_id,
        rule_key=body.rule_key,
        domain=body.domain,
        title=body.title,
        expression=body.expression,
        parameters=body.parameters,
        strictness=dict(body.strictness),
        test_cases=[t.model_dump() for t in body.test_cases],
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        mandatory_below=body.mandatory_below,
    )
    return remember(uow, key, digest, 201, {"id": str(rule_id)})


@router.post("/policy-rules/{rule_id}/publish", status_code=204)
async def publish_policy_rule(rule_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"rule_id": str(rule_id)})
    if replayed:
        return replayed
    owner = await governance.policy_rule_owner(uow.session, rule_id)
    await _visible(uow, owner)
    await uow.require("policy:publish", owner)
    await governance.publish_policy_rule(uow.session, rule_id)
    remember(uow, key, digest, 204, None)
    return JSONResponse(None, status_code=204)


@router.post("/policy-rules/test")
async def test_policy_rule(body: PolicyRuleTestRequest, uow: UowDep) -> dict[str, object]:
    """Dry run: compile the expression and run the test cases without saving anything."""
    from datetime import date

    spec = RuleSpec(
        rule_key="DRY-RUN-00",
        owner_scope_id=uow.ctx.scope_node_id,
        expression=body.expression,
        valid_from=date.today(),
        parameters=body.parameters,
        tests=tuple(RuleTestCase(t.name, t.facts, t.expect) for t in body.test_cases),
    )
    failures = run_tests(spec)
    return {
        "passed": not failures,
        "failures": [{"name": f.name, "message": f.message} for f in failures],
    }
