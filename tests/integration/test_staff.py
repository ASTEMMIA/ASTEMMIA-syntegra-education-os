"""Staff 360 and postings (ADR-024)."""

from datetime import date, timedelta

import pytest

from syntegra.core.clock import today
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.hr import api as hr
from syntegra.iam import api as iam
from syntegra.institutions import api as inst
from syntegra.institutions import bodies as eb
from syntegra.institutions.body_rules import load_catalogue
from syntegra.mdm import api as mdm
from syntegra.platform.crypto import build_field_crypto
from tests.audit_helpers import verify_all_days
from tests.integration.test_tertiary_register import _abia

CRYPTO = build_field_crypto(
    env="test", kek_b64=None, previous_keks_b64=[], blind_index_key_b64=None
)
SCHOOLS = """name,lga,levels,government_code,address_line1,town,postcode
TEST Central School Aba,Aba North,Primary,ASB/AN/001,1 Rd,Aba,450001
TEST Township School Umuahia,Umuahia North,Primary; JSS,ASB/UN/002,2 Rd,Umuahia,440001
"""
LONG_AGO = date(2010, 1, 4)


class Abia:
    pass


async def _world(sm, world):
    a = Abia()
    a.tenant, a.bodies = await _abia(sm, world)
    a.asubeb = a.bodies["ASUBEB"]
    async with sm() as s, s.begin():
        await eb.seed_catalogue(s, load_catalogue("ng"))
        with use_context(RequestContext(a.tenant.id, a.asubeb, None, system=True)):
            await inst.commit_import(
                s, await inst.preview_import(s, SCHOOLS, source_name=None), managing_body="ASUBEB"
            )
            schools = {v.government_code: v for v in await inst.list_institutions(s)}
        a.aba, a.umu = schools["ASB/AN/001"], schools["ASB/UN/002"]
        with use_context(
            RequestContext(a.tenant.id, a.tenant.root_scope_node_id, None, system=True)
        ):
            hro = await iam.create_user(
                s, tenant_id=a.tenant.id, login="hr@asubeb.example", display_name="HR"
            )
            await iam.grant_role(s, user_id=hro.id, role_code="HRO", scope_node_id=a.asubeb)
            sta = await iam.create_user(
                s, tenant_id=a.tenant.id, login="dps@abia.example", display_name="DPS"
            )
            await iam.grant_role(
                s, user_id=sta.id, role_code="STA", scope_node_id=a.tenant.root_scope_node_id
            )
        a.hro, a.sta = hro.id, sta.id
    return a


class as_:
    """A request context usable with both ``with`` and ``async with``."""

    def __init__(self, a, user=None, scope=None):
        self._cm = use_context(
            RequestContext(a.tenant.id, scope or a.asubeb, user, system=user is None)
        )

    def __enter__(self):
        return self._cm.__enter__()

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *exc):
        return self.__exit__(*exc)


async def _person(s, given, family, born=date(1975, 3, 1), sex="F"):
    from syntegra.mdm.domain import Sex

    r = await mdm.create_person(
        s,
        CRYPTO,
        mdm.PersonIn(
            given_name=given,
            family_name=family,
            other_names="Ngozi",
            date_of_birth=born,
            sex_at_registration=Sex(sex),
        ),
    )
    return r.person_id


async def _teacher(s, a, given="Ada", family="Okeke", *, school=None, **kw):
    pid = await _person(s, given, family)
    return await hr.appoint(
        s,
        hr.AppointmentIn(
            person_id=pid,
            category=kw.pop("category", "teaching"),
            position=kw.pop("position", "Classroom teacher"),
            start_date=kw.pop("start_date", LONG_AGO),
            institution_id=(school or a.aba).id,
            grade_level=kw.pop("grade_level", 8),
            step=kw.pop("step", 2),
            salary_structure="CONPSS",
            first_appointment_date=kw.pop("first_appointment_date", date(2000, 9, 1)),
            **kw,
        ),
    )


# --------------------------------------------------------------------------- appointment


