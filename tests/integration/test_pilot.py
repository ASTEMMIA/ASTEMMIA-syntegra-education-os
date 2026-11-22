"""Pilot institutions, demonstration data and the monitoring dashboard (ADR-031).

The real pilot list is confidential and never in the repository: these tests use made-up
institutions in the same shape.
"""

from datetime import date

import pytest
from sqlalchemy import func, select

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.learners import api as lrn
from syntegra.mdm import api as mdm
from syntegra.mdm.models import Person
from syntegra.monitoring import api as mon
from syntegra.pilot import api as pilot
from syntegra.pilot.names import PHONE_PREFIX, Names
from tests.audit_helpers import verify_all_days
from tests.integration.test_staff import CRYPTO, _world, as_

PILOT = """board,name,type,lga,levels,address_line1,town,postcode,latitude,longitude
ASUBEB,TEST Pilot Primary School,,Aba North,Primary,1 Rd,Aba,450101,5.1200,7.3700
ASUBEB,TEST Pilot Girls Secondary School,,Umuahia North,JSS; SSS,2 Rd,Umuahia,440236,5.52,7.49
MIN-TERTIARY-EDUCATION,TEST Pilot Technical College,Technical college,Umuahia South,Technical,3 Rd,Umuahia,440236,5.47,7.49
MIN-TERTIARY-EDUCATION,TEST Pilot Polytechnic,Poly,Osisioma,ND/HND,4 Rd,Aba,450102,5.145,7.335
"""  # noqa: E501
ABA_CENTRAL = PILOT.replace("Aba North,Primary", "Aba Central,Primary")


def test_allocation_and_names():
    assert pilot.allocate(2500, [6, 6, 6, 6, 6, 3, 4]) == [406, 406, 405, 405, 405, 203, 270]
    assert sum(pilot.allocate(7, [1, 1, 1])) == 7 and pilot.allocate(5, [0, 0]) == [0, 0]
    assert pilot.single_sex("TEST Girls Secondary School") == "F"
    assert pilot.single_sex("Boys' High School") == "M"
    assert pilot.single_sex("TEST High School") is None
    names = Names(1)
    kids = [names.child(9, date(2026, 9, 14)) for _ in range(300)]
    assert len({(k.given, k.family, k.born) for k in kids}) == 300  # all different
    assert all(k.guardian_phone.startswith(PHONE_PREFIX) for k in kids)  # never a mobile
    assert all(date(2016, 9, 15) <= k.born <= date(2017, 9, 14) for k in kids)  # age 9 at start
    assert {k.sex for k in (names.child(14, date(2026, 9, 14), "F") for _ in range(50))} == {"F"}
    same = Names(1)  # the same seed makes the same people
    assert [same.child(9, date(2026, 9, 14)).given for _ in range(3)] == [k.given for k in kids[:3]]


