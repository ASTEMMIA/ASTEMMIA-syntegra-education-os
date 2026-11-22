"""Registration, classes, curriculum and teaching staff (ADR-029)."""

from datetime import date

import pytest

from syntegra.academic import api as acad
from syntegra.academic.domain import age_problem, class_name, next_year, year
from syntegra.core.clock import today
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.iam import api as iam
from syntegra.institutions import api as inst
from syntegra.learners import api as lrn
from syntegra.mdm.api import DuplicateBlocked
from tests.audit_helpers import verify_all_days
from tests.integration.test_staff import CRYPTO, _teacher, _world, as_

S1 = (
    (date(2026, 9, 14), date(2026, 12, 18)),
    (date(2027, 1, 11), date(2027, 4, 9)),
    (date(2027, 4, 26), date(2027, 7, 23)),
)
S2 = (
    (date(2027, 9, 13), date(2027, 12, 17)),
    (date(2028, 1, 10), date(2028, 4, 7)),
    (date(2028, 4, 24), date(2028, 7, 21)),
)
MORE_SCHOOLS = """name,lga,levels,government_code,address_line1,town,postcode
TEST Comprehensive Secondary School,Aba South,JSS; SSS,ASB/AS/003,3 Rd,Aba,450001
"""
POLY = """name,type,lga,levels,government_code,address_line1,town,postcode
TEST State Polytechnic,Poly,Aba South,ND/HND,,2 Polytechnic Road,Aba,450001
"""


def pupil(given="Chidi", family="Eze", born=date(2017, 3, 1), **kw):
    return lrn.PupilIn(
        given_name=given,
        family_name=family,
        date_of_birth=born,
        sex=kw.pop("sex", "M"),
        guardian_name=kw.pop("guardian_name", "Ngozi Eze"),
        guardian_phone=kw.pop("guardian_phone", "08031234567"),
        guardian_relationship=kw.pop("guardian_relationship", "mother"),
        **kw,
    )


async def _user(s, a, login, role, scope):
    u = await iam.create_user(s, tenant_id=a.tenant.id, login=login, display_name=login)
    await iam.grant_role(s, user_id=u.id, role_code=role, scope_node_id=scope)
    return u.id


async def _setup(sm, world):
    a = await _world(sm, world)
    a.school = a.aba.scope_node_id
    root = a.tenant.root_scope_node_id
    async with sm() as s, s.begin():
        with use_context(RequestContext(a.tenant.id, root, None, system=True)):
            a.version = await acad.seed_national_curriculum(s)
            await acad.adopt(s, a.version, years=None, from_session="2026/2027")
        with as_(a):  # ASUBEB sets the school calendar
            a.s1 = await acad.create_session(s, acad.SessionIn("2026/2027", list(S1)))
            await inst.commit_import(
                s,
                await inst.preview_import(s, MORE_SCHOOLS, source_name=None),
                managing_body="ASUBEB",
            )
            a.sec = next(
                v for v in await inst.list_institutions(s) if v.government_code == "ASB/AS/003"
            )
        with as_(a, scope=a.bodies["MIN-TERTIARY-EDUCATION"]):
            await inst.commit_import(
                s, await inst.preview_import(s, POLY, source_name=None), managing_body="Ministry"
            )
            a.poly = (await inst.list_institutions(s))[0]
    async with sm() as s, s.begin(), as_(a, a.hro):
        a.t1 = await _teacher(s, a, "Ada", "Okeke")
        a.t2 = await _teacher(s, a, "Uche", "Nwosu")
        a.t_umu = await _teacher(s, a, "Emeka", "Obi", school=a.umu)
        a.cook = await _teacher(s, a, "Bola", "Ade", category="non_teaching", position="Cook")
    async with sm() as s, s.begin(), as_(a, scope=root):
        a.reg = await _user(s, a, "registrar@central.example", "REG", a.school)
        a.head = await _user(s, a, "head@central.example", "HEAD", a.school)
        a.reg_umu = await _user(s, a, "registrar@township.example", "REG", a.umu.scope_node_id)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        a.room = await acad.add_room(s, a.aba.id, code="b4", name="Block B, room 4", capacity=45)
        a.p4a = await acad.create_class(
            s,
            a.aba.id,
            acad.ClassIn(
                a.s1.id, "P4", "A", capacity=40, room_id=a.room.id, class_teacher_staff_id=a.t1.id
            ),
        )
        a.p4b = await acad.create_class(s, a.aba.id, acad.ClassIn(a.s1.id, "P4", "B", capacity=1))
    return a


