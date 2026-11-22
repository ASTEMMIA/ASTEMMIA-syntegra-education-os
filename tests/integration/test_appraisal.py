"""Teacher appraisal and lesson observation (ADR-025)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from syntegra.core.clock import today
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.hr import appraisal as appr
from syntegra.hr.appraisal_rules import TEACHING_CRITERIA, band, check_template, default_template
from syntegra.iam import api as iam
from syntegra.iam.models import AppUser
from tests.audit_helpers import verify_all_days
from tests.integration.test_staff import _teacher, _world, as_

YEAR = (date(today().year, 1, 1), date(today().year, 12, 31))


def all_rated(value=4, **overrides):
    out = {c.code: {"rating": value} for c in TEACHING_CRITERIA}
    for code, rating in overrides.items():
        out[code] = rating
    return out


async def _setup(sm, world):
    a = await _world(sm, world)
    async with sm() as s, s.begin(), as_(a, a.hro):
        a.teacher = await _teacher(s, a)
        a.colleague = await _teacher(s, a, "Uche", "Nwosu")
        a.cycle = await appr.open_cycle(
            s, name="TEST appraisal", period_start=YEAR[0], period_end=YEAR[1]
        )
    async with sm() as s, s.begin(), as_(a, scope=a.tenant.root_scope_node_id):
        head = await iam.create_user(
            s, tenant_id=a.tenant.id, login="head@central.example", display_name="Head teacher"
        )
        await iam.grant_role(
            s, user_id=head.id, role_code="HEAD", scope_node_id=a.aba.scope_node_id
        )
        me = await iam.create_user(
            s, tenant_id=a.tenant.id, login="ada@central.example", display_name="Ada Okeke"
        )
        await s.execute(
            update(AppUser).where(AppUser.id == me.id).values(person_id=a.teacher.person_id)
        )
    a.head, a.me, a.school = head.id, me.id, a.aba.scope_node_id
    return a


def test_scoring_rules():
    assert check_template(default_template()) == []
    from syntegra.hr.appraisal_rules import score

    pct, label, problems = score(default_template()["teaching"], all_rated(4))
    assert (pct, label, problems) == (Decimal("80.00"), "Very good", [])
    pct, _, problems = score(default_template()["teaching"], all_rated(5))
    assert pct == Decimal("100.00") and len(problems) == 9  # every 5 needs a reason
    assert band(Decimal("84.99")) == "Very good" and band(Decimal(85)) == "Outstanding"
    bad = default_template() | {"teaching": [{"code": "x", "name": "X", "weight": 90}]}
    assert "add up to 100" in check_template(bad)[0]


async def test_appraise_countersign_with_observations(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        obs = await appr.observe_lesson(
            s,
            a.teacher.id,
            observed_on=today() - timedelta(days=3),
            class_name="Primary 4B",
            subject="Mathematics",
            topic="Fractions",
            ratings={
                c: {"rating": 4}
                for c in ("lesson_planning", "delivery", "classroom_management", "assessment")
            },
            strengths="Clear examples",
        )
        assert obs.score == Decimal("80.00")
        ap = await appr.start(s, a.cycle.id, a.teacher.id)
        assert ap.status == "draft" and ap.place == "TEST Central School Aba"
        assert [c["code"] for c in ap.criteria][:2] == ["subject_knowledge", "lesson_planning"]
        # A draft may be incomplete, but not wrong.
        draft = await appr.rate(s, ap.id, appr.RatingsIn(ratings={"delivery": {"rating": 4}}))
        assert draft.score is None and draft.ratings["delivery"]["rating"] == 4
        with pytest.raises(ValidationFailed, match="rate 'Knowledge of subject'"):
            await appr.rate(
                s, ap.id, appr.RatingsIn(ratings={"delivery": {"rating": 4}}), submit=True
            )
        with pytest.raises(ValidationFailed, match="say why"):
            await appr.rate(
                s,
                ap.id,
                appr.RatingsIn(ratings=all_rated(4, punctuality={"rating": 1})),
                submit=True,
            )
        with pytest.raises(ValidationFailed, match="Very good score"):
            await appr.rate(
                s,
                ap.id,
                appr.RatingsIn(ratings=all_rated(3), recommendation="promotion"),
                submit=True,
            )
        done = await appr.rate(
            s,
            ap.id,
            appr.RatingsIn(
                ratings=all_rated(
                    4, delivery={"rating": 5, "comment": "Excellent use of learning aids"}
                ),
                strengths="Well-prepared lesson notes",
                recommendation="promotion",
            ),
            submit=True,
        )
        assert (done.status, done.score, done.band) == ("submitted", Decimal("84.00"), "Very good")
        assert [o.subject for o in done.observations] == ["Mathematics"]
        with pytest.raises(Conflict, match="cannot be changed"):
            await appr.rate(s, ap.id, appr.RatingsIn(ratings=all_rated(4)))
        with pytest.raises(Conflict, match="four-eyes"):
            await appr.countersign(s, ap.id, approve=True)
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        mine = await appr.appraisee_comment(s, ap.id, "I agree and would like ICT training.")
        assert mine.appraisee_comment.startswith("I agree")
    async with sm() as s, s.begin(), as_(a, a.hro):
        with pytest.raises(ValidationFailed, match="being returned"):
            await appr.countersign(s, ap.id, approve=False)
        signed = await appr.countersign(s, ap.id, approve=True, comment="Agreed")
        assert signed.status == "countersigned"
        (row,) = [
            p for p in await appr.progress(s, a.cycle.id) if p.place == "TEST Central School Aba"
        ]
        assert (row.staff, row.countersigned, row.not_started, row.average_score) == (
            2,
            1,
            1,
            Decimal("84.00"),
        )
        assert await verify_all_days(s, a.tenant.id)


async def test_nobody_appraises_themselves_and_return_flow(sm, world):
    a = await _setup(sm, world)
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        with pytest.raises(Conflict, match="your own appraisal"):
            await appr.start(s, a.cycle.id, a.teacher.id)
        with pytest.raises(Conflict, match="your own"):
            await appr.observe_lesson(
                s,
                a.teacher.id,
                observed_on=today(),
                class_name="P4",
                subject="Maths",
                ratings={
                    c: {"rating": 3}
                    for c in ("lesson_planning", "delivery", "classroom_management", "assessment")
                },
            )
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        ap = await appr.start(s, a.cycle.id, a.colleague.id)
        with pytest.raises(Conflict, match="already has an appraisal"):
            await appr.start(s, a.cycle.id, a.colleague.id)
        await appr.rate(s, ap.id, appr.RatingsIn(ratings=all_rated(3)), submit=True)
    async with sm() as s, s.begin(), as_(a, a.me, a.school):
        with pytest.raises(Conflict, match="only the person appraised"):
            await appr.appraisee_comment(s, ap.id, "Not mine")
    async with sm() as s, s.begin(), as_(a, a.hro):
        back = await appr.countersign(s, ap.id, approve=False, comment="Add a development plan")
        assert back.status == "returned"
    async with sm() as s, s.begin(), as_(a, a.head, a.school):
        again = await appr.rate(
            s,
            ap.id,
            appr.RatingsIn(
                ratings=all_rated(3),
                recommendation="improvement_plan",
                development_plan="Weekly coaching on lesson delivery",
            ),
            submit=True,
        )
        assert again.status == "submitted" and again.band == "Good"
    async with sm() as s, s.begin(), as_(a, a.hro):
        await appr.close_cycle(s, a.cycle.id)
        with pytest.raises(Conflict, match="closed"):
            await appr.start(s, a.cycle.id, a.teacher.id)


async def test_cycles_follow_the_hierarchy(sm, world):
    a = await _setup(sm, world)
    async with sm() as s:
        with as_(a, a.head, a.school):  # a school works under the board's cycle
            assert [c.name for c in await appr.cycles(s)] == ["TEST appraisal"]
        with as_(a, scope=a.bodies["MIN-TERTIARY-EDUCATION"]):
            assert await appr.cycles(s) == []
            with pytest.raises(NotFound):
                await appr.progress(s, a.cycle.id)


async def test_api(client, sm, world):
    a = await _setup(sm, world)

    async def login(name, scope):
        r = await client.post("/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": name})
        return {
            "Authorization": f"Bearer {r.json()['access_token']}",
            "X-Scope-Node-Id": str(scope),
        }

    head = await login("head@central.example", a.school)
    hro = await login("hr@asubeb.example", a.asubeb)
    cycles = (await client.get("/v1/appraisal-cycles", headers=head)).json()["items"]
    cid = cycles[0]["id"]
    r = await client.post(
        f"/v1/appraisal-cycles/{cid}/appraisals/{a.teacher.id}",
        json={},
        headers=head | {"Idempotency-Key": "a1"},
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    ratings = {c.code: {"rating": 4} for c in TEACHING_CRITERIA}
    r = await client.post(
        f"/v1/appraisals/{aid}/ratings",
        json={"ratings": ratings, "submit": True},
        headers=head | {"Idempotency-Key": "a2"},
    )
    assert r.status_code == 200 and r.json()["band"] == "Very good"
    r = await client.post(
        f"/v1/appraisals/{aid}/countersign", json={}, headers=hro | {"Idempotency-Key": "a3"}
    )
    assert r.status_code == 200 and r.json()["status"] == "countersigned"
    hist = (await client.get(f"/v1/staff/{a.teacher.id}/appraisals", headers=hro)).json()["items"]
    assert [h["score"] for h in hist] == ["80.00"]
    # A head teacher may not open cycles: that is HR's (countersigning) role.
    r = await client.post(
        "/v1/appraisal-cycles",
        json={"name": "Head cycle", "period_start": "2026-01-01", "period_end": "2026-12-31"},
        headers=head | {"Idempotency-Key": "a4"},
    )
    assert r.status_code == 403