async def test_pilot_institutions_load_all_or_nothing(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin():
        with pytest.raises(ValidationFailed, match="nothing was registered") as e:
            await pilot.load_institutions(s, a.tenant.id, ABA_CENTRAL)
        assert "Aba Central" in (e.value.next_action or "")
        with pytest.raises(ValidationFailed, match="'board' column"):
            await pilot.load_institutions(s, a.tenant.id, "name,lga\nX,Aba North\n")
    async with sm() as s, s.begin():
        first = await pilot.load_institutions(s, a.tenant.id, PILOT)
    assert [(p.board, p.lga, p.created) for p in first] == [
        ("ASUBEB", "Aba North", True),
        ("ASUBEB", "Umuahia North", True),
        ("MIN-TERTIARY-EDUCATION", "Umuahia South", True),
        ("MIN-TERTIARY-EDUCATION", "Osisioma", True),
    ]
    async with sm() as s, s.begin():
        again = await pilot.load_institutions(s, a.tenant.id, PILOT)
    assert [p.id for p in again] == [p.id for p in first] and not any(p.created for p in again)


async def test_demonstration_data_and_monitoring(sm, world, client):
    a = await _world(sm, world)
    async with sm() as s, s.begin():
        pilots = await pilot.load_institutions(s, a.tenant.id, PILOT)
        out = await pilot.load_demo(s, CRYPTO, a.tenant.id, pilots, learners_total=150, seed=7)
    by = {x.name: x for x in out.schools}
    assert out.learners == 150 and sum(x.learners for x in out.schools) == 150
    assert by["TEST Pilot Polytechnic"].classes == 0  # tertiary learners take courses
    assert by["TEST Pilot Polytechnic"].teachers >= 1
    assert all(x.teachers >= x.classes for x in out.schools)
    async with sm() as s, s.begin():
        with pytest.raises(Conflict, match="already loaded"):
            await pilot.load_demo(s, CRYPTO, a.tenant.id, pilots, learners_total=10)
        assert await pilot.demo_loaded(s, a.tenant.id) == 150
        # Every person made is marked: each learner, their guardian, each teacher.
        demo_people = (
            await s.execute(
                select(func.count(Person.id))
                .where(Person.source_system == mdm.DEMO_SOURCE)
                .execution_options(**pilot.BYPASS)
            )
        ).scalar_one()
        assert demo_people == 150 + 150 + out.teachers
    girls = next(p for p in pilots if "Girls" in p.name)
    primary = next(p for p in pilots if "Primary" in p.name)
    async with sm() as s, s.begin(), as_(a, scope=girls.scope_node_id):
        found = await lrn.find_learners(s, "DEMO/", institution_id=girls.id)
        assert found and all(e.demo for e in found)
        sexes = {(await lrn.get_learner(s, e.learner_id)).sex for e in found}
        assert sexes == {"F"}  # a girls' school
    # A real child with exactly a demo child's name and birthday is not matched with it.
    async with sm() as s, s.begin(), as_(a, scope=primary.scope_node_id):
        demo_kid = (await lrn.find_learners(s, "DEMO/", institution_id=primary.id))[0]
        k = await lrn.get_learner(s, demo_kid.learner_id)
        assert k.demo and k.guardians[0].phone.startswith("+234700")
        sess = demo_kid.session_id
        real = await lrn.register_direct(
            s,
            CRYPTO,
            primary.id,
            lrn.PupilIn(
                given_name=k.first_name,
                family_name=k.surname,
                other_names=k.other_names,
                date_of_birth=k.date_of_birth,
                sex=k.sex,
                guardian_name="Real Parent",
                guardian_phone="08031230000",
                guardian_relationship="mother",
            ),
            lrn.EnrolIn(sess, demo_kid.year_code),
        )
        assert real.learner_id != k.id and not real.demo
    # The State's dashboard: everything beneath it, demonstration data counted apart.
    async with sm() as s:
        with use_context(RequestContext(a.tenant.id, a.tenant.root_scope_node_id, a.sta)):
            o = await mon.overview(s)
    assert (o.institutions, o.learners, o.demo_learners) == (6, 151, 150)  # + 2 in the world
    assert o.girls + o.boys == 151 and o.teachers == out.teachers == o.demo_teachers
    assert o.classes == out.classes and o.pupil_teacher_ratio == round(151 / out.teachers, 1)
    assert {a.lga for a in o.by_lga} >= {"Aba North", "Umuahia North", "Umuahia South", "Osisioma"}
    assert {x["area"] for x in o.not_yet} == {"Infrastructure", "Inspection reports"}
    assert o.attendance_rate is None and o.results == 0  # no registers or results yet
    assert next(x for x in o.schools if x.id == girls.id).boys == 0
    assert all(x.latitude and x.longitude for x in o.schools if x.id in {p.id for p in pilots})
    # A school sees only itself.
    async with sm() as s:
        with use_context(RequestContext(a.tenant.id, primary.scope_node_id, None, system=True)):
            mine = await mon.overview(s)
    assert [x.id for x in mine.schools] == [primary.id] and mine.learners == by[
        primary.name
    ].learners + 1

    # Over HTTP: the state officer may look; the HR officer may not.
    async def login(name):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = await client.get("/v1/monitoring/overview", headers=await login("dps@abia.example"))
    assert r.status_code == 200 and r.json()["demo_learners"] == 150, r.text
    assert "Real Parent" not in r.text and k.first_name not in r.text  # counts, not names
    r = await client.get("/v1/monitoring/overview", headers=await login("hr@asubeb.example"))
    assert r.status_code == 403
    async with sm() as s:
        assert await verify_all_days(s, a.tenant.id)