# --------------------------------------------------------------------------- rules


def test_years_and_ages():
    assert class_name(year("P4"), "A") == "Primary 4A"
    assert class_name(year("jss1"), "Gold") == "JSS 1 Gold"
    assert (
        next_year("P5").code == "P6"
        and next_year("P6") is None
        and next_year("100L").code == "200L"
    )
    assert age_problem(date(2017, 3, 1), year("P4"), date(2026, 9, 14)) is None  # 9 in Primary 4
    assert "usually 8-12" in age_problem(date(2021, 3, 1), year("P4"), date(2026, 9, 14))
    assert age_problem(date(1990, 1, 1), year("200L"), date(2026, 9, 14)) is None  # no band
    assert lrn.add_working_days(date(2026, 11, 20), 10) == date(2026, 12, 4)  # Friday + 10


# --------------------------------------------------------------------------- classes


async def test_calendar_curriculum_and_classes(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        # The school works under the board's calendar.
        [sess] = await acad.sessions(s)
        assert (sess.name, [p.name for p in sess.parts]) == (
            "2026/2027",
            ["First term", "Second term", "Third term"],
        )
        with pytest.raises(Conflict, match="only the office that set"):
            await acad.set_current(s, sess.parts[0].id)
        # The class took its subjects from the adopted curriculum.
        subjects = await acad.class_offerings(s, a.p4a.id)
        assert {o.subject.code for o in subjects if o.kind == "core"} == {
            "ENG",
            "MTH",
            "NGL",
            "BST",
            "RNV",
            "CCA",
            "HIS",
            "PVS",
            "FRE",
        }
        assert [o.subject.code for o in subjects if o.kind == "optional"] == ["ARB"]
        assert (a.p4a.name, a.p4a.class_teacher_name, a.p4a.room.code) == (
            "Primary 4A",
            "Ada Okeke",
            "B4",
        )
        with pytest.raises(Conflict, match="already exists"):
            await acad.create_class(s, a.aba.id, acad.ClassIn(a.s1.id, "P4", "A"))
        with pytest.raises(ValidationFailed, match="not registered for JSS"):
            await acad.create_class(s, a.aba.id, acad.ClassIn(a.s1.id, "JSS1", "A"))
        with pytest.raises(ValidationFailed, match="seats 45"):
            await acad.create_class(
                s, a.aba.id, acad.ClassIn(a.s1.id, "P5", "A", capacity=50, room_id=a.room.id)
            )
        with pytest.raises(Conflict, match="CMP is already used"):  # a state subject
            await acad.add_subject(s, a.aba.id, acad.CourseIn(code="cmp", name="Computer Studies"))
        local = await acad.add_subject(s, a.aba.id, acad.CourseIn(code="rob", name="Robotics club"))
        extra = await acad.add_class_subject(s, a.p4a.id, local.id, periods_per_week=2)
        assert (extra.subject.code, extra.subject.institution_id) == ("ROB", a.aba.id)
    async with sm() as s, s.begin(), as_(a, scope=a.tenant.root_scope_node_id):
        [cur] = await acad.curricula(s)
        assert (cur.code, cur.status, cur.adoptions[0]["years"]) == ("NG-SCHOOLS", "published", "*")


async def test_teaching_assignments(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        offered = {o.subject.code: o for o in await acad.class_offerings(s, a.p4a.id)}
        got = await acad.assign_teacher(s, offered["MTH"].id, a.t2.id)
        assert [t.staff_name for t in got.offering.teachers] == ["Uche Nwosu"]
        assert got.warnings == [
            "no current TRCN registration on record",
            "no verified teaching qualification on record",
        ]
        with pytest.raises(Conflict, match="already teaches"):
            await acad.assign_teacher(s, offered["MTH"].id, a.t2.id)
        await acad.assign_teacher(s, offered["MTH"].id, a.t1.id, role="assistant")
        with pytest.raises(NotFound):  # posted to another school: outside this school's scope
            await acad.assign_teacher(s, offered["ENG"].id, a.t_umu.id)
        with pytest.raises(Conflict, match="only teaching staff"):
            await acad.assign_teacher(s, offered["ENG"].id, a.cook.id)
        # A new lead replaces the old one (here, from today).
        swapped = await acad.assign_teacher(s, offered["MTH"].id, a.t1.id)
        assert [(t.staff_name, t.role) for t in swapped.offering.teachers] == [
            ("Ada Okeke", "lead")
        ]
        # The weekly load limit is enforced.
        for code in ("ENG", "BST", "RNV", "NGL", "PVS", "CCA"):
            await acad.assign_teacher(s, offered[code].id, a.t2.id)
        load, _ = await acad.teaching_load(s, a.t2.id)
        assert load == 6 + 5 + 4 + 3 + 3 + 2  # 23: Mathematics went to Ada
        club = await acad.add_subject(s, a.aba.id, acad.CourseIn(code="ROB", name="Robotics club"))
        heavy = await acad.add_class_subject(s, a.p4b.id, club.id, periods_per_week=14)
        with pytest.raises(Conflict, match="the limit is 36"):  # 23 + 14
            await acad.assign_teacher(s, heavy.id, a.t2.id)
        mine = await acad.classes_taught(s, await acad.staff_of_person(s, a.t2.person_id))
        assert [c.name for c in mine] == ["Primary 4A"]
        dropped = await acad.drop_offering(s, offered["ARB"].id)
        assert not dropped.active
        # Class teacher must be teaching staff posted here.
        with pytest.raises(Conflict, match="only teaching staff"):
            await acad.change_class(s, a.p4b.id, acad.ClassChange(class_teacher_staff_id=a.cook.id))


# --------------------------------------------------------------------------- registration


async def test_admission_application_to_enrolment(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        app = await lrn.submit_application(
            s,
            a.aba.id,
            lrn.ApplicationIn(
                session_id=a.s1.id,
                year_code="P4",
                given_name="Amara",
                family_name="Okafor",
                date_of_birth=date(2017, 6, 2),
                sex="F",
                guardian_name="Obinna Okafor",
                guardian_phone="0803 765 4321",
                guardian_relationship="father",
                previous_school="Home",
            ),
        )
        assert app.reference.startswith(f"ADM-{today().year}-") and app.status == "submitted"
        assert app.due_by == lrn.add_working_days(today(), 10) and not app.overdue
        assert app.guardian_phone == "+2348037654321"
        with pytest.raises(Conflict, match="cannot be offered now"):
            await lrn.move_application(s, app.id, "offer")
        await lrn.move_application(s, app.id, "review")
        assessed = await lrn.move_application(
            s, app.id, "assess", score="72.5", note="Good reading"
        )
        assert str(assessed.assessment_score) == "72.50"
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        with pytest.raises(ValidationFailed, match="give the reason"):
            await lrn.move_application(s, app.id, "reject")
        offered = await lrn.move_application(s, app.id, "offer", offer_days=7)
        assert offered.status == "offered" and offered.offer_expires_on == date.fromordinal(
            today().toordinal() + 7
        )
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        with pytest.raises(Conflict, match="accepted offer"):
            await lrn.enrol_applicant(s, CRYPTO, app.id, lrn.AdmitIn())
        await lrn.move_application(s, app.id, "accept", note="Mother called")
        e = await lrn.enrol_applicant(
            s, CRYPTO, app.id, lrn.AdmitIn(class_id=a.p4a.id, admission_number="2026/0201")
        )
        assert (e.class_name, e.source, e.year_name, e.admission_number) == (
            "Primary 4A",
            "admission",
            "Primary 4",
            "2026/0201",
        )
        assert e.learner_number.startswith("LRN-")
        done = await lrn.get_application(s, app.id)
        assert done.status == "enrolled" and [h["status"] for h in done.history] == [
            "submitted",
            "under_review",
            "assessment",
            "offered",
            "accepted",
            "enrolled",
        ]
        ln = await lrn.get_learner(s, e.learner_id)
        assert (ln.first_name, ln.current.class_name) == ("Amara", "Primary 4A")
        assert [(g.name, g.relationship, g.phone) for g in ln.guardians] == [
            ("Obinna Okafor", "father", "+2348037654321")
        ]
        assert [m.first_name for m in await lrn.class_list(s, a.p4a.id)] == ["Amara"]
        assert await verify_all_days(s, a.tenant.id)


async def test_direct_entry_duplicates_age_and_capacity(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        first = await lrn.register_direct(
            s, CRYPTO, a.aba.id, pupil(), lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id)
        )
        with pytest.raises(Conflict, match="already enrolled at this school"):
            await lrn.register_direct(
                s,
                CRYPTO,
                a.aba.id,
                pupil(person_id=(await lrn.get_learner(s, first.learner_id)).person_id),
                lrn.EnrolIn(a.s1.id, "P4"),
            )
        with pytest.raises(DuplicateBlocked):
            await lrn.register_direct(s, CRYPTO, a.aba.id, pupil(), lrn.EnrolIn(a.s1.id, "P4"))
        with pytest.raises(ValidationFailed, match="usually 8-12"):
            await lrn.register_direct(
                s,
                CRYPTO,
                a.aba.id,
                pupil("Ife", "Nnaji", date(2021, 1, 5), sex="F"),
                lrn.EnrolIn(a.s1.id, "P4"),
            )
        late = await lrn.register_direct(
            s,
            CRYPTO,
            a.aba.id,
            pupil("Ife", "Nnaji", date(2021, 1, 5), sex="F"),
            lrn.EnrolIn(
                a.s1.id, "P4", class_id=a.p4b.id, age_override="Skipped a year on assessment"
            ),
        )
        assert (
            late.age_override == "Skipped a year on assessment" and late.class_name == "Primary 4B"
        )
        with pytest.raises(Conflict, match="is full"):
            await lrn.place_in_class(s, first.id, a.p4b.id)
        # Siblings share the guardian's record.
        sib = await lrn.register_direct(
            s,
            CRYPTO,
            a.aba.id,
            pupil("Obi", "Eze", date(2016, 2, 1)),
            lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id),
        )
        g1 = (await lrn.get_learner(s, first.learner_id)).guardians[0].person_id
        g2 = (await lrn.get_learner(s, sib.learner_id)).guardians[0].person_id
        assert g1 == g2
        out = await lrn.end_enrolment(
            s, sib.id, status="withdrawn", reason="Family relocated abroad"
        )
        assert out.status == "withdrawn" and out.class_id is None
        assert [m.first_name for m in await lrn.class_list(s, a.p4a.id)] == ["Chidi"]


async def test_class_list_import(sm, world):
    a = await _setup(sm, world)
    head = ",".join(lrn.IMPORT_COLUMNS)
    csv_text = (
        head
        + """
Okeke,Ada,,F,2017-02-11,Primary 4A,2025/0101,Ifeoma Okeke,08031112222,mother
Nwankwo,Tobe,,M,01/09/2016,primary 4a,2025/0102,Chuks Nwankwo,08033334444,father
Bad,Sex,,Q,2017-01-01,Primary 4A,,Some One,08035556666,mother
Ghost,Class,,F,2017-01-01,Primary 9Z,,Some One,08035556666,mother
Okeke,Ada,,F,2017-02-11,Primary 4A,2025/0101,Ifeoma Okeke,08031112222,mother
Old,Learner,,M,2012-01-01,Primary 4B,,Ola Old,08037778888,guardian
Over,Capacity,,F,2017-05-05,Primary 4B,,Ola Old,08037778888,guardian
"""
    )
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        job_id = await lrn.preview_import(
            s, CRYPTO, a.aba.id, a.s1.id, csv_text, source_name="p4.csv"
        )
        job, rows = await lrn.import_job(s, job_id)
        assert job.summary["valid"] == 3 and job.summary["errors"] == 4
        msgs = {r.row_no: r.messages for r in rows}
        assert msgs[4] == ["sex is F or M"]
        assert "not open for 2026/2027" in msgs[5][0]
        assert msgs[6] == ["the same pupil appears twice in the file"]
        assert "over its capacity of 1" in msgs[8][0]
        assert rows[5].data["age_note"].startswith("the learner is 14")  # recorded, not refused
        summary = await lrn.commit_import(s, CRYPTO, job_id)
        assert (summary["created"], summary["skipped"]) == (3, 0)
        assert [m.surname for m in await lrn.class_list(s, a.p4a.id)] == ["Nwankwo", "Okeke"]
        with pytest.raises(Conflict, match="already been committed"):
            await lrn.commit_import(s, CRYPTO, job_id)


async def test_transfer_to_another_school(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        e = await lrn.register_direct(
            s, CRYPTO, a.aba.id, pupil(), lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id)
        )
        t = await lrn.request_transfer(s, e.id, a.umu.id, reason="Family moved to Umuahia")
        assert (t.from_institution, t.to_institution, t.status) == (
            "TEST Central School Aba",
            "TEST Township School Umuahia",
            "requested",
        )
        with pytest.raises(Conflict, match="already waiting"):
            await lrn.request_transfer(s, e.id, a.umu.id, reason="again")
    umu = a.umu.scope_node_id
    async with sm() as s, s.begin(), as_(a, a.head, umu):
        a.p4_umu = await acad.create_class(s, a.umu.id, acad.ClassIn(a.s1.id, "P4", "A"))
    async with sm() as s, s.begin(), as_(a, a.reg_umu, umu):
        [incoming] = await lrn.transfers(s, a.umu.id)
        assert incoming.learner_name == "Chidi Eze"
        with pytest.raises(NotFound):  # the receiving school cannot read the sending school's file
            await lrn.get_enrolment(s, e.id)
        done = await lrn.decide_transfer(s, incoming.id, accept=True, class_id=a.p4_umu.id)
        assert done.status == "accepted"
        ln = await lrn.get_learner(s, e.learner_id)
        assert (ln.current.institution_name, ln.current.class_name, ln.current.source) == (
            "TEST Township School Umuahia",
            "Primary 4A",
            "transfer",
        )
        assert [h.status for h in ln.history] == ["active", "transferred_out"]
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        with pytest.raises(NotFound):  # the learner has left this school's scope
            await lrn.get_learner(s, e.learner_id)
        assert await lrn.class_list(s, a.p4a.id) == []
        assert await verify_all_days(s, a.tenant.id)


async def test_promotion_into_the_next_session(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a):
        s2 = await acad.create_session(s, acad.SessionIn("2027/2028", list(S2)))
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        x = await lrn.register_direct(
            s, CRYPTO, a.aba.id, pupil(), lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id)
        )
        y = await lrn.register_direct(
            s,
            CRYPTO,
            a.aba.id,
            pupil("Kelechi", "Ude", date(2016, 8, 8)),
            lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id),
        )
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        p5a = await acad.create_class(s, a.aba.id, acad.ClassIn(s2.id, "P5", "A"))
        p4a2 = await acad.create_class(s, a.aba.id, acad.ClassIn(s2.id, "P4", "A"))
        with pytest.raises(ValidationFailed, match="Primary 5 class"):
            await lrn.promote(s, a.p4a.id, p4a2.id)
        r = await lrn.promote(
            s, a.p4a.id, p5a.id, repeat_class_id=p4a2.id, repeaters=[y.learner_id]
        )
        assert (r.promoted, r.repeated, r.to_class, r.repeat_class) == (
            1,
            1,
            "Primary 5A",
            "Primary 4A",
        )
        assert [m.first_name for m in await lrn.class_list(s, p5a.id)] == ["Chidi"]
        ln = await lrn.get_learner(s, x.learner_id)
        assert [(h.session_name, h.year_code, h.status) for h in ln.history] == [
            ("2027/2028", "P5", "active"),
            ("2026/2027", "P4", "completed"),
        ]


