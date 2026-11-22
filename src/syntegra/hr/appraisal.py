"""Staff appraisal and lesson observation (ADR-025).

    open cycle ──► start (appraiser) ──► rate ──► submit ──┬─► countersign (another officer)
                                              ▲            └─► return ──┘
                                              └── appraisee may add their comment at any point

The appraiser is normally the head teacher; the countersigning officer is someone else with
``appraisal:countersign`` (four-eyes). Nobody appraises or countersigns their own appraisal.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.hr import events
from syntegra.hr.api import _publish, _staff, get_staff, nominal_roll
from syntegra.hr.appraisal_rules import (
    OBSERVATION_CRITERIA,
    RATING_LABEL,
    Recommendation,
    check_template,
    default_template,
    score,
)
from syntegra.hr.models import Appraisal, AppraisalCycle, LessonObservation, Staff
from syntegra.platform.session import PLATFORM_BYPASS

EDITABLE = ("draft", "returned")


# --------------------------------------------------------------------------- views


@dataclass(frozen=True, slots=True)
class CycleView:
    id: uuid.UUID
    name: str
    period_start: date
    period_end: date
    status: str
    criteria: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ObservationView:
    id: uuid.UUID
    staff_id: uuid.UUID
    observed_on: date
    class_name: str
    subject: str
    topic: str | None
    ratings: dict[str, Any]
    score: Decimal
    strengths: str | None
    improvements: str | None


@dataclass(frozen=True, slots=True)
class AppraisalView:
    id: uuid.UUID
    cycle: CycleView
    staff_id: uuid.UUID
    staff_number: str
    staff_name: str
    category: str
    position: str | None
    place: str | None
    status: str
    criteria: list[dict[str, Any]]
    ratings: dict[str, Any]
    score: Decimal | None
    band: str | None
    strengths: str | None
    improvements: str | None
    development_plan: str | None
    recommendation: str
    appraiser_comment: str | None
    appraisee_comment: str | None
    countersigner_comment: str | None
    appraiser_id: uuid.UUID | None
    countersigner_id: uuid.UUID | None
    observations: list[ObservationView]


def _cycle_view(c: AppraisalCycle) -> CycleView:
    return CycleView(c.id, c.name, c.period_start, c.period_end, c.status, c.criteria)


def _obs_view(o: LessonObservation) -> ObservationView:
    return ObservationView(
        o.id,
        o.staff_id,
        o.observed_on,
        o.class_name,
        o.subject,
        o.topic,
        o.ratings,
        o.score,
        o.strengths,
        o.improvements,
    )


async def _person_of_user(session: AsyncSession, user_id: uuid.UUID | None) -> uuid.UUID | None:
    if user_id is None:
        return None
    from syntegra.iam.models import AppUser

    return (
        await session.execute(
            select(AppUser.person_id)
            .where(AppUser.id == user_id)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one_or_none()


async def _not_self(session: AsyncSession, staff: Staff, what: str) -> None:
    """Nobody appraises, observes or countersigns themselves."""
    mine = await _person_of_user(session, current_context().user_id)
    if mine is not None and mine == staff.person_id:
        raise Conflict(f"you cannot {what} your own appraisal")


# --------------------------------------------------------------------------- cycles


async def open_cycle(
    session: AsyncSession,
    *,
    name: str,
    period_start: date,
    period_end: date,
    criteria: dict[str, Any] | None = None,
) -> CycleView:
    """Open an appraisal period for the employer the caller works in."""
    ctx = current_context()
    template = criteria or default_template()
    errors = check_template(template)
    if not name.strip():
        errors.append("the cycle needs a name, e.g. 2026 annual appraisal")
    if period_end < period_start:
        errors.append("the period ends before it starts")
    if errors:
        raise ValidationFailed("; ".join(errors))
    dup = (
        await session.execute(
            select(AppraisalCycle.id).where(
                AppraisalCycle.scope_node_id == ctx.scope_node_id,
                AppraisalCycle.name == name.strip(),
            )
        )
    ).first()
    if dup:
        raise Conflict(f"an appraisal cycle called '{name.strip()}' already exists here")
    c = AppraisalCycle(
        tenant_id=ctx.tenant_id,
        scope_node_id=ctx.scope_node_id,
        name=name.strip(),
        period_start=period_start,
        period_end=period_end,
        status="open",
        criteria=template,
    )
    session.add(c)
    await session.flush()
    return _cycle_view(c)


async def close_cycle(session: AsyncSession, cycle_id: uuid.UUID) -> CycleView:
    c = await _cycle(session, cycle_id)
    c.status = "closed"
    await session.flush()
    return _cycle_view(c)


async def _cycle(session: AsyncSession, cycle_id: uuid.UUID) -> AppraisalCycle:
    """A cycle the caller works under: opened at their scope or above it (a school appraises
    in the board's cycle), or below it (the state sees the board's cycle)."""
    from syntegra.governance import api as governance

    ctx = current_context()
    c = (
        await session.execute(
            select(AppraisalCycle)
            .where(AppraisalCycle.id == cycle_id, AppraisalCycle.tenant_id == ctx.tenant_id)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one_or_none()
    above = await governance.ancestor_ids(session, ctx.scope_node_id) | {ctx.scope_node_id}
    if c is None or not (
        c.scope_node_id in above
        or await governance.is_within(session, ctx.scope_node_id, c.scope_node_id)
    ):
        raise NotFound("appraisal cycle not found")
    return c


async def cycles(session: AsyncSession) -> list[CycleView]:
    """Cycles that apply to the caller: those opened at their scope or any scope above it."""
    from syntegra.governance import api as governance

    ctx = current_context()
    above = await governance.ancestor_ids(session, ctx.scope_node_id) | {ctx.scope_node_id}
    rows = (
        await session.execute(
            select(AppraisalCycle)
            .where(
                AppraisalCycle.tenant_id == ctx.tenant_id,
                AppraisalCycle.scope_node_id.in_(above),
            )
            .order_by(AppraisalCycle.period_start.desc())
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalars()
    return [_cycle_view(c) for c in rows]


# --------------------------------------------------------------------------- appraisals


async def _appraisal(session: AsyncSession, appraisal_id: uuid.UUID) -> Appraisal:
    a = (
        await session.execute(
            select(Appraisal).where(Appraisal.id == appraisal_id).with_for_update()
        )
    ).scalar_one_or_none()
    if a is None:
        raise NotFound("appraisal not found in your scope")
    return a


async def _view(session: AsyncSession, a: Appraisal) -> AppraisalView:
    cycle = await _cycle(session, a.cycle_id)
    staff = await get_staff(session, a.staff_id)
    post = staff.current_post
    obs = (
        await session.execute(
            select(LessonObservation)
            .where(
                LessonObservation.staff_id == a.staff_id,
                LessonObservation.observed_on >= cycle.period_start,
                LessonObservation.observed_on <= cycle.period_end,
            )
            .order_by(LessonObservation.observed_on)
        )
    ).scalars()
    return AppraisalView(
        a.id,
        _cycle_view(cycle),
        a.staff_id,
        staff.staff_number,
        " ".join(p for p in (staff.first_name, staff.other_names, staff.surname) if p),
        staff.category,
        post.position if post else None,
        post.place_name if post else None,
        a.status,
        cycle.criteria[staff.category],
        a.ratings,
        a.score,
        a.band,
        a.strengths,
        a.improvements,
        a.development_plan,
        a.recommendation,
        a.appraiser_comment,
        a.appraisee_comment,
        a.countersigner_comment,
        a.appraiser_id,
        a.countersigner_id,
        [_obs_view(o) for o in obs],
    )


async def get_appraisal(session: AsyncSession, appraisal_id: uuid.UUID) -> AppraisalView:
    return await _view(session, await _appraisal(session, appraisal_id))


async def start(session: AsyncSession, cycle_id: uuid.UUID, staff_id: uuid.UUID) -> AppraisalView:
    """Begin a staff member's appraisal in a cycle; the caller becomes the appraiser."""
    ctx = current_context()
    cycle = await _cycle(session, cycle_id)
    if cycle.status != "open":
        raise Conflict("this appraisal cycle is closed")
    staff = await _staff(session, staff_id)
    if staff.status == "exited":
        raise Conflict("this staff member has left")
    await _not_self(session, staff, "conduct")
    existing = (
        await session.execute(
            select(Appraisal.id)
            .where(Appraisal.cycle_id == cycle.id, Appraisal.staff_id == staff.id)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).first()
    if existing:
        raise Conflict("this staff member already has an appraisal in this cycle")
    a = Appraisal(
        tenant_id=staff.tenant_id,
        scope_node_id=staff.scope_node_id,
        cycle_id=cycle.id,
        staff_id=staff.id,
        status="draft",
        appraiser_id=ctx.user_id,
        ratings={},
        recommendation=Recommendation.NONE.value,
    )
    session.add(a)
    await session.flush()
    return await _view(session, a)


@dataclass(frozen=True, slots=True)
class RatingsIn:
    ratings: dict[str, dict[str, Any]]
    strengths: str | None = None
    improvements: str | None = None
    development_plan: str | None = None
    recommendation: str = "none"
    appraiser_comment: str | None = None


def _text(v: str | None) -> str | None:
    v = (v or "").strip()
    if len(v) > 1000:
        raise ValidationFailed("comments are limited to 1000 characters")
    return v or None


async def rate(
    session: AsyncSession, appraisal_id: uuid.UUID, data: RatingsIn, *, submit: bool = False
) -> AppraisalView:
    """Save the appraiser's ratings and comments; with ``submit`` they must be complete."""
    ctx = current_context()
    a = await _appraisal(session, appraisal_id)
    if a.status not in EDITABLE:
        raise Conflict(f"this appraisal is {a.status} and cannot be changed")
    if a.appraiser_id is not None and ctx.user_id is not None and a.appraiser_id != ctx.user_id:
        raise Conflict("only the appraiser can rate this appraisal")
    staff = await _staff(session, a.staff_id)
    await _not_self(session, staff, "conduct")
    try:
        rec = Recommendation(data.recommendation)
    except ValueError:
        raise ValidationFailed(f"'{data.recommendation}' is not a recommendation") from None
    cycle = await _cycle(session, a.cycle_id)
    pct, band, problems = score(cycle.criteria[staff.category], data.ratings)
    if submit:
        if problems:
            raise ValidationFailed("; ".join(problems), next_action="Complete the ratings.")
        if rec is Recommendation.PROMOTION and pct < 70:
            raise ValidationFailed("a promotion recommendation needs a Very good score or better")
        if rec is Recommendation.IMPROVEMENT_PLAN and not (data.development_plan or "").strip():
            raise ValidationFailed("an improvement plan needs the plan written down")
    else:
        # A draft may be incomplete, but whatever is entered must make sense.
        codes = {c["code"] for c in cycle.criteria[staff.category]}
        bad = [p for p in problems if p.startswith("unknown")]
        bad += [
            f"'{k}' must be rated from 1 to 5"
            for k, v in data.ratings.items()
            if k in codes and v.get("rating") is not None and v.get("rating") not in RATING_LABEL
        ]
        if bad:
            raise ValidationFailed("; ".join(bad))
    a.ratings = {
        k: {"rating": v.get("rating"), "comment": _text(v.get("comment"))}
        for k, v in data.ratings.items()
    }
    a.strengths, a.improvements = _text(data.strengths), _text(data.improvements)
    a.development_plan, a.appraiser_comment = (
        _text(data.development_plan),
        _text(data.appraiser_comment),
    )
    a.recommendation = rec.value
    a.score, a.band = (pct, band) if not problems else (None, None)
    if submit:
        a.status, a.submitted_at = "submitted", utcnow()
        _publish(session, staff, events.APPRAISAL_SUBMITTED, {"appraisal_id": str(a.id)})
    await session.flush()
    return await _view(session, a)


async def appraisee_comment(
    session: AsyncSession, appraisal_id: uuid.UUID, comment: str
) -> AppraisalView:
    """The staff member's own response, recorded with the appraisal."""
    a = await _appraisal(session, appraisal_id)
    staff = await _staff(session, a.staff_id)
    mine = await _person_of_user(session, current_context().user_id)
    if mine != staff.person_id:
        raise Conflict("only the person appraised can add their comment")
    if a.status == "countersigned":
        raise Conflict("this appraisal is complete")
    a.appraisee_comment = _text(comment)
    await session.flush()
    return await _view(session, a)


async def countersign(
    session: AsyncSession, appraisal_id: uuid.UUID, *, approve: bool, comment: str | None = None
) -> AppraisalView:
    """Countersign a submitted appraisal, or return it to the appraiser with a reason."""
    ctx = current_context()
    a = await _appraisal(session, appraisal_id)
    if a.status != "submitted":
        raise Conflict("only a submitted appraisal can be countersigned or returned")
    if a.appraiser_id is not None and a.appraiser_id == ctx.user_id:
        raise Conflict("the appraiser cannot countersign their own appraisal (four-eyes)")
    staff = await _staff(session, a.staff_id)
    await _not_self(session, staff, "countersign")
    if not approve and not (comment or "").strip():
        raise ValidationFailed("say why the appraisal is being returned")
    a.countersigner_id, a.countersigner_comment = ctx.user_id, _text(comment)
    if approve:
        a.status, a.countersigned_at = "countersigned", utcnow()
        _publish(session, staff, events.APPRAISAL_COUNTERSIGNED, {"appraisal_id": str(a.id)})
    else:
        a.status = "returned"
        _publish(session, staff, events.APPRAISAL_RETURNED, {"appraisal_id": str(a.id)})
    await session.flush()
    return await _view(session, a)


async def appraisals_of(session: AsyncSession, staff_id: uuid.UUID) -> list[AppraisalView]:
    await _staff(session, staff_id)
    rows = (
        await session.execute(select(Appraisal).where(Appraisal.staff_id == staff_id))
    ).scalars()
    return [await _view(session, a) for a in rows]


@dataclass(frozen=True, slots=True)
class CycleProgress:
    place: str
    staff: int
    not_started: int
    draft: int
    submitted: int
    returned: int
    countersigned: int
    average_score: Decimal | None


async def progress(session: AsyncSession, cycle_id: uuid.UUID) -> list[CycleProgress]:
    """How far each place of work in the caller's scope has got with a cycle."""
    cycle = await _cycle(session, cycle_id)
    roll = await nominal_roll(session, limit=100_000)
    by_staff = {
        a.staff_id: a
        for a in (
            await session.execute(select(Appraisal).where(Appraisal.cycle_id == cycle.id))
        ).scalars()
    }
    places: dict[str, list[Appraisal | None]] = {}
    for v in roll:
        post = v.current_post
        places.setdefault(post.place_name if post else "(no current post)", []).append(
            by_staff.get(v.id)
        )
    out = []
    for place, items in sorted(places.items()):
        done = [a for a in items if a is not None]
        scores = [a.score for a in done if a.status == "countersigned" and a.score is not None]
        count = {
            st: sum(a.status == st for a in done)
            for st in (*EDITABLE, "submitted", "countersigned")
        }
        out.append(
            CycleProgress(
                place=place,
                staff=len(items),
                not_started=len(items) - len(done),
                draft=count["draft"],
                submitted=count["submitted"],
                returned=count["returned"],
                countersigned=count["countersigned"],
                average_score=(sum(scores, Decimal(0)) / len(scores)).quantize(Decimal("0.01"))
                if scores
                else None,
            )
        )
    return out


# --------------------------------------------------------------------------- lesson observation


async def observe_lesson(
    session: AsyncSession,
    staff_id: uuid.UUID,
    *,
    observed_on: date,
    class_name: str,
    subject: str,
    ratings: dict[str, dict[str, Any]],
    topic: str | None = None,
    strengths: str | None = None,
    improvements: str | None = None,
) -> ObservationView:
    ctx = current_context()
    staff = await _staff(session, staff_id)
    if staff.category != "teaching":
        raise ValidationFailed("lesson observation is for teaching staff")
    await _not_self(session, staff, "observe")
    if observed_on > today():
        raise ValidationFailed("a lesson cannot be observed in the future")
    if not class_name.strip() or not subject.strip():
        raise ValidationFailed("give the class and the subject observed")
    criteria = [
        {"code": c, "name": c.replace("_", " "), "weight": 25} for c in OBSERVATION_CRITERIA
    ]
    pct, _band, problems = score(criteria, ratings)
    if problems:
        raise ValidationFailed("; ".join(problems))
    o = LessonObservation(
        tenant_id=staff.tenant_id,
        scope_node_id=staff.scope_node_id,
        staff_id=staff.id,
        observed_by=ctx.user_id,
        observed_on=observed_on,
        class_name=class_name.strip()[:60],
        subject=subject.strip()[:80],
        topic=(topic or "").strip()[:200] or None,
        ratings={
            k: {"rating": v["rating"], "comment": _text(v.get("comment"))}
            for k, v in ratings.items()
        },
        score=pct,
        strengths=_text(strengths),
        improvements=_text(improvements),
    )
    session.add(o)
    await session.flush()
    _publish(session, staff, events.LESSON_OBSERVED, {"observation_id": str(o.id)})
    await session.flush()
    return _obs_view(o)
