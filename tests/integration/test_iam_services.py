import pytest

from syntegra.core.context import use_context
from syntegra.core.errors import Conflict, Forbidden, ValidationFailed
from syntegra.iam import api as iam
from syntegra.iam.domain import Cell, Clearance


async def test_grant_evaluation_through_the_hierarchy(sm, world):
    async with sm() as s:
        # A state officer's grant at the state node covers a school's scope beneath it.
        d = await iam.check(
            s,
            user_id=world.state_officer,
            permission="learner:view",
            target_scope_id=world.school_a_root,
        )
        assert d.allowed and d.cell is Cell.AGGREGATE
        d = await iam.check(
            s,
            user_id=world.teacher_a,
            permission="learner:view",
            target_scope_id=world.school_a_campus_2,
        )
        assert not d.allowed


async def test_revoked_grant_stops_working(sm, world):
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root, world.head_a)):
            (g,) = await iam.grants_for(s, world.teacher_a)
            await iam.revoke_grant(s, g.grant_id)
            assert await iam.grants_for(s, world.teacher_a) == []


async def test_grant_must_stay_inside_users_tenant(sm, world):
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root, world.head_a)):
            with pytest.raises(ValidationFailed, match="own tenant"):
                await iam.grant_role(
                    s, user_id=world.teacher_a, role_code="TCH", scope_node_id=world.school_b_root
                )
            with pytest.raises(ValidationFailed, match="clearance cap"):
                await iam.grant_role(
                    s,
                    user_id=world.teacher_a,
                    role_code="TCH",
                    scope_node_id=world.school_a_root,
                    clearance_cap=Clearance.SPECIAL,
                )


async def test_clone_role_rules(sm, world):
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root, world.head_a)):
            rid = await iam.clone_role(
                s,
                tenant_id=world.school_a_tenant,
                source_code="TCH",
                new_code="TCH-LEAD",
                name="Lead teacher",
                permissions=None,
                creator_id=world.head_a,
            )
            assert rid
            # A teacher-level creator cannot mint a role reaching safeguarding cases.
            with pytest.raises(Forbidden):
                await iam.clone_role(
                    s,
                    tenant_id=world.school_a_tenant,
                    source_code="DSL",
                    new_code="DSL-COPY",
                    name="x",
                    permissions={"safeguarding_case:view": "Y"},
                    creator_id=world.teacher_a,
                )
            with pytest.raises(Conflict):
                await iam.clone_role(
                    s,
                    tenant_id=world.school_a_tenant,
                    source_code="TCH",
                    new_code="HEAD",
                    name="x",
                    permissions=None,
                    creator_id=world.head_a,
                )
            with pytest.raises(Forbidden, match="cannot be edited"):
                await iam.assert_can_edit_role(
                    s, (await iam._role(s, "TCH", world.school_a_tenant)).id
                )
            await iam.assert_can_edit_role(s, rid)
