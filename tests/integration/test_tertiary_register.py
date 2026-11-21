"""Tertiary register under the Abia State Ministry of Tertiary Education (ADR-012)."""

from sqlalchemy import select

from syntegra.core.context import RequestContext, use_context
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel
from syntegra.governance.structure import load_structure
from syntegra.institutions import api as inst
from syntegra.institutions.models import Institution

TERTIARY_CSV = """name,type,lga,levels,government_code,address_line1,town,postcode
TEST State University,University,Isuikwuato,Degree; Postgraduate,,1 University Road,Uturu,440001
TEST State Polytechnic,Poly,Aba South L.G.A.,ND/HND,,2 Polytechnic Road,Aba,450001
TEST College of Education,COE,Umuahia North,NCE; Degree,,3 College Road,Umuahia,440001
TEST College of Health Technology,College of Health,Ohafia,ND,,4 Health Road,Ohafia,440001
TEST Technical College,Technical College,Umuahia South,NTC/NBC,,5 Technical Road,Umuahia,440001
TEST Unknown Kind,Academy,Ohafia,Degree,,6 Road,Ohafia,440001
TEST Wrong Place Poly,Polytechnic,Lagos Island,ND,,7 Road,Lagos,100001
TEST Primary School,School,Ohafia,Primary,,8 Road,Ohafia,440001
TEST Poly No Type,,Ohafia,HND,,9 Road,Ohafia,440001
"""
ADDR = ",1 Main Road,Ohafia,440001"


async def _abia(sm, world):
    async with sm() as s, s.begin():
        tenant, bodies, _ = await governance.apply_structure(
            s,
            load_structure("ng-abia"),
            platform_scope_id=world.global_root,
            residency_region="af-south-1",
        )
    return tenant, bodies


async def test_ministry_registers_tertiary_institutions_directly_under_it(sm, world):
    tenant, bodies = await _abia(sm, world)
    ministry = bodies["MIN-TERTIARY-EDUCATION"]
    async with sm() as s, s.begin():
        with use_context(RequestContext(tenant.id, ministry, None, system=True)):
            job_id = await inst.preview_import(s, TERTIARY_CSV, source_name="tertiary.csv")
            job, rows = await inst.get_job(s, job_id)
            assert job.summary["valid"] == 5 and job.summary["errors"] == 4
            assert job.summary["by_type"] == {
                "University": 1,
                "Polytechnic": 1,
                "College of education": 1,
                "College of health": 1,
                "Technical college": 1,
            }
            msgs = {r.row_no: r.messages[0] for r in rows if r.status == "error"}
            assert "type 'Academy' is not recognised" in msgs[7]
            assert "not a local government area of this state" in msgs[8]
            assert "does not register Primary" in msgs[9]  # the Ministry does not register schools
            assert "type is required for tertiary" in msgs[10]
            assert (await inst.commit_import(s, job_id, managing_body="Ministry"))["created"] == 5

            uni = (
                await s.execute(
                    select(Institution).where(Institution.institution_type == "university")
                )
            ).scalar_one()
            assert uni.parent_scope_id == ministry and uni.location == "Isuikwuato"
            node = await governance.get_scope_node(s, uni.scope_node_id)
            assert node.parent_id == ministry and node.level == GovernanceLevel.INSTITUTION
            assert node.local_label == "University"
            poly = (await inst.list_institutions(s, institution_type="polytechnic"))[0]
            assert poly.levels == ["ND", "HND"] and poly.location == "Aba South"


async def test_asubeb_refuses_tertiary_and_ministry_refuses_schools(sm, world):
    tenant, bodies = await _abia(sm, world)
    async with sm() as s, s.begin():
        with use_context(RequestContext(tenant.id, bodies["ASUBEB"], None, system=True)):
            _job, rows = await inst.get_job(
                s,
                await inst.preview_import(
                    s,
                    "name,type,lga,levels,address_line1,town,postcode\n"
                    f"TEST Poly,Polytechnic,Ohafia,ND{ADDR}\nTEST Pry,,Ohafia,Primary{ADDR}\n",
                    source_name=None,
                ),
            )
            assert [r.status for r in rows] == ["error", "valid"]
            assert rows[0].messages == ["this body does not register ND institutions"]


async def test_school_and_university_in_one_lga_stay_under_their_own_bodies(sm, world):
    """Kindergarten to university in one state: same location, different oversight."""
    tenant, bodies = await _abia(sm, world)
    async with sm() as s, s.begin():
        with use_context(RequestContext(tenant.id, bodies["ASUBEB"], None, system=True)):
            await inst.commit_import(
                s,
                await inst.preview_import(
                    s,
                    "name,lga,levels,address_line1,town,postcode\n"
                    f"TEST Pry Ohafia,Ohafia,Primary{ADDR}\n",
                    source_name=None,
                ),
                managing_body="ASUBEB",
            )
        with use_context(
            RequestContext(tenant.id, bodies["MIN-TERTIARY-EDUCATION"], None, system=True)
        ):
            await inst.commit_import(
                s,
                await inst.preview_import(
                    s,
                    "name,type,lga,levels,address_line1,town,postcode\n"
                    f"TEST Poly Ohafia,Polytechnic,Ohafia,ND{ADDR}\n",
                    source_name=None,
                ),
                managing_body="Ministry",
            )
        with use_context(RequestContext(tenant.id, tenant.root_scope_node_id, None)):
            both = await inst.list_institutions(s)
            assert {v.location for v in both} == {"Ohafia"}
            school = next(v for v in both if v.institution_type == "School")
            poly = next(v for v in both if v.institution_type == "Polytechnic")
            assert await governance.is_within(s, bodies["ASUBEB"], school.scope_node_id)
            assert await governance.is_within(
                s, bodies["MIN-TERTIARY-EDUCATION"], poly.scope_node_id
            )
            assert not await governance.is_within(s, bodies["ASUBEB"], poly.scope_node_id)
