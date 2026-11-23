"""Attendance, continuous assessment and examinations (ADR-032)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from syntegra.academic import api as acad
from syntegra.assessment import api as ass
from syntegra.assessment.domain import DEFAULTS, Policy, check_policy, competition_rank, gpa
from syntegra.assessment.models import SheetEvent
from syntegra.attendance import api as att
from syntegra.attendance.models import AttendanceCorrection
from syntegra.core.errors import Conflict, Forbidden, ValidationFailed
from syntegra.iam.models import AppUser
from syntegra.learners import api as lrn
from tests.audit_helpers import verify_all_days
from tests.integration.test_learners import _setup, _user, pupil
from tests.integration.test_staff import CRYPTO, as_

FRIDAY = datetime(2026, 11, 20, 10, 30, tzinfo=att.LOCAL)
TERM_START = date(2026, 9, 14)


@pytest.fixture
def friday(monkeypatch):
    monkeypatch.setattr(att, "local_now", lambda: FRIDAY)


def test_grading_rules():
    senior, nbte, nuc = DEFAULTS["senior"], DEFAULTS["nbte"], DEFAULTS["nuc"]
    assert [senior.grade(Decimal(v)).grade for v in (100, 75, 74.5, 50, 49.99, 0)] == [
        "A1", "A1", "B2", "C6", "D7", "F9",
    ]  # fmt: skip
    assert nbte.grade(Decimal(72)).points == Decimal("3.50")
    assert gpa([(Decimal("4.00"), 3), (Decimal("2.00"), 1)]) == Decimal("3.50")
    assert gpa([(None, 3)]) is None
    assert nbte.award(Decimal("3.50")) == "Distinction" and nbte.award(Decimal("1.99")) == "Fail"
    assert nuc.award(Decimal("4.50")) == "First class"
    assert competition_rank({"a": Decimal(80), "b": Decimal(80), "c": Decimal(70)}) == {
        "a": 1, "b": 1, "c": 3,
    }  # fmt: skip
    assert Policy.from_json(senior.to_json()) == senior
    bad = Policy.from_json({**DEFAULTS["basic"].to_json(), "components": [
        {"code": "CA1", "name": "Test", "max_score": 30}, {"code": "EXAM", "name": "Exam", "max_score": 60},  # noqa: E501
    ]})  # fmt: skip
    assert check_policy(bad) == ["the parts add up to 100"]


async def _class(sm, world, n=3):
    """Primary 4A with ``n`` pupils from the start of term, Ada (class teacher) signed in."""
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, scope=a.tenant.root_scope_node_id):
        a.ada = await _user(s, a, "ada@central.example", "TCH", a.school)
        await s.execute(update(AppUser).where(AppUser.id == a.ada).values(person_id=a.t1.person_id))
    a.kids = []
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        for given, family, phone in (
            ("Chidi", "Eze", "08031110001"),
            ("Amaka", "Obi", "08031110002"),
            ("Tobe", "Nwosu", "08031110003"),
            ("Ada", "Kalu", "08031110004"),
        )[:n]:
            e = await lrn.register_direct(
                s,
                CRYPTO,
                a.aba.id,
                pupil(given, family, guardian_phone=phone),
                lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id, starts_on=TERM_START),
            )
            a.kids.append(e.learner_id)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        a.term1, a.term2 = (await acad.get_session(s, a.s1.id)).parts[:2]
    return a


def marks(kids, *statuses, reason=None):
    return [att.MarkIn(k, st, reason if st == "excused" else None) for k, st in zip(kids, statuses)]


async def test_registers(sm, world, friday):
    a = await _class(sm, world)
    k = a.kids
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        empty = await att.get_register(s, a.p4a.id, date(2026, 11, 20), "am")
        assert not empty.taken and [m.status for m in empty.marks] == [None, None, None]
        for day, why in (
            (date(2026, 11, 14), "weekend"),
            (date(2026, 11, 23), "has not come"),
            (date(2026, 12, 21), "has not come"),
        ):
            with pytest.raises(ValidationFailed, match=why):
                await att.take_register(
                    s, a.p4a.id, day, "am", marks(k, "present", "present", "present")
                )
        with pytest.raises(ValidationFailed, match="1 not marked"):
            await att.take_register(
                s, a.p4a.id, date(2026, 11, 20), "am", marks(k[:2], "present", "present")
            )
        with pytest.raises(ValidationFailed, match="needs a reason"):
            await att.take_register(
                s, a.p4a.id, date(2026, 11, 20), "am", marks(k, "present", "late", "excused")
            )
        r = await att.take_register(
            s,
            a.p4a.id,
            date(2026, 11, 20),
            "am",
            marks(k, "present", "late", "excused", reason="Malaria"),
        )
        assert r.taken and r.counts == {"present": 1, "late": 1, "absent": 0, "excused": 1}
        # The same day, the teacher may change her mind.
        r = await att.take_register(
            s, a.p4a.id, date(2026, 11, 20), "am", marks(k, "present", "present", "absent")
        )
        assert r.counts["absent"] == 1
        # Yesterday's missed register, taken late; but not changed afterwards without the head.
        await att.take_register(
            s, a.p4a.id, date(2026, 11, 19), "am", marks(k, "present", "absent", "present")
        )
        with pytest.raises(Forbidden, match="after the day"):
            await att.take_register(
                s, a.p4a.id, date(2026, 11, 19), "am", marks(k, "present", "present", "present")
            )
        with pytest.raises(Forbidden, match="7 days late"):
            await att.take_register(
                s, a.p4a.id, date(2026, 11, 9), "am", marks(k, "present", "present", "present")
            )
    # The State closes every school for a public holiday.
    async with sm() as s, s.begin(), as_(a, a.sta, a.tenant.root_scope_node_id):
        await att.add_closure(s, date(2026, 10, 1), "Independence Day")
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        with pytest.raises(ValidationFailed, match="closed \\(Independence Day\\)"):
            await att.take_register(
                s, a.p4a.id, date(2026, 10, 1), "am", marks(k, "present", "present", "present")
            )
        assert [c.reason for c in await att.closures(s, date(2026, 9, 1), date(2026, 12, 31))] == [
            "Independence Day"
        ]
        with pytest.raises(ValidationFailed, match="say why"):
            await att.take_register(
                s,
                a.p4a.id,
                date(2026, 11, 19),
                "am",
                marks(k, "present", "present", "present"),
                may_amend=True,
            )
        await att.take_register(
            s, a.p4a.id, date(2026, 11, 19), "am", marks(k, "present", "present", "present"),
            may_amend=True, amend_reason="Amaka was at the clinic, signed in late by the nurse",
        )  # fmt: skip
        [corr] = (await s.execute(select(AttendanceCorrection))).scalars().all()
        assert (corr.old_status, corr.new_status) == ("absent", "present")
        # Persistent absence: Tobe misses most of two weeks.
        for day in att.school_days_back(date(2026, 11, 18), 6):
            for sess in ("am", "pm"):
                await att.take_register(
                    s, a.p4a.id, day, sess, marks(k, "present", "late", "absent"), may_amend=True
                )
        report = await att.class_attendance(s, a.p4a.id, a.term1.id)
        lines = {ln.learner_id: ln for ln in report.learners}
        assert report.registers == 14
        assert lines[k[0]].summary == {
            "opened": 14, "present": 14, "late": 0, "absent": 0, "excused": 0, "rate": 1.0,
        }  # fmt: skip
        assert lines[k[1]].summary["late"] == 12 and not lines[k[1]].persistent
        assert lines[k[2]].persistent and lines[k[2]].summary["absent"] == 13
        assert (await att.learner_summary(s, k[2], a.term1.id)).rate == round(1 / 14, 3)
        day = await att.school_day(s, a.aba.id, date(2026, 11, 20))
        assert {c.class_name: (c.am, c.pm) for c in day.classes}["Primary 4A"] == (True, False)
    async with sm() as s:
        assert await verify_all_days(s, a.tenant.id)


async def test_staff_attendance(sm, world, monkeypatch):
    a = await _class(sm, world, n=1)
    monkeypatch.setattr(att, "local_now", lambda: datetime(2026, 11, 20, 8, 25, tzinfo=att.LOCAL))
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        me = await att.my_day(s, a.t1.person_id)
        assert me.institution_name == a.aba.name and me.status is None
        me = await att.sign(s, a.t1.person_id, "in")
        assert (me.status, str(me.time_in)) == ("late", "08:25:00")  # after 08:00
        with pytest.raises(Conflict, match="signed in at 08:25"):
            await att.sign(s, a.t1.person_id, "in")
    monkeypatch.setattr(att, "local_now", lambda: datetime(2026, 11, 20, 15, 5, tzinfo=att.LOCAL))
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        assert str((await att.sign(s, a.t1.person_id, "out")).time_out) == "15:05:00"
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        day = await att.record_staff(
            s, a.aba.id, date(2026, 11, 20), [att.StaffIn(a.t2.id, "sick", note="Hospital note")]
        )
        lines = {ln.staff_id: ln for ln in day}
        assert lines[a.t1.id].status == "late" and lines[a.t1.id].self_recorded
        assert lines[a.t2.id].status == "sick" and not lines[a.t2.id].self_recorded
        with pytest.raises(ValidationFailed, match="not posted"):
            await att.record_staff(
                s, a.aba.id, date(2026, 11, 20), [att.StaffIn(a.t_umu.id, "present")]
            )


async def _score_all(s, a, offering_ids, totals):
    """Give each pupil the same split of ``totals`` in every subject."""
    for oid in offering_ids:
        await ass.enter_scores(
            s,
            oid,
            a.term1.id,
            [
                ass.ScoreIn(kid, part, Decimal(str(round(v, 2))))
                for kid, t in zip(a.kids, totals)
                for part, v in (("CA1", t * 0.2), ("CA2", t * 0.2), ("EXAM", t * 0.6))
            ],
        )


async def test_results_report_cards_and_transcripts(sm, world, friday):
    a = await _class(sm, world)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        offered = await acad.class_offerings(s, a.p4a.id)
        subjects = {o.subject.code: o.id for o in offered if o.kind == "core"}
        await acad.assign_teacher(s, subjects["MTH"], a.t1.id)
    mth = subjects["MTH"]
    # Ada marks Mathematics; the rules are the basic-education defaults.
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        assert await ass.teaches(s, [a.t1.id], mth) and not await ass.teaches(
            s, [a.t1.id], subjects["ENG"]
        )
        with pytest.raises(ValidationFailed, match="CA1 is out of 20"):
            await ass.enter_scores(s, mth, a.term1.id, [ass.ScoreIn(a.kids[0], "CA1", Decimal(21))])
        with pytest.raises(ValidationFailed, match="not on this sheet"):
            await ass.enter_scores(s, mth, a.term1.id, [ass.ScoreIn(a.kids[0], "PROJ", Decimal(5))])
        v = await ass.enter_scores(
            s, mth, a.term1.id,
            [ass.ScoreIn(a.kids[0], "CA1", Decimal("17.5")), ass.ScoreIn(a.kids[0], "CA2", Decimal(16)),  # noqa: E501
             ass.ScoreIn(a.kids[0], "EXAM", None, absent=True)],
        )  # fmt: skip
        assert v.lines[0].total is None or v.missing == 2
        with pytest.raises(ValidationFailed, match="2 learner"):
            await ass.submit(s, mth, a.term1.id)
        await _score_all(s, a, [mth], [80, 80, 55])
        v = await ass.submit(s, mth, a.term1.id)
        by = {ln.learner_id: ln for ln in v.lines}  # lines are in surname order
        assert v.status == "submitted" and [by[k].position for k in a.kids] == [1, 1, 3]
        assert [by[k].grade for k in a.kids] == ["A", "A", "C"] and v.passed == 3
        with pytest.raises(Conflict, match="submitted"):
            await ass.enter_scores(s, mth, a.term1.id, [ass.ScoreIn(a.kids[0], "CA1", Decimal(1))])
    # The head returns it, Ada corrects and resubmits, the head approves.
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        await ass.send_back(s, mth, a.term1.id, "Check Tobe's examination script")
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        await ass.enter_scores(s, mth, a.term1.id, [ass.ScoreIn(a.kids[2], "EXAM", Decimal(40))])
        await ass.submit(s, mth, a.term1.id)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        assert (await ass.approve(s, mth, a.term1.id)).status == "approved"
        # The head marks the other subjects; someone else must approve what the head submitted.
        others = [oid for code, oid in subjects.items() if code != "MTH"]
        await _score_all(s, a, others, [70, 60, 50])
        for oid in others:
            await ass.submit(s, oid, a.term1.id)
        with pytest.raises(Forbidden, match="you submitted"):
            await ass.approve(s, others[0], a.term1.id)
        preview = await ass.class_results(s, a.p4a.id, a.term1.id)
        assert not preview.ready and len(preview.waiting) == len(others)
        with pytest.raises(Conflict, match="not approved yet"):
            await ass.publish_reports(s, a.p4a.id, a.term1.id)
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        for oid in others:
            await ass.approve(s, oid, a.term1.id)
    # Attendance goes on the report card.
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        await att.take_register(
            s, a.p4a.id, date(2026, 11, 20), "am", marks(a.kids, "present", "present", "absent")
        )
        await ass.set_comment(
            s, a.kids[0], a.term1.id, class_teacher="A careful and cheerful pupil."
        )
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        await ass.set_comment(s, a.kids[0], a.term1.id, head="Keep it up.")
        r = await ass.publish_reports(s, a.p4a.id, a.term1.id)
        assert r.published == 3 and r.ready
        with pytest.raises(Conflict, match="nothing has changed"):
            await ass.publish_reports(s, a.p4a.id, a.term1.id)
        [card] = await ass.report_cards(s, a.kids[0])
        c = card.content
        assert c["class"]["name"] == "Primary 4A" and c["term"]["name"] == "First term"
        maths = next(x for x in c["subjects"] if x["code"] == "MTH")
        assert maths["parts"] == {"CA1": "16.00", "CA2": "16.00", "EXAM": "48.00"}
        assert (maths["total"], maths["grade"], maths["position"]) == ("80.00", "A", "1st")
        assert maths["highest"] == "80.00" and maths["lowest"] == "62.00"  # Tobe, re-marked
        assert c["summary"]["position"] == "1st" and c["summary"]["out_of"] == 3
        assert c["attendance"]["opened"] == 1 and c["attendance"]["present"] == 1
        assert c["comments"] == {
            "class_teacher": "A careful and cheerful pupil.",
            "head": "Keep it up.",
        }
        assert c["next_term_begins"] == "2027-01-11"
        # A pupil who joins after the results were approved has no result to report yet.
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        late = await lrn.register_direct(
            s,
            CRYPTO,
            a.aba.id,
            pupil("Ebuka", "Late", guardian_phone="08031119999"),
            lrn.EnrolIn(a.s1.id, "P4", class_id=a.p4a.id, starts_on=date(2026, 11, 16)),
        )
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        preview = await ass.class_results(s, a.p4a.id, a.term1.id)
        assert str(late.learner_id) not in {c["learner"]["id"] for c in preview.cards}
        # A correction after publishing: reopen with a reason, change, approve, republish.
        await ass.send_back(s, mth, a.term1.id, "Marking error found on re-check")
    async with sm() as s, s.begin(), as_(a, a.ada, a.school):
        await ass.enter_scores(s, mth, a.term1.id, [ass.ScoreIn(a.kids[0], "EXAM", Decimal(50))])
        with pytest.raises(ValidationFailed, match="1 learner"):  # the newcomer needs scores too
            await ass.submit(s, mth, a.term1.id)
        await ass.enter_scores(
            s,
            mth,
            a.term1.id,
            [
                ass.ScoreIn(late.learner_id, p, Decimal(v))
                for p, v in (("CA1", 10), ("CA2", 11), ("EXAM", 30))
            ],
        )
        await ass.submit(s, mth, a.term1.id)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        await ass.approve(s, mth, a.term1.id)
        await ass.publish_reports(s, a.p4a.id, a.term1.id)
        [card2] = await ass.report_cards(s, a.kids[0])
        assert card2.version == 2 and card2.content_sha256 != card.content_sha256
        assert [
            e.action
            for e in (
                await s.execute(
                    select(SheetEvent)
                    .where(SheetEvent.sheet_id.isnot(None))
                    .order_by(SheetEvent.at)
                )
            ).scalars()
            if e.action in ("returned", "reopened")
        ] == ["returned", "reopened"]
        # The transcript gathers the published cards.
        t = await ass.issue_transcript(s, a.kids[0], "Admission to JSS 1")
        assert t.reference.startswith("TRN-") and t.content["school_terms"][0]["average"]
        with pytest.raises(ValidationFailed, match="say what the transcript is for"):
            await ass.issue_transcript(s, a.kids[0], "x")
    async with sm() as s:
        assert await verify_all_days(s, a.tenant.id)


async def test_exam_timetable_and_policies(sm, world, friday):
    a = await _class(sm, world, n=2)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        subjects = {o.subject.code: o.id for o in await acad.class_offerings(s, a.p4a.id)}
        room = a.room.id
        nine = datetime(2026, 12, 7, 9, 0)
        p = await ass.schedule_paper(
            s, ass.PaperIn(subjects["MTH"], a.term1.id, nine, 90, room_id=room)
        )
        assert p.ends_at == datetime(2026, 12, 7, 10, 30) and p.room == "Block B, room 4"
        with pytest.raises(Conflict, match="have Mathematics"):
            await ass.schedule_paper(
                s, ass.PaperIn(subjects["ENG"], a.term1.id, datetime(2026, 12, 7, 10, 0), 60)
            )
        with pytest.raises(ValidationFailed, match="outside First term"):
            await ass.schedule_paper(
                s, ass.PaperIn(subjects["ENG"], a.term1.id, datetime(2027, 1, 5, 9), 60)
            )
        await ass.schedule_paper(
            s, ass.PaperIn(subjects["ENG"], a.term1.id, datetime(2026, 12, 7, 10, 30), 60)
        )  # straight after: no clash
        p4b_maths = next(
            o.id for o in await acad.class_offerings(s, a.p4b.id) if o.subject.code == "MTH"
        )
        with pytest.raises(Conflict, match="room is in use"):
            await ass.schedule_paper(s, ass.PaperIn(p4b_maths, a.term1.id, nine, 60, room_id=room))
        assert [x.subject for x in await ass.timetable(s, a.aba.id, a.term1.id)] == [
            "Mathematics", "English Studies",
        ]  # fmt: skip
        await ass.cancel_paper(s, p.id)
        assert len(await ass.timetable(s, a.aba.id, a.term1.id)) == 1
    # The school sets its own rules for basic education: sheets opened after use them.
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        rules = {
            **DEFAULTS["basic"].to_json(),
            "components": [
                {"code": "CA1", "name": "Test", "max_score": 15},
                {"code": "PROJ", "name": "Project", "max_score": 15},
                {"code": "EXAM", "name": "Examination", "max_score": 70},
            ],
        }
        with pytest.raises(ValidationFailed, match="add up to 100"):
            await ass.set_policy(s, {**rules, "components": rules["components"][:2]})
        v = await ass.set_policy(s, rules)
        assert v.source == a.aba.name
        sheet = await ass.enter_scores(
            s, subjects["BST"], a.term1.id, [ass.ScoreIn(a.kids[0], "PROJ", Decimal(12))]
        )
        assert [c["code"] for c in sheet.rules["components"]] == ["CA1", "PROJ", "EXAM"]
    async with sm() as s, s.begin(), as_(a, a.reg_umu, a.umu.scope_node_id):
        other = {p.group: p for p in await ass.policies(s)}
        assert other["basic"].source == "default"  # another school keeps the State's rules


async def test_tertiary_gpa(sm, world, friday):
    a = await _setup(sm, world)
    poly = a.poly.scope_node_id
    async with sm() as s, s.begin(), as_(a, scope=poly):
        sess = await acad.create_session(
            s, acad.SessionIn("2026/2027", [(date(2026, 10, 5), date(2027, 2, 26)),
                                            (date(2027, 3, 15), date(2027, 7, 30))], tertiary=True)
        )  # fmt: skip
        first = sess.parts[0]
        runs = []
        for code, units in (("COM 111", 12), ("GNS 101", 4)):
            c = await acad.add_subject(
                s,
                a.poly.id,
                acad.CourseIn(code=code, name=code, units=units, year_code="ND1", semester=1),
            )
            runs.append(await acad.offer_course(s, first.id, c.id))
        st = await lrn.register_direct(
            s,
            CRYPTO,
            a.poly.id,
            pupil("Ifeanyi", "Kalu", date(2006, 1, 1)),
            lrn.EnrolIn(sess.id, "ND1"),
        )
        await lrn.register_subjects(s, st.id, [r.id for r in runs])
        for r, (ca, exam) in zip(runs, ((28, 50), (20, 25))):  # 78 -> AA 4.00; 45 -> D 2.25
            sheet = await ass.enter_scores(
                s, r.id, first.id,
                [ass.ScoreIn(st.learner_id, "CA", Decimal(ca)), ass.ScoreIn(st.learner_id, "EXAM", Decimal(exam))],  # noqa: E501
            )  # fmt: skip
            assert sheet.rules["group"] == "nbte"
            await ass.submit(s, r.id, first.id)
    async with sm() as s, s.begin(), as_(a, scope=poly):
        for r in runs:
            await ass.approve(s, r.id, first.id)
        [sem] = await ass.course_results(s, st.learner_id)
        assert [(c["grade"], c["points"]) for c in sem.courses] == [("AA", "4.00"), ("D", "2.25")]
        assert (sem.units, sem.gpa, sem.cgpa) == (16, "3.56", "3.56")  # (4x12 + 2.25x4) / 16
        tr = await ass.transcript(s, st.learner_id)
        assert tr["cgpa"] == "3.56" and tr["standing"] == "Distinction"


async def test_http_access(client, sm, world, friday):
    a = await _class(sm, world, n=2)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        offered = await acad.class_offerings(s, a.p4a.id)
        subjects = {o.subject.code: o.id for o in offered if o.kind == "core"}
        await acad.assign_teacher(s, subjects["MTH"], a.t1.id)
        await _score_all(s, a, list(subjects.values()), [75, 52])
        for oid in subjects.values():
            await ass.submit(s, oid, a.term1.id)
    async with sm() as s, s.begin(), as_(a, a.reg, a.school):
        for oid in subjects.values():
            await ass.approve(s, oid, a.term1.id)
        mother = (await lrn.get_learner(s, a.kids[0])).guardians[0].person_id
        await lrn.give_guardian_account(s, a.kids[0], mother, "mum@example.com", sign_in_url="x")

    async def login(name):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {
            "Authorization": f"Bearer {r.json()['access_token']}",
            "X-Scope-Node-Id": str(a.school),
        }

    ada, head, mum = (
        await login("ada@central.example"),
        await login("head@central.example"),
        await login("mum@example.com"),
    )
    n = iter(range(100))

    def k(h):
        return h | {"Idempotency-Key": f"ae{next(n)}"}

    body = {
        "on": "2026-11-20",
        "session": "am",
        "marks": [{"learner_id": str(x), "status": "present"} for x in a.kids],
    }
    r = await client.put(f"/v1/classes/{a.p4a.id}/register", json=body, headers=ada)
    assert r.status_code == 200 and r.json()["counts"]["present"] == 2, r.text
    r = await client.get(f"/v1/classes/{a.p4b.id}/register?on=2026-11-20", headers=ada)
    assert r.status_code == 403  # not her class
    mine = (await client.get("/v1/my/subjects", headers=ada)).json()["items"]
    assert [m["subject"] for m in mine] == ["Mathematics"]
    r = await client.put(
        f"/v1/offerings/{subjects['ENG']}/results/{a.term1.id}/scores",
        json={"entries": [{"learner_id": str(a.kids[0]), "component": "CA1", "score": 5}]},
        headers=ada,
    )
    assert r.status_code == 403  # she does not teach English
    r = await client.post(f"/v1/classes/{a.p4a.id}/results/{a.term1.id}/publish", headers=k(ada))
    assert r.status_code == 403
    r = await client.post(f"/v1/classes/{a.p4a.id}/results/{a.term1.id}/publish", headers=k(head))
    assert r.status_code == 200 and r.json() == {"published": 2}, r.text
    # The mother sees her own child, and only her own.
    kids = (await client.get("/v1/my/children", headers=mum)).json()["items"]
    assert len(kids) == 1 and kids[0]["attendance"]["present"] == 1
    card_id = kids[0]["report_cards"][0]["id"]
    card = (await client.get(f"/v1/report-cards/{card_id}", headers=mum)).json()
    assert card["content"]["summary"]["position"] == "1st"
    r = await client.get(f"/v1/learners/{a.kids[1]}/report-cards", headers=mum)
    assert r.status_code == 404
    # The State's dashboard now has attendance and results.
    sta = await login("dps@abia.example")
    sta.pop("X-Scope-Node-Id")
    o = (await client.get("/v1/monitoring/overview", headers=sta)).json()
    assert o["attendance_rate"] == 1.0 and o["results"] == 2 * len(subjects)
    assert o["pass_rate"] == 1.0 and o["registers_today"] == 1
