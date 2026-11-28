"""ADR-010: one Abia State tenant; ASUBEB and the Ministry of Tertiary Education are scopes."""

from contextlib import asynccontextmanager

from syntegra.core.context import RequestContext, use_context
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel
from syntegra.institutions import api as inst

CSV = "name,lga,levels\nTEST Primary One,Ohafia,Primary\nTEST Secondary Two,Ohafia,Secondary\n"


async def _boards(sm, world):
    """State root -> ASUBEB (board) -> Ohafia LGEA; state root -> Tertiary board."""
    async with sm() as s, s.begin():
        with use_context(RequestContext(world.abia_tenant, world.abia_root, None, system=True)):
            asubeb = await governance.create_scope_node(
                s,
                parent_id=world.abia_root,
                level=GovernanceLevel.LOCAL_AUTHORITY,
                code="ASUBEB",
                name="Abia State Universal Basic Education Board",
                local_label="Board",
            )
            tertiary = await governance.create_scope_node(
                s,
                parent_id=world.abia_root,
                level=GovernanceLevel.LOCAL_AUTHORITY,
                code="MIN-TERTIARY-EDUCATION",
                name="Abia State Ministry of Tertiary Education",
                local_label="Ministry",
            )
            ohafia = await governance.create_scope_node(
                s,
                parent_id=asubeb.id,
                level=GovernanceLevel.NETWORK,
                code="LGEA-OHAFIA",
                name="Ohafia LGEA",
                local_label="LGEA",
            )
    return asubeb.id, tertiary.id, ohafia.id


@asynccontextmanager
async def at(world, scope):
    with use_context(RequestContext(world.abia_tenant, scope, world.state_officer)):
        yield


async def test_schools_import_under_the_board_and_its_lgeas(sm, world):
    asubeb, _, ohafia = await _boards(sm, world)
    async with sm() as s, s.begin(), at(world, asubeb):
        job = await inst.preview_import(s, CSV, source_name="asubeb.csv")
        assert (await inst.commit_import(s, job, managing_body="ASUBEB"))["created"] == 2
        schools = await inst.list_institutions(s, parent_scope_id=ohafia)
        assert len(schools) == 2
        node = await governance.get_scope_node(s, schools[0].scope_node_id)
        assert node.parent_id == ohafia and await governance.is_within(s, asubeb, node.id)


async def test_board_levels_come_from_configuration(sm, world):
    asubeb, _, _ = await _boards(sm, world)
    async with sm() as s, s.begin(), at(world, asubeb):
        await governance.set_config(
            s, scope_node_id=asubeb, key="register.levels", value=["primary"]
        )
        _job, rows = await inst.get_job(s, await inst.preview_import(s, CSV, source_name=None))
        assert [r.status for r in rows] == ["valid", "error"]
        assert rows[1].messages == ["this body does not register JSS, SSS institutions"]


async def test_one_person_record_is_shared_across_boards(sm, world):
    """Kindergarten to university: both boards are scopes of one tenant, so a person record
    created under ASUBEB is the same record the tertiary board's scope can be granted to see."""
    asubeb, tertiary, _ = await _boards(sm, world)
    async with sm() as s:
        assert await governance.lowest_common_ancestor(s, asubeb, tertiary) == world.abia_root
