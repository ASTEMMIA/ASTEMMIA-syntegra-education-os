"""Performance management and continuous professional development (ADR-026)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from syntegra.core.clock import today, utcnow
from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.hr import api as hr
from syntegra.hr import appraisal as appr
from syntegra.hr import cpd
from syntegra.hr.appraisal_rules import default_template
from syntegra.hr.models import Appraisal
from tests.audit_helpers import verify_all_days
from tests.integration.test_appraisal import YEAR, _setup, all_rated
from tests.integration.test_staff import as_

OBS = ("lesson_planning", "delivery", "classroom_management", "assessment")


async def _countersigned(sm, a, staff_id, ratings=None, cycle_id=None):
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        ap = await appr.start(s, cycle_id or a.cycle.id, staff_id)
        await appr.rate(s, ap.id, appr.RatingsIn(ratings=ratings or all_rated(4)), submit=True)
    async with sm() as s, s.begin(), as_(a, a.hro):
        await appr.countersign(s, ap.id, approve=True)
    return ap.id


# --------------------------------------------------------------------------- objectives


async def test_objectives_blend_into_the_score(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        pms = await appr.open_cycle(
            s,
            name="TEST PMS cycle",
            period_start=YEAR[0],
            period_end=YEAR[1],
            criteria=default_template(30),
        )
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        objs = await appr.add_objective(
            s,
            pms.id,
            a.teacher.id,
            title="Raise Primary 5 numeracy",
            measure="Pupils at expected level",
            target="70%",
            weight=60,
        )
        objs = await appr.add_objective(
            s,
            pms.id,
            a.teacher.id,
            title="Run a maths club",
            measure="Sessions held",
            target="20 sessions",
            weight=40,
        )
        with pytest.raises(ValidationFailed, match="more than 100"):
            await appr.add_objective(
                s, pms.id, a.teacher.id, title="Extra", measure="m", target="t", weight=10
            )
        first, second = objs
        mid = await appr.review_objective(s, first.id, progress=40, note="On track")
        assert (mid.status, mid.mid_year_progress) == ("reviewed", 40)
        ap = await appr.start(s, pms.id, a.teacher.id)
        await appr.assess_objective(s, first.id, achievement=100)
        with pytest.raises(ValidationFailed, match="assess every agreed objective"):
            await appr.rate(s, ap.id, appr.RatingsIn(ratings=all_rated(4)), submit=True)
        await appr.assess_objective(s, second.id, achievement=25, note="Club started late")
        done = await appr.rate(s, ap.id, appr.RatingsIn(ratings=all_rated(4)), submit=True)
        # 70% criteria (80.00) + 30% objectives ((60*100 + 40*25) / 100 = 70.00)
        assert (done.competency_score, done.objectives_score, done.score) == (
            Decimal("80.00"),
            Decimal("70.00"),
            Decimal("77.00"),
        )
        assert [o.status for o in done.objectives] == ["assessed", "assessed"]
        with pytest.raises(Conflict, match="already assessed"):
            await appr.review_objective(s, first.id, progress=50)


# --------------------------------------------------------------------------- self-assessment


async def test_self_assessment(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        ap = await appr.start(s, a.cycle.id, a.teacher.id)
        with pytest.raises(Conflict, match="only the person appraised"):
            await appr.self_assess(s, ap.id, all_rated(3))
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        mine = await appr.self_assess(s, ap.id, all_rated(5))  # no reasons needed for oneself
        assert mine.self_ratings["delivery"]["rating"] == 5 and mine.ratings == {}
        with pytest.raises(ValidationFailed, match="from 1 to 5"):
            await appr.self_assess(s, ap.id, {"delivery": {"rating": 9}})
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        await appr.rate(s, ap.id, appr.RatingsIn(ratings=all_rated(4)), submit=True)
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        with pytest.raises(Conflict, match="closes when the appraisal is submitted"):
            await appr.self_assess(s, ap.id, all_rated(4))


# --------------------------------------------------------------------------- probation


async def test_probation_review_confirms_the_appointment(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        ap = await appr.start(s, a.cycle.id, a.colleague.id, kind="probation")
        with pytest.raises(ValidationFailed, match="for probation reviews"):
            await appr.rate(
                s,
                (await appr.start(s, a.cycle.id, a.colleague.id)).id,
                appr.RatingsIn(ratings=all_rated(4), recommendation="confirm_appointment"),
            )
        with pytest.raises(ValidationFailed, match="must recommend confirm, extend or terminate"):
            await appr.rate(s, ap.id, appr.RatingsIn(ratings=all_rated(4)), submit=True)
        with pytest.raises(ValidationFailed, match="recommends confirm, extend or terminate"):
            await appr.rate(
                s, ap.id, appr.RatingsIn(ratings=all_rated(4), recommendation="promotion")
            )
        await appr.rate(
            s,
            ap.id,
            appr.RatingsIn(ratings=all_rated(4), recommendation="confirm_appointment"),
            submit=True,
        )
    async with sm() as s, s.begin(), as_(a, a.hro):
        await appr.countersign(s, ap.id, approve=True)
        staff = await hr.get_staff(s, a.colleague.id)
        assert staff.confirmation_date == today()
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        with pytest.raises(Conflict, match="already confirmed"):
            await appr.start(s, a.cycle.id, a.colleague.id, kind="probation")


# --------------------------------------------------------------------------- appeals


async def test_appeal_decided_by_an_uninvolved_officer(sm, world):
    a = await _setup(sm, world)
    ap_id = await _countersigned(sm, a, a.teacher.id, all_rated(3))
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        with pytest.raises(Conflict, match="only the person appraised can appeal"):
            await appr.lodge_appeal(s, ap_id, "I disagree with the ratings given")
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        lodged = await appr.lodge_appeal(s, ap_id, "My learners' results were not considered")
        assert lodged.appeal.status == "lodged"
        with pytest.raises(Conflict, match="already been appealed"):
            await appr.lodge_appeal(s, ap_id, "Again")
    async with sm() as s, s.begin(), as_(a, a.hro):  # the countersigning officer
        with pytest.raises(Conflict, match="not involved"):
            await appr.decide_appeal(s, ap_id, uphold=True, note="x")
    async with sm() as s, s.begin(), as_(a, a.sta, a.tenant.root_scope_node_id):
        decided = await appr.decide_appeal(
            s,
            ap_id,
            uphold=True,
            note="Results evidence accepted",
            revised_ratings={
                "learner_progress": {"rating": 5, "comment": "Best results in the LGEA"}
            },
        )
        assert decided.appeal.status == "upheld" and decided.score == Decimal("60.00")
        assert decided.final_score == Decimal("64.00") and decided.final_band == "Good"
        with pytest.raises(Conflict, match="no appeal waiting"):
            await appr.decide_appeal(s, ap_id, uphold=False, note="x")
        assert await verify_all_days(s, a.tenant.id)


async def test_appeals_close_after_the_period(sm, world):
    a = await _setup(sm, world)
    ap_id = await _countersigned(sm, a, a.teacher.id)
    async with sm() as s, s.begin(), as_(a, a.hro):
        await s.execute(
            update(Appraisal)
            .where(Appraisal.id == ap_id)
            .values(countersigned_at=utcnow() - timedelta(days=31))
        )
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        with pytest.raises(Conflict, match="30-day appeal period has passed"):
            await appr.lodge_appeal(s, ap_id, "Too late now")


# --------------------------------------------------------------------------- CPD


async def test_cpd_programme_nomination_completion_and_compliance(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        p = await cpd.create_programme(
            s,
            cpd.ProgrammeIn(
                code="test-num-01",
                title="Teaching fractions with manipulatives",
                provider="National Teachers' Institute",
                provider_accredited=True,
                category="pedagogy",
                mode="in_person",
                start_date=today() - timedelta(days=10),
                end_date=today() - timedelta(days=8),
                hours=Decimal("18"),
                credit_points=12,
                capacity=1,
                venue="Aba North LGEA hall",
            ),
        )
        assert p.code == "TEST-NUM-01" and p.places_taken == 0
    async with sm() as s, s.begin(), as_(a, a.head, a.school):  # the school nominates
        assert [x.code for x in await cpd.programmes(s)] == ["TEST-NUM-01"]
        with pytest.raises(Conflict, match="place\\(s\\) left"):
            await cpd.nominate(s, p.id, [a.teacher.id, a.colleague.id])
        (x,) = await cpd.nominate(s, p.id, [a.teacher.id])
        with pytest.raises(Conflict, match="0 place"):
            await cpd.nominate(s, p.id, [a.colleague.id])
        with pytest.raises(Conflict, match="can move to: enrolled, withdrawn"):
            await cpd.progress_participation(s, x.id, status="completed")
        await cpd.progress_participation(s, x.id, status="enrolled")
        await cpd.progress_participation(s, x.id, status="attended")
        with pytest.raises(ValidationFailed, match="programme's last day"):
            await cpd.progress_participation(
                s, x.id, status="completed", completed_on=today() - timedelta(days=9)
            )
        await cpd.progress_participation(s, x.id, status="completed", evidence="NTI/CERT/0042")
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        with pytest.raises(Conflict, match="cannot verify your own"):
            await cpd.verify(s, x.id, approve=True)
        own = await cpd.report_activity(
            s,
            title="ICT in the classroom (online)",
            category="ict",
            activity_type="course",
            completed_on=today() - timedelta(days=2),
            evidence="COURSE-CERT-9",
            hours=Decimal("6"),
            credit_points=5,
            reflection="Using slides for fractions",
        )
        assert own.status == "self_reported"
        with pytest.raises(ValidationFailed, match="give evidence"):
            await cpd.report_activity(
                s,
                title="Something",
                category="ict",
                activity_type="course",
                completed_on=today(),
                evidence=" ",
            )
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        await cpd.verify(s, x.id, approve=True)
        await cpd.verify(s, own.id, approve=True)
        rows = {r.name: r for r in await cpd.compliance(s, today().year)}
        ada = rows["Ada Okeke"]
        assert (ada.points, ada.hours, ada.required, ada.shortfall, ada.met) == (
            17,
            Decimal("24"),
            20,
            3,
            False,
        )
        assert rows["Uche Nwosu"].points == 0


async def test_development_plan_and_training_needs(sm, world):
    a = await _setup(sm, world)
    weak = all_rated(
        4, classroom_management={"rating": 2}, assessment={"rating": 1, "comment": "No marking"}
    )
    ap_id = await _countersigned(sm, a, a.teacher.id, weak)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        act = await cpd.add_action(
            s,
            a.teacher.id,
            need="Marking and feedback",
            action="Attend the assessment workshop; head checks books monthly",
            category="assessment",
            due_date=today() - timedelta(days=1),
            appraisal_id=ap_id,
        )
        assert act.overdue
        with pytest.raises(ValidationFailed, match="record the outcome"):
            await cpd.update_action(s, act.id, status="done")
        done = await cpd.update_action(s, act.id, status="done", outcome="Books marked weekly")
        assert done.status == "done" and not done.overdue
        needs = await cpd.training_needs(s, a.cycle.id)
        assert needs["appraisals"] == 1
        cats = {(n.criterion, n.cpd_category) for n in needs["needs"]}
        assert cats == {
            ("Classroom management and learner discipline", "pedagogy"),
            ("Assessment, marking and record keeping", "assessment"),
        }
        assert needs["by_category"] == {"pedagogy": 1, "assessment": 1}


async def test_api(client, sm, world):
    a = await _setup(sm, world)

    async def login(name, scope):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {
            "Authorization": f"Bearer {r.json()['access_token']}",
            "X-Scope-Node-Id": str(scope),
        }

    hro = await login("hr@asubeb.example", a.asubeb)
    me = await login("ada@central.example", a.school)
    body = {
        "code": "API-01",
        "title": "Inclusive classrooms",
        "provider": "ASUBEB",
        "category": "inclusive_education",
        "mode": "school_based",
        "start_date": (today() - timedelta(days=3)).isoformat(),
        "end_date": (today() - timedelta(days=1)).isoformat(),
        "hours": "8",
        "credit_points": 6,
    }
    r = await client.post("/v1/cpd-programmes", json=body, headers=hro | {"Idempotency-Key": "c1"})
    assert r.status_code == 201, r.text
    n = await client.post(
        f"/v1/cpd-programmes/{r.json()['id']}/nominations",
        json={"staff_ids": [str(a.teacher.id)]},
        headers=hro | {"Idempotency-Key": "c2"},
    )
    assert n.status_code == 201 and n.json()["items"][0]["status"] == "nominated"
    rep = await client.post(
        "/v1/me/cpd",
        json={
            "title": "Peer observation",
            "category": "pedagogy",
            "activity_type": "peer_observation",
            "completed_on": today().isoformat(),
            "evidence": "Observation log 3",
            "credit_points": 2,
        },
        headers=me | {"Idempotency-Key": "c3"},
    )
    assert rep.status_code == 201, rep.text
    v = await client.post(
        f"/v1/cpd-participations/{rep.json()['id']}/verify",
        json={"approve": True},
        headers=hro | {"Idempotency-Key": "c4"},
    )
    assert v.status_code == 200
    comp = (await client.get(f"/v1/cpd-compliance?year={today().year}", headers=hro)).json()
    assert comp["required_points"] == 20
    assert {x["name"]: x["points"] for x in comp["staff"]}["Ada Okeke"] == 2
    rec = (await client.get(f"/v1/staff/{a.teacher.id}/cpd", headers=hro)).json()
    assert rec["verified_points_by_year"] == {str(today().year): 2}