async def test_senior_secondary_subjects_and_tertiary_courses(sm, world):
    a = await _setup(sm, world)
    sec = a.sec.scope_node_id
    async with sm() as s, s.begin(), as_(a, scope=sec):
        ss1 = await acad.create_class(s, a.sec.id, acad.ClassIn(a.s1.id, "SSS1", "Science"))
        e = await lrn.register_direct(
            s,
            CRYPTO,
            a.sec.id,
            pupil("Nneka", "Agu", date(2011, 4, 4), sex="F"),
            lrn.EnrolIn(a.s1.id, "SSS1", class_id=ss1.id),
        )
        offered = {o.subject.code: o.id for o in await acad.class_offerings(s, ss1.id)}
        with pytest.raises(ValidationFailed, match="take 8 or 9"):
            await lrn.register_subjects(s, e.id, [offered["BIO"], offered["CHE"]])
        with pytest.raises(ValidationFailed, match="trade or entrepreneurship"):
            await lrn.register_subjects(
                s, e.id, [offered[c] for c in ("BIO", "CHE", "PHY", "AGR", "GEO")]
            )
        got = await lrn.register_subjects(
            s, e.id, [offered[c] for c in ("BIO", "CHE", "PHY", "AGR", "DPR")]
        )
        assert len(got) == 8 and {o.subject.code for o in got} >= {"ENGL", "MTH", "CIV", "DPR"}
        assert len(await lrn.registered_subjects(s, e.id)) == 8
    poly = a.poly.scope_node_id
    async with sm() as s, s.begin(), as_(a, scope=poly):
        sess = await acad.create_session(
            s,
            acad.SessionIn(
                "2026/2027",
                [(date(2026, 10, 5), date(2027, 2, 26)), (date(2027, 3, 15), date(2027, 7, 30))],
                tertiary=True,
            ),
        )
        first = sess.parts[0]
        courses = [
            await acad.add_subject(
                s,
                a.poly.id,
                acad.CourseIn(
                    code=c,
                    name=n,
                    units=u,
                    year_code="ND1",
                    semester=1,
                    department="Computer Science",
                ),
            )
            for c, n, u in (
                ("COM 111", "Introduction to Computing", 3),
                ("COM 112", "Programming in BASIC", 3),
                ("MTH 111", "Logic and Linear Algebra", 2),
                ("STA 111", "Descriptive Statistics", 3),
                ("GNS 101", "Use of English", 2),
                ("COM 113", "Computer Hardware", 3),
            )
        ]
        runs = [await acad.offer_course(s, first.id, c.id) for c in courses]
        with pytest.raises(ValidationFailed, match="semester 1 course"):
            await acad.offer_course(s, sess.parts[1].id, courses[0].id)
        lecturer_check = await acad.course_offerings(s, a.poly.id, first.id)
        assert [o.subject.code for o in lecturer_check][:2] == ["COM 111", "COM 112"]
        st = await lrn.register_direct(
            s,
            CRYPTO,
            a.poly.id,
            pupil("Ifeanyi", "Kalu", date(2006, 1, 1)),
            lrn.EnrolIn(sess.id, "ND1"),
        )
        with pytest.raises(ValidationFailed, match="8 units"):
            await lrn.register_subjects(s, st.id, [runs[0].id, runs[1].id, runs[4].id])
        ok = await lrn.register_subjects(s, st.id, [r.id for r in runs])
        assert sum(o.units for o in ok) == 16