async def test_appointment_staff_number_and_retirement(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        t = await _teacher(s, a, government_file_number="asb/t/0001")
        assert (
            t.staff_number.startswith("STF-0000-0001-") and t.government_file_number == "ASB/T/0001"
        )
        assert (t.surname, t.first_name, t.other_names, t.sex) == ("Okeke", "Ada", "Ngozi", "F")
        post = t.current_post
        assert post.place_name == "TEST Central School Aba" and post.grade_level == 8
        assert post.appointment_type == "posting"  # first appointed elsewhere in 2000
        # Teachers: 65 years of age or 40 years of service, whichever first.
        assert t.retirement_date == date(2040, 3, 1)
        n = await _teacher(s, a, "Emeka", "Obi", category="non_teaching", position="Bursar")
        assert n.retirement_date == date(2035, 3, 1)  # 60 / 35 for other staff
        assert t.years_of_service == today().year - 2000 - (today() < date(today().year, 9, 1))


async def test_appointment_checks(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        t = await _teacher(s, a, government_file_number="ASB/T/9")
        with pytest.raises(Conflict, match="already a staff member"):
            await hr.appoint(
                s,
                hr.AppointmentIn(
                    person_id=t.person_id,
                    category="teaching",
                    position="Teacher",
                    start_date=LONG_AGO,
                    institution_id=a.aba.id,
                ),
            )
        with pytest.raises(Conflict, match="file number ASB/T/9"):
            await _teacher(s, a, "Bola", "Ade", government_file_number="asb/t/9")
        with pytest.raises(ValidationFailed, match="grade level must be 1 to 17"):
            await _teacher(s, a, "Chi", "Eze", grade_level=19)
        with pytest.raises(ValidationFailed, match="first appointment cannot be after"):
            await _teacher(
                s,
                a,
                "Dayo",
                "Ibe",
                first_appointment_date=date(2020, 1, 1),
                start_date=date(2015, 1, 1),
            )


async def test_a_school_sees_its_own_staff(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        await _teacher(s, a)
        await _teacher(s, a, "Obinna", "Uche", school=a.umu)
    async with sm() as s:
        with as_(a, scope=a.aba.scope_node_id):
            assert [v.surname for v in await hr.nominal_roll(s)] == ["Okeke"]
        with as_(a, scope=a.umu.scope_node_id):
            assert [v.surname for v in await hr.nominal_roll(s)] == ["Uche"]
        with as_(a):
            assert len(await hr.nominal_roll(s)) == 2
        with as_(a, scope=a.bodies["MIN-TERTIARY-EDUCATION"]):
            assert await hr.nominal_roll(s) == []


# --------------------------------------------------------------------------- qualifications


async def test_qualifications_trcn_and_qualified_teacher(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        t = await _teacher(s, a)
        t = await hr.add_qualification(
            s, t.id, kind="nce", title="NCE Mathematics/Physics", year=1999
        )
        t = await hr.add_qualification(s, t.id, kind="degree", title="B.Sc Chemistry", year=2004)
        t = await hr.add_qualification(s, t.id, kind="degree", title="B.Ed Mathematics", year=2008)
        teaching = {q.title: q.teaching for q in t.qualifications}
        assert teaching == {
            "NCE Mathematics/Physics": True,
            "B.Sc Chemistry": False,
            "B.Ed Mathematics": True,
        }
        assert t.highest_qualification.kind == "degree" and not t.qualified_teacher
        for q in t.qualifications:
            t = await hr.verify_qualification(s, q.id)
        t = await hr.set_registration(
            s,
            t.id,
            number="trcn/2009/1234",
            registered_on=date(2009, 5, 1),
            expires_on=today() + timedelta(days=200),
        )
        assert t.registered_teacher and t.qualified_teacher
        assert t.registrations[0].number == "TRCN/2009/1234"
        other = await _teacher(s, a, "Uche", "Nwosu")
        with pytest.raises(Conflict, match="recorded for another staff member"):
            await hr.set_registration(s, other.id, number="TRCN/2009/1234")
        with pytest.raises(ValidationFailed, match="not a teacher registration body"):
            await hr.set_registration(s, other.id, number="X1", body_code="WAEC")
        with pytest.raises(ValidationFailed, match="not known"):
            await hr.add_qualification(s, other.id, kind="black belt", title="Karate")
        t = await hr.set_registration(s, t.id, number="TRCN/2009/1234", status="expired")
        assert not t.qualified_teacher


# --------------------------------------------------------------------------- postings


async def test_transfer_needs_a_second_officer_and_moves_the_record(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        t = await _teacher(s, a)
        await hr.add_qualification(s, t.id, kind="nce", title="NCE English")
        req = await hr.request_posting(
            s,
            t.id,
            hr.PostingIn(
                appointment_type="transfer",
                effective_date=today(),
                institution_id=a.umu.id,
                reference="ASUBEB/PST/2026/14",
                reason="Staffing need at Township School",
            ),
        )
        assert req.status == "pending" and req.to_place == "TEST Township School Umuahia"
        with pytest.raises(Conflict, match="four-eyes"):
            await hr.decide_posting(s, req.id, approve=True)
        with pytest.raises(Conflict, match="already waiting"):
            await hr.request_posting(
                s,
                t.id,
                hr.PostingIn(appointment_type="promotion", effective_date=today(), grade_level=9),
            )
    async with sm() as s, s.begin(), as_(a, a.sta, a.tenant.root_scope_node_id):
        done = await hr.decide_posting(s, req.id, approve=True, note="Approved")
        assert done.status == "approved"
        v = await hr.get_staff(s, t.id)
        old, new = sorted(v.posts, key=lambda p: p.start_date)
        assert old.end_date == today() - timedelta(days=1) and not old.current
        assert new.current and new.place_name == "TEST Township School Umuahia"
        assert new.appointment_type == "transfer" and new.grade_level == 8 and new.step == 2
    async with sm() as s:
        with as_(a, scope=a.umu.scope_node_id):
            (moved,) = await hr.nominal_roll(s)
            assert moved.qualifications[0].title == "NCE English"  # it moved with them
        with as_(a, scope=a.aba.scope_node_id):
            assert await hr.nominal_roll(s) == []
        assert await verify_all_days(s, a.tenant.id)


async def test_promotion_rules_rejection_and_cancel(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        t = await _teacher(s, a)
        bad = [
            (
                hr.PostingIn(appointment_type="promotion", effective_date=today(), grade_level=7),
                "higher grade",
            ),
            (
                hr.PostingIn(
                    appointment_type="transfer", effective_date=today(), institution_id=a.aba.id
                ),
                "different place",
            ),
            (
                hr.PostingIn(appointment_type="promotion", effective_date=LONG_AGO, grade_level=9),
                "after the current post",
            ),
            (
                hr.PostingIn(appointment_type="first_appointment", effective_date=today()),
                "made by appointing",
            ),
        ]
        for data, message in bad:
            with pytest.raises(ValidationFailed, match=message):
                await hr.request_posting(s, t.id, data)
        r = await hr.request_posting(
            s,
            t.id,
            hr.PostingIn(
                appointment_type="promotion", effective_date=today(), grade_level=9, step=1
            ),
        )
        assert r.position == "Classroom teacher" and r.grade_level == 9
    async with sm() as s, s.begin(), as_(a, a.sta, a.tenant.root_scope_node_id):
        with pytest.raises(ValidationFailed, match="reason"):
            await hr.decide_posting(s, r.id, approve=False)
        with pytest.raises(Conflict, match="only the requester"):
            await hr.cancel_posting(s, r.id)
        assert (
            await hr.decide_posting(s, r.id, approve=False, note="Not due")
        ).status == "rejected"
        with pytest.raises(Conflict, match="already rejected"):
            await hr.decide_posting(s, r.id, approve=True)


# --------------------------------------------------------------------------- exits and the roll


async def test_exit_and_nominal_roll_filters(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        old = await _teacher(
            s,
            a,
            "Grace",
            "Old",
            first_appointment_date=date(1987, 1, 5),
            start_date=date(1987, 1, 5),
        )
        young = await _teacher(s, a, "Tolu", "Young", grade_level=10)
        bursar = await _teacher(
            s, a, "Ike", "Ego", category="non_teaching", position="Bursar", grade_level=12
        )
        await hr.set_registration(s, young.id, number="TRCN/1")
        roll = lambda **kw: hr.nominal_roll(s, hr.RollFilter(**kw))  # noqa: E731
        assert {v.surname for v in await roll(category="teaching")} == {"Old", "Young"}
        assert {v.surname for v in await roll(grade_min=10)} == {"Young", "Ego"}
        assert {v.surname for v in await roll(position="bursar")} == {"Ego"}
        assert {v.surname for v in await roll(unregistered_teachers=True)} == {"Old"}
        soon = await roll(retiring_within_days=365)  # 40 years since 1987 falls within a year
        assert [v.surname for v in soon] == ["Old"]
        (row,) = [
            x for x in await hr.staffing_summary(s) if x.place_name == "TEST Central School Aba"
        ]
        assert (
            row.teaching,
            row.non_teaching,
            row.registered_teachers,
            row.retiring_within_a_year,
        ) == (2, 1, 1, 1)
        gone = await hr.end_employment(s, old.id, exit_date=today(), reason="retirement")
        assert gone.status == "exited" and all(p.end_date == today() for p in gone.posts)
        assert {v.surname for v in await roll()} == {"Young", "Ego"}
        assert {v.surname for v in await roll(status="exited")} == {"Old"}
        with pytest.raises(Conflict, match="already left"):
            await hr.end_employment(s, old.id, exit_date=today(), reason="retirement")
        with pytest.raises(Conflict, match="has left"):
            await hr.request_posting(
                s,
                old.id,
                hr.PostingIn(appointment_type="promotion", effective_date=today(), grade_level=9),
            )
        assert bursar.category == "non_teaching"


# --------------------------------------------------------------------------- API


async def test_api(client, sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        pid = await _person(s, "Ada", "Okeke")

    async def login(name):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    hro = await login("hr@asubeb.example") | {"X-Scope-Node-Id": str(a.asubeb)}
    sta = await login("dps@abia.example")
    body = {
        "person_id": str(pid),
        "category": "teaching",
        "position": "Classroom teacher",
        "start_date": "2010-01-04",
        "institution_id": str(a.aba.id),
        "grade_level": 8,
        "step": 2,
        "salary_structure": "conpss",
    }
    r = await client.post("/v1/staff", json=body, headers=hro | {"Idempotency-Key": "s1"})
    assert r.status_code == 201, r.text
    staff = r.json()
    assert staff["current_post"]["institution"]["code"] == a.aba.institution_code
    assert staff["current_post"]["salary_structure"] == "CONPSS"
    roll = (await client.get("/v1/staff?category=teaching", headers=hro)).json()["items"]
    assert [x["staff_number"] for x in roll] == [staff["staff_number"]]
    csv_text = (await client.get("/v1/staff/nominal-roll.csv", headers=hro)).text
    assert csv_text.splitlines()[0].startswith("staff_number,file_number,surname")
    assert "Okeke,Ada,Ngozi,F,1975-03-01,teaching,TEST Central School Aba" in csv_text
    p = await client.post(
        f"/v1/staff/{staff['id']}/postings",
        json={
            "appointment_type": "transfer",
            "effective_date": today().isoformat(),
            "institution_id": str(a.umu.id),
        },
        headers=hro | {"Idempotency-Key": "s2"},
    )
    assert p.status_code == 201, p.text
    own = await client.post(
        f"/v1/posting-requests/{p.json()['id']}/approve",
        json={},
        headers=hro | {"Idempotency-Key": "s3"},
    )
    assert own.status_code == 409
    ok = await client.post(
        f"/v1/posting-requests/{p.json()['id']}/approve",
        json={},
        headers=sta | {"Idempotency-Key": "s4"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    summary = (await client.get("/v1/staffing-summary", headers=sta)).json()["items"]
    assert [(x["place"], x["teaching"]) for x in summary] == [("TEST Township School Umuahia", 1)]
    # After the transfer, the old school no longer sees the record.
    with pytest.raises(NotFound):
        async with sm() as s:
            with as_(a, scope=a.aba.scope_node_id):
                await hr.get_staff(s, staff["id"])
