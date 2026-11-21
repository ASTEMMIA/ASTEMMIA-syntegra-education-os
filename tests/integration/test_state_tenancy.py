"""ADR-009: government schools are scopes inside one state tenant (ASUBEB). Duplicate
reviews between schools go to the lowest scope that covers both: the LGEA, else the state."""

from contextlib import asynccontextmanager
from datetime import date

from sqlalchemy import select

from syntegra.core.context import RequestContext, use_context
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel
from syntegra.mdm import api as mdm
from syntegra.mdm.domain import Sex
from syntegra.mdm.models import MatchReview
from tests.integration.test_mdm import CRYPTO


@asynccontextmanager
async def as_registrar(world, scope):
    with use_context(RequestContext(world.abia_tenant, scope, None)):
        yield


async def _schools(sm, world):
    """State tenant: two LGEAs; Umuahia North has two schools, Aba South one."""
    async with sm() as s, s.begin():
        with use_context(RequestContext(world.abia_tenant, world.abia_root, None, system=True)):
            aba = await governance.create_scope_node(
                s,
                parent_id=world.abia_root,
                level=GovernanceLevel.LOCAL_AUTHORITY,
                code="LGEA-ABA-SOUTH",
                name="Aba South LGEA",
            )
            mk = lambda parent, code: governance.create_scope_node(  # noqa: E731
                s, parent_id=parent, level=GovernanceLevel.INSTITUTION, code=code, name=code
            )
            x = await mk(world.lgea, "GPS-UMUAHIA-1")
            y = await mk(world.lgea, "GSS-UMUAHIA-2")
            z = await mk(aba.id, "GPS-ABA-1")
    return x.id, y.id, z.id, aba.id


def child(given="Chidera", scope=None):
    return mdm.PersonIn(
        given_name=given,
        family_name="Okonkwo",
        date_of_birth=date(2015, 4, 2),
        sex_at_registration=Sex.FEMALE,
        scope_node_id=scope,
    )


async def _review_scope(sm, world, first_scope, second_scope):
    async with sm() as s, s.begin(), as_registrar(world, first_scope):
        await mdm.create_person(s, CRYPTO, child(scope=first_scope))
    async with sm() as s, s.begin(), as_registrar(world, second_scope):
        r = await mdm.create_person(s, CRYPTO, child(scope=second_scope))
        found = await mdm.find_candidates(s, CRYPTO, child(scope=second_scope))
        assert found and not found[0].visible  # the other school's child stays hidden
    async with sm() as s:
        with use_context(RequestContext(world.abia_tenant, world.abia_root, None)):
            review = (
                await s.execute(select(MatchReview).where(MatchReview.person_id == r.person_id))
            ).scalar_one()
    return review.scope_node_id


async def test_same_lgea_review_goes_to_the_lgea(sm, world):
    x, y, _, _ = await _schools(sm, world)
    assert await _review_scope(sm, world, x, y) == world.lgea


async def test_different_lgeas_review_goes_to_the_state(sm, world):
    x, _, z, _ = await _schools(sm, world)
    assert await _review_scope(sm, world, x, z) == world.abia_root


async def test_lowest_common_ancestor(sm, world):
    x, y, z, aba = await _schools(sm, world)
    async with sm() as s:
        assert await governance.lowest_common_ancestor(s, x, y) == world.lgea
        assert await governance.lowest_common_ancestor(s, x, z) == world.abia_root
        assert await governance.lowest_common_ancestor(s, x, x) == x
        assert await governance.lowest_common_ancestor(s, z, aba) == aba
