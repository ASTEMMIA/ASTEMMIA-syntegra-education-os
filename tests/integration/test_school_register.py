"""School register and bulk import under ASUBEB (ADR-010, ADR-012), against the database."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel
from syntegra.institutions import api as inst
from syntegra.institutions.models import Institution
from syntegra.platform.models import OutboxEvent
from tests.integration.test_api import as_

CSV = """name,lga,levels,government_code,town,phone
Community Primary School Ndume,Umuahia North,Primary,ASB/UN/001,Ndume,0803 123 4567
Government Secondary School Afara,Umuahia North LGEA,JSS & SSS,ASB/UN/002,Afara,
Central School Aba,Aba South,Pry,,Aba,
COMMUNITY PRIMARY SCHOOL NDUME,Umuahia North,Primary,,,
Nowhere School,Atlantis,Primary,,,
"""


async def asubeb(sm, world, lgas=("Umuahia North", "Aba South")):
    """State root -> ASUBEB (body) -> its LGEAs, as the structure file builds it."""
    async with sm() as s, s.begin():
        with use_context(RequestContext(world.abia_tenant, world.abia_root, None, system=True)):
            body = await governance.create_scope_node(
                s,
                parent_id=world.abia_root,
                level=GovernanceLevel.LOCAL_AUTHORITY,
                code="ASUBEB",
                name="Abia State Universal Basic Education Board",
                local_label="Board",
            )
            units = {}
            for name in lgas:
                n = await governance.create_scope_node(
                    s,
                    parent_id=body.id,
                    level=GovernanceLevel.NETWORK,
                    code="LGEA-" + name.upper().replace(" ", "-"),
                    name=f"{name} LGEA",
                    local_label="LGEA",
                )
                units[name] = n.id
    return body.id, units


@asynccontextmanager
async def at(world, scope):
    with use_context(RequestContext(world.abia_tenant, scope, world.state_officer)):
        yield


async def test_preview_registers_nothing_and_explains_every_error(sm, world):
    body, _ = await asubeb(sm, world)
    async with sm() as s, s.begin(), at(world, body):
        job_id = await inst.preview_import(s, CSV, source_name="schools.csv")
        job, rows = await inst.get_job(s, job_id)
        assert job.summary["valid"] == 3 and job.summary["errors"] == 2
        assert [r.status for r in rows] == ["valid", "valid", "valid", "error", "error"]
        assert "also appears on row 2" in rows[3].messages[0] and "Atlantis" in rows[4].messages[0]
        assert (await s.execute(select(Institution))).first() is None


async def test_commit_registers_schools_with_scope_nodes_ids_and_events(sm, world):
    body, units = await asubeb(sm, world)
    async with sm() as s, s.begin(), at(world, body):
        job_id = await inst.preview_import(s, CSV, source_name="schools.csv")
        summary = await inst.commit_import(s, job_id, managing_body="ASUBEB")
        assert summary["created"] == 3 and summary["skipped"] == 0
        schools = await inst.list_institutions(s)
        assert sorted(v.institution_code for v in schools) == [
            "INS-NG-000001",
            "INS-NG-000002",
            "INS-NG-000003",
        ]
        gss = next(v for v in schools if v.name.startswith("Government"))
        assert (
            gss.levels == ["JSS", "SSS"]
            and gss.location == "Umuahia North"
            and gss.institution_type == "School"
        )
        node = await governance.get_scope_node(s, gss.scope_node_id)
        assert (
            node.level == GovernanceLevel.INSTITUTION and node.parent_id == units["Umuahia North"]
        )
        assert (
            len(
                (
                    await s.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == "INS.REGISTERED")
                    )
                ).all()
            )
            == 3
        )
        with pytest.raises(Conflict):
            await inst.commit_import(s, job_id, managing_body="ASUBEB")


async def test_rows_registered_since_preview_are_skipped_not_duplicated(sm, world):
    body, _ = await asubeb(sm, world)
    async with sm() as s, s.begin(), at(world, body):
        first = await inst.preview_import(s, CSV, source_name="a.csv")
        second = await inst.preview_import(s, CSV, source_name="b.csv")
        await inst.commit_import(s, first, managing_body="ASUBEB")
        summary = await inst.commit_import(s, second, managing_body="ASUBEB")
        assert summary["created"] == 0 and summary["skipped"] == 3
        assert len((await s.execute(select(Institution))).all()) == 3


async def test_a_bad_file_or_wrong_scope_is_refused_with_a_next_step(sm, world):
    body, _ = await asubeb(sm, world)
    async with sm() as s, s.begin(), at(world, body):
        with pytest.raises(ValidationFailed, match="columns are missing"):
            await inst.preview_import(s, "school,place\nA,B\n", source_name=None)
        with pytest.raises(ValidationFailed, match="no institutions"):
            await inst.preview_import(s, "name,lga,levels\n", source_name=None)
    async with sm() as s, s.begin(), at(world, world.abia_root):
        with pytest.raises(ValidationFailed) as exc:  # the state root is not a body
            await inst.preview_import(s, CSV, source_name=None)
        assert "Switch to the body" in exc.value.next_action


async def test_api_import_flow_and_permissions(client, sm, world):
    body, _ = await asubeb(sm, world, lgas=("Umuahia North",))
    h = await as_(client, "state_officer", body)
    tpl = await client.get("/v1/bulk/institutions/template", headers=h)
    assert tpl.status_code == 200 and tpl.text.startswith("name,type,lga,levels")
    teacher = await as_(client, "teacher_a", world.school_a_campus_1, **{"Idempotency-Key": "x"})
    assert (
        await client.post("/v1/bulk/institutions/preview", content=CSV, headers=teacher)
    ).status_code == 403
    p = await client.post(
        "/v1/bulk/institutions/preview?source_name=schools.csv",
        content=CSV,
        headers=h | {"Idempotency-Key": "p1", "Content-Type": "text/csv"},
    )
    assert p.status_code == 201, p.text
    body_json = p.json()
    assert (
        body_json["summary"]["valid"] == 2 and body_json["summary"]["errors"] == 3
    )  # no Aba South here
    c = await client.post(
        f"/v1/bulk/jobs/{body_json['id']}/commit", headers=h | {"Idempotency-Key": "c1"}
    )
    assert c.status_code == 200 and c.json()["summary"]["created"] == 2
    lst = (await client.get("/v1/institutions?type=school", headers=h)).json()["items"]
    assert {v["name"] for v in lst} == {
        "Community Primary School Ndume",
        "Government Secondary School Afara",
    }
    assert {v["type"] for v in lst} == {"School"}
    one = await client.post(
        "/v1/institutions",
        json={"name": "Model Primary School", "lga": "Umuahia North", "levels": "Primary"},
        headers=h | {"Idempotency-Key": "s1"},
    )
    assert one.status_code == 201 and one.json()["institution_code"] == "INS-NG-000003"