# --------------------------------------------------------------------------- HTTP


async def test_api(client, sm, world):
    from sqlalchemy import update

    from syntegra.iam.models import AppUser
    from syntegra.platform.captcha import DEV_TOKEN

    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, scope=a.tenant.root_scope_node_id):
        me = await _user(s, a, "ada@central.example", "TCH", a.school)
        await s.execute(update(AppUser).where(AppUser.id == me).values(person_id=a.t1.person_id))

    async def login(name, scope):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {
            "Authorization": f"Bearer {r.json()['access_token']}",
            "X-Scope-Node-Id": str(scope),
        }

    head = await login("head@central.example", a.school)
    reg = await login("registrar@central.example", a.school)
    tch = await login("ada@central.example", a.school)
    n = iter(range(100))

    def k(h):
        return h | {"Idempotency-Key": f"k{next(n)}"}

    sessions = (await client.get("/v1/sessions", headers=head)).json()["items"]
    assert sessions[0]["name"] == "2026/2027"
    r = await client.post(
        f"/v1/institutions/{a.aba.id}/classes",
        json={"session_id": sessions[0]["id"], "year_code": "P5", "arm": "A", "capacity": 35},
        headers=k(head),
    )
    assert r.status_code == 201 and r.json()["name"] == "Primary 5A", r.text
    p5a = r.json()["id"]
    r = await client.post(
        f"/v1/institutions/{a.aba.id}/classes",
        json={"session_id": sessions[0]["id"], "year_code": "P5", "arm": "B"},
        headers=k(reg),
    )
    assert r.status_code == 403  # registrars register learners; heads open classes
    detail = (await client.get(f"/v1/classes/{p5a}", headers=head)).json()
    mth = next(o for o in detail["subjects"] if o["subject"]["code"] == "MTH")
    r = await client.post(
        f"/v1/offerings/{mth['id']}/teachers", json={"staff_id": str(a.t1.id)}, headers=k(head)
    )
    assert r.status_code == 200 and r.json()["warnings"][0].startswith("no current TRCN")
    body = {
        "pupil": {
            "given_name": "Chidi",
            "family_name": "Eze",
            "date_of_birth": "2016-03-01",
            "sex": "M",
            "guardian_name": "Ngozi Eze",
            "guardian_phone": "08031234567",
            "guardian_relationship": "mother",
        },
        "session_id": sessions[0]["id"],
        "year_code": "P5",
        "class_id": p5a,
    }
    r = await client.post(f"/v1/institutions/{a.aba.id}/learners", json=body, headers=k(reg))
    assert r.status_code == 201 and r.json()["class_name"] == "Primary 5A", r.text
    learner_id = r.json()["learner_id"]
    r = await client.post(
        f"/v1/institutions/{a.aba.id}/learners", json=body | {"class_id": None}, headers=k(reg)
    )
    assert r.status_code == 409 and r.json()["candidates"][0]["display_name"]
    # A teacher sees the lists of the classes they teach, and nothing else.
    mine = (await client.get("/v1/my/classes", headers=tch)).json()["items"]
    assert {c["name"] for c in mine} == {
        "Primary 4A",
        "Primary 5A",
    }  # class teacher of 4A, teaches 5A
    assert (await client.get(f"/v1/classes/{p5a}/learners", headers=tch)).json()["items"][0][
        "first_name"
    ] == "Chidi"
    assert (await client.get(f"/v1/classes/{a.p4b.id}/learners", headers=tch)).status_code == 403
    assert (await client.get(f"/v1/learners/{learner_id}", headers=tch)).status_code == 200
    assert (await client.get("/v1/learners", params={"q": "eze"}, headers=tch)).status_code == 403
    # Families can apply online; heads decide.
    online = {
        "session_id": sessions[0]["id"],
        "year_code": "P1",
        "given_name": "Ada",
        "family_name": "Obi",
        "date_of_birth": "2020-07-07",
        "sex": "F",
        "guardian_name": "Uju Obi",
        "guardian_phone": "08039990000",
        "guardian_relationship": "mother",
    }
    r = await client.post(f"/v1/public/NG-AB/institutions/{a.aba.id}/applications", json=online)
    assert r.status_code == 422 and "CAPTCHA" in r.text
    r = await client.post(
        f"/v1/public/NG-AB/institutions/{a.aba.id}/applications",
        json=online | {"captcha_token": DEV_TOKEN},
    )
    assert r.status_code == 201 and r.json()["reference"].startswith("ADM-"), r.text
    [app] = (await client.get(f"/v1/institutions/{a.aba.id}/applications", headers=reg)).json()[
        "items"
    ]
    assert (app["channel"], app["status"]) == ("online", "submitted")
    assert (
        await client.post(f"/v1/applications/{app['id']}/review", json={}, headers=k(reg))
    ).status_code == 200
    assert (
        await client.post(f"/v1/applications/{app['id']}/offer", json={}, headers=k(reg))
    ).status_code == 403
    r = await client.post(
        f"/v1/applications/{app['id']}/offer", json={"offer_days": 10}, headers=k(head)
    )
    assert r.json()["status"] == "offered"
    r = await client.get("/v1/schools", params={"q": "township"}, headers=reg)
    assert r.status_code == 200, r.text
    hits = r.json()["items"]
    assert [h["name"] for h in hits] == ["TEST Township School Umuahia"]
    r = await client.post(f"/v1/applications/{app['id']}/enrol", json={}, headers=k(reg))
    assert r.status_code == 422  # not an action
    assert (
        await client.post(f"/v1/applications/{app['id']}/accept", json={}, headers=k(reg))
    ).status_code == 200
    r = await client.post(
        f"/v1/applications/{app['id']}/enrolment",
        json={"admission_number": "2026/0300", "age_override": None},
        headers=k(reg),
    )
    assert r.status_code == 201 and r.json()["source"] == "admission", r.text
    r = await client.get("/v1/class-lists/template.csv", headers=reg)
    assert r.text.startswith("surname,first_name")
