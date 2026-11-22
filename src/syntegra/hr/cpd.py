"""Continuous professional development, development plans and training needs (ADR-026).

* **Programmes** are offered by a body (ASUBEB, the Ministry, a school…) with a provider, category,
  mode, dates, hours and CPD points. Schools beneath the body nominate their staff.
* **Participation:** nominated → enrolled → attended → completed → verified (or withdrawn).
  Staff may also report CPD they did elsewhere (a course, a conference, mentoring, research) with
  evidence; someone else verifies it. Only verified activity earns points.
* **Compliance:** verified points in a year against the jurisdiction's annual requirement
  (setting ``hr.cpd``), per staff member and per place of work. This is what TRCN registration
  renewal needs to see.
* **Development plans:** actions for each staff member (from an appraisal or agreed directly), with
  a due date and status, linked to the CPD that met them.
* **Training needs:** from countersigned appraisals, which criteria are weak where, and the CPD
  category that answers each, so programmes are planned where they are needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.hr import events
from syntegra.hr.api import _publish, _staff, nominal_roll
from syntegra.hr.appraisal import _cycle, _person_of_user
from syntegra.hr.appraisal_rules import CRITERION_CPD, Recommendation
from syntegra.hr.models import (
    CPD_CATEGORIES,
    Appraisal,
    CpdParticipation,
    CpdProgramme,
    DevelopmentAction,
)
from syntegra.platform.session import PLATFORM_BYPASS

CPD_SETTING = "hr.cpd"
DEFAULT_ANNUAL_POINTS = 20  # placeholder until the TRCN requirement is confirmed for Abia
MODES = ("in_person", "online", "blended", "school_based")
ACTIVITY_TYPES = (
    "workshop",
    "course",
    "conference",
    "mentoring",
    "peer_observation",
    "research",
    "qualification",
    "other",
)
LIVE = ("nominated", "enrolled", "attended", "completed", "verified", "self_reported")


def _category(value: str) -> str:
    if value not in CPD_CATEGORIES:
        raise ValidationFailed(f"category must be one of {', '.join(CPD_CATEGORIES)}")
    return value


# --------------------------------------------------------------------------- programmes


@dataclass(frozen=True, slots=True)
class ProgrammeView:
    id: uuid.UUID
    code: str
    title: str
    provider: str
    provider_accredited: bool
    category: str
    mode: str
    audience: str | None
    description: str | None
    start_date: date
    end_date: date
    venue: str | None
    hours: Decimal
    credit_points: int
    capacity: int | None
    places_taken: int
    status: str


@dataclass(frozen=True, slots=True)
class ProgrammeIn:
    code: str
    title: str
    provider: str
    category: str
    mode: str
    start_date: date
    end_date: date
    hours: Decimal
    credit_points: int
    provider_accredited: bool = False
    audience: str | None = None
    description: str | None = None
    venue: str | None = None
    capacity: int | None = None


async def _taken(session: AsyncSession, programme_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(CpdParticipation)
                .where(
                    CpdParticipation.programme_id == programme_id,
                    CpdParticipation.status.in_(LIVE),
                )
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        ).scalar_one()
    )


async def _programme_view(session: AsyncSession, p: CpdProgramme) -> ProgrammeView:
    return ProgrammeView(
        p.id,
        p.code,
        p.title,
        p.provider,
        p.provider_accredited,
        p.category,
        p.mode,
        p.audience,
        p.description,
        p.start_date,
        p.end_date,
        p.venue,
        p.hours,
        p.credit_points,
        p.capacity,
        await _taken(session, p.id),
        p.status,
    )


async def create_programme(session: AsyncSession, data: ProgrammeIn) -> ProgrammeView:
    ctx = current_context()
    errors = []
    code = data.code.strip().upper()
    if not code or len(code) > 30:
        errors.append("the programme code is required (at most 30 characters)")
    if not data.title.strip() or not data.provider.strip():
        errors.append("title and provider are required")
    if data.mode not in MODES:
        errors.append(f"mode must be one of {', '.join(MODES)}")
    if data.end_date < data.start_date:
        errors.append("the programme ends before it starts")
    if data.hours <= 0 or data.credit_points < 0:
        errors.append("hours must be positive and points not negative")
    if data.capacity is not None and data.capacity < 1:
        errors.append("capacity must be at least 1")
    _category(data.category)
    if errors:
        raise ValidationFailed("; ".join(errors))
    dup = (
        await session.execute(
            select(CpdProgramme.id)
            .where(CpdProgramme.tenant_id == ctx.tenant_id, CpdProgramme.code == code)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).first()
    if dup:
        raise Conflict(f"programme code {code} is already used")
    p = CpdProgramme(
        tenant_id=ctx.tenant_id,
        scope_node_id=ctx.scope_node_id,
        code=code,
        title=data.title.strip(),
        provider=data.provider.strip(),
        provider_accredited=data.provider_accredited,
        category=data.category,
        mode=data.mode,
        audience=(data.audience or "").strip() or None,
        description=(data.description or "").strip() or None,
        start_date=data.start_date,
        end_date=data.end_date,
        venue=(data.venue or "").strip() or None,
        hours=data.hours,
        credit_points=data.credit_points,
        capacity=data.capacity,
        status="open",
    )
    session.add(p)
    await session.flush()
    return await _programme_view(session, p)


async def _programme(session: AsyncSession, programme_id: uuid.UUID) -> CpdProgramme:
    """A programme the caller may use: run at their scope, above it, or below it."""
    ctx = current_context()
    p = (
        await session.execute(
            select(CpdProgramme)
            .where(CpdProgramme.id == programme_id, CpdProgramme.tenant_id == ctx.tenant_id)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one_or_none()
    above = await governance.ancestor_ids(session, ctx.scope_node_id) | {ctx.scope_node_id}
    if p is None or not (
        p.scope_node_id in above
        or await governance.is_within(session, ctx.scope_node_id, p.scope_node_id)
    ):
        raise NotFound("programme not found")
    return p


async def programmes(
    session: AsyncSession, *, category: str | None = None, open_only: bool = False
) -> list[ProgrammeView]:
    """Programmes the caller can nominate to: run at their scope or above it, or below it."""
    ctx = current_context()
    above = await governance.ancestor_ids(session, ctx.scope_node_id) | {ctx.scope_node_id}
    rows = (
        await session.execute(
            select(CpdProgramme)
            .where(CpdProgramme.tenant_id == ctx.tenant_id)
            .order_by(CpdProgramme.start_date.desc())
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalars()
    out = []
    for p in rows:
        if category and p.category != category:
            continue
        if open_only and p.status != "open":
            continue
        if p.scope_node_id in above or await governance.is_within(
            session, ctx.scope_node_id, p.scope_node_id
        ):
            out.append(await _programme_view(session, p))
    return out


async def set_programme_status(
    session: AsyncSession, programme_id: uuid.UUID, status: str
) -> ProgrammeView:
    p = await _programme(session, programme_id)
    if status not in ("planned", "open", "closed", "completed", "cancelled"):
        raise ValidationFailed("unknown programme status")
    if status == "cancelled":
        for x in (
            await session.execute(
                select(CpdParticipation)
                .where(
                    CpdParticipation.programme_id == p.id,
                    CpdParticipation.status.in_(("nominated", "enrolled")),
                )
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        ).scalars():
            x.status, x.note = "withdrawn", "programme cancelled"
    p.status = status
    await session.flush()
    return await _programme_view(session, p)


# --------------------------------------------------------------------------- participation


@dataclass(frozen=True, slots=True)
class ParticipationView:
    id: uuid.UUID
    staff_id: uuid.UUID
    programme_id: uuid.UUID | None
    title: str
    provider: str | None
    category: str
    activity_type: str
    started_on: date | None
    completed_on: date | None
    hours: Decimal | None
    credit_points: int
    status: str
    source: str
    evidence: str | None
    reflection: str | None
    note: str | None


def _part_view(x: CpdParticipation) -> ParticipationView:
    return ParticipationView(
        x.id,
        x.staff_id,
        x.programme_id,
        x.title,
        x.provider,
        x.category,
        x.activity_type,
        x.started_on,
        x.completed_on,
        x.hours,
        x.credit_points,
        x.status,
        x.source,
        x.evidence,
        x.reflection,
        x.note,
    )


async def nominate(
    session: AsyncSession,
    programme_id: uuid.UUID,
    staff_ids: list[uuid.UUID],
    *,
    appraisal_id: uuid.UUID | None = None,
) -> list[ParticipationView]:
    """Nominate staff to a programme, within its capacity. Each must be active and not already on
    it. ``appraisal_id`` links a nomination to the appraisal that recommended training."""
    ctx = current_context()
    p = await _programme(session, programme_id)
    if p.status != "open":
        raise Conflict(f"this programme is {p.status}, not open for nominations")
    if p.capacity is not None and await _taken(session, p.id) + len(staff_ids) > p.capacity:
        raise Conflict(
            f"the programme has {p.capacity - await _taken(session, p.id)} place(s) left",
            next_action="Nominate fewer staff, or ask the organiser to raise the capacity.",
        )
    out = []
    for sid in staff_ids:
        staff = await _staff(session, sid)
        if staff.status != "active":
            raise Conflict(f"staff {staff.staff_number} is not active")
        dup = (
            await session.execute(
                select(CpdParticipation.id).where(
                    CpdParticipation.programme_id == p.id,
                    CpdParticipation.staff_id == staff.id,
                    CpdParticipation.status.in_(LIVE),
                )
            )
        ).first()
        if dup:
            raise Conflict(f"staff {staff.staff_number} is already on this programme")
        x = CpdParticipation(
            tenant_id=staff.tenant_id,
            scope_node_id=staff.scope_node_id,
            staff_id=staff.id,
            programme_id=p.id,
            title=p.title,
            provider=p.provider,
            category=p.category,
            activity_type="programme",
            started_on=p.start_date,
            hours=p.hours,
            credit_points=p.credit_points,
            status="nominated",
            source="appraisal" if appraisal_id else "nomination",
            appraisal_id=appraisal_id,
            nominated_by=ctx.user_id,
        )
        session.add(x)
        await session.flush()
        _publish(session, staff, events.CPD_NOMINATED, {"participation_id": str(x.id)})
        out.append(_part_view(x))
    await session.flush()
    return out


_NEXT = {
    "nominated": {"enrolled", "withdrawn"},
    "enrolled": {"attended", "withdrawn"},
    "attended": {"completed", "withdrawn"},
}


async def _participation(session: AsyncSession, participation_id: uuid.UUID) -> CpdParticipation:
    x = (
        await session.execute(
            select(CpdParticipation)
            .where(CpdParticipation.id == participation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if x is None:
        raise NotFound("CPD record not found in your scope")
    return x


async def progress_participation(
    session: AsyncSession,
    participation_id: uuid.UUID,
    *,
    status: str,
    completed_on: date | None = None,
    evidence: str | None = None,
    note: str | None = None,
) -> ParticipationView:
    """Move a nomination on: enrolled, attended, completed (with the date), or withdrawn."""
    x = await _participation(session, participation_id)
    if status not in _NEXT.get(x.status, set()):
        allowed = ", ".join(sorted(_NEXT.get(x.status, set()))) or "nothing"
        raise Conflict(f"a {x.status} record can move to: {allowed}")
    if status == "completed":
        p = await _programme(session, x.programme_id) if x.programme_id else None
        done = completed_on or today()
        if done > today() or (p is not None and done < p.end_date):
            raise ValidationFailed(
                "completion is on or after the programme's last day, not later than today"
            )
        x.completed_on = done
    x.status = status
    x.evidence = (evidence or x.evidence or "").strip()[:300] or None
    x.note = (note or x.note or "").strip()[:500] or None
    await session.flush()
    return _part_view(x)


async def report_activity(
    session: AsyncSession,
    *,
    title: str,
    category: str,
    activity_type: str,
    completed_on: date,
    evidence: str,
    provider: str | None = None,
    started_on: date | None = None,
    hours: Decimal | None = None,
    credit_points: int = 0,
    reflection: str | None = None,
) -> ParticipationView:
    """A staff member reports CPD they did themselves; it counts once someone verifies it."""
    from syntegra.hr.models import Staff

    ctx = current_context()
    me = await _person_of_user(session, ctx.user_id)
    staff = (
        (
            await session.execute(
                select(Staff)
                .where(
                    Staff.tenant_id == ctx.tenant_id,
                    Staff.person_id == me,
                    Staff.status == "active",
                )
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        ).scalar_one_or_none()
        if me
        else None
    )
    if staff is None:
        raise Conflict("only staff can report their own CPD")
    errors = []
    if activity_type not in ACTIVITY_TYPES:
        errors.append(f"activity type must be one of {', '.join(ACTIVITY_TYPES)}")
    _category(category)
    if not title.strip():
        errors.append("give the activity's title")
    if not evidence.strip():
        errors.append(
            "give evidence: a certificate number, attendance letter or document reference"
        )
    if completed_on > today() or (started_on and started_on > completed_on):
        errors.append("dates are not plausible")
    if credit_points < 0 or (hours is not None and hours <= 0):
        errors.append("points cannot be negative and hours must be positive")
    if errors:
        raise ValidationFailed("; ".join(errors))
    x = CpdParticipation(
        tenant_id=staff.tenant_id,
        scope_node_id=staff.scope_node_id,
        staff_id=staff.id,
        title=title.strip()[:200],
        provider=(provider or "").strip()[:200] or None,
        category=category,
        activity_type=activity_type,
        started_on=started_on,
        completed_on=completed_on,
        hours=hours,
        credit_points=credit_points,
        status="self_reported",
        source="self",
        evidence=evidence.strip()[:300],
        reflection=(reflection or "").strip()[:1000] or None,
    )
    session.add(x)
    await session.flush()
    return _part_view(x)


async def verify(
    session: AsyncSession,
    participation_id: uuid.UUID,
    *,
    approve: bool,
    note: str | None = None,
    credit_points: int | None = None,
) -> ParticipationView:
    """Accept CPD as evidence (it then earns its points) or reject it with a reason. A completed
    programme or a self-reported activity can be verified; nobody verifies their own."""
    ctx = current_context()
    x = await _participation(session, participation_id)
    if x.status not in ("completed", "self_reported"):
        raise Conflict("only completed or self-reported CPD can be verified")
    staff = await _staff(session, x.staff_id)
    if await _person_of_user(session, ctx.user_id) == staff.person_id:
        raise Conflict("you cannot verify your own CPD")
    if not approve and not (note or "").strip():
        raise ValidationFailed("say why the CPD is not accepted")
    if credit_points is not None:
        if credit_points < 0:
            raise ValidationFailed("points cannot be negative")
        x.credit_points = credit_points
    x.status = "verified" if approve else "rejected"
    x.verified_by, x.verified_at = ctx.user_id, utcnow()
    x.note = (note or "").strip()[:500] or x.note
    if approve:
        _publish(session, staff, events.CPD_VERIFIED, {"participation_id": str(x.id)})
    await session.flush()
    return _part_view(x)


async def record_of(session: AsyncSession, staff_id: uuid.UUID) -> list[ParticipationView]:
    await _staff(session, staff_id)
    rows = (
        await session.execute(
            select(CpdParticipation)
            .where(CpdParticipation.staff_id == staff_id)
            .order_by(CpdParticipation.id.desc())
        )
    ).scalars()
    return [_part_view(x) for x in rows]


async def participants(session: AsyncSession, programme_id: uuid.UUID) -> list[ParticipationView]:
    p = await _programme(session, programme_id)
    rows = (
        await session.execute(select(CpdParticipation).where(CpdParticipation.programme_id == p.id))
    ).scalars()
    return [_part_view(x) for x in rows]


# --------------------------------------------------------------------------- compliance


async def annual_requirement(session: AsyncSession) -> int:
    value = await governance.resolve_config(session, current_context().scope_node_id, CPD_SETTING)
    if isinstance(value, dict) and isinstance(value.get("annual_points"), int):
        return int(value["annual_points"])
    return DEFAULT_ANNUAL_POINTS


@dataclass(frozen=True, slots=True)
class Compliance:
    staff_id: uuid.UUID
    staff_number: str
    name: str
    category: str
    place: str | None
    points: int
    hours: Decimal
    required: int

    @property
    def shortfall(self) -> int:
        return max(self.required - self.points, 0)

    @property
    def met(self) -> bool:
        return self.points >= self.required


async def compliance(
    session: AsyncSession, year: int, *, teaching_only: bool = True
) -> list[Compliance]:
    """Verified CPD points per staff member in a calendar year, against the requirement."""
    required = await annual_requirement(session)
    roll = await nominal_roll(session, limit=100_000)
    if teaching_only:
        roll = [v for v in roll if v.category == "teaching"]
    ids = [v.id for v in roll]
    totals: dict[uuid.UUID, tuple[int, Decimal]] = {}
    if ids:
        for x in (
            await session.execute(
                select(CpdParticipation).where(
                    CpdParticipation.staff_id.in_(ids),
                    CpdParticipation.status == "verified",
                    CpdParticipation.completed_on >= date(year, 1, 1),
                    CpdParticipation.completed_on <= date(year, 12, 31),
                )
            )
        ).scalars():
            pts, hrs = totals.get(x.staff_id, (0, Decimal(0)))
            totals[x.staff_id] = (pts + x.credit_points, hrs + (x.hours or Decimal(0)))
    out = []
    for v in roll:
        pts, hrs = totals.get(v.id, (0, Decimal(0)))
        post = v.current_post
        out.append(
            Compliance(
                v.id,
                v.staff_number,
                " ".join(p for p in (v.first_name, v.surname) if p),
                v.category,
                post.place_name if post else None,
                pts,
                hrs,
                required,
            )
        )
    return out


# --------------------------------------------------------------------------- development plans


@dataclass(frozen=True, slots=True)
class ActionView:
    id: uuid.UUID
    staff_id: uuid.UUID
    appraisal_id: uuid.UUID | None
    need: str
    action: str
    category: str
    due_date: date | None
    status: str
    participation_id: uuid.UUID | None
    outcome: str | None

    @property
    def overdue(self) -> bool:
        return (
            self.status in ("planned", "in_progress")
            and self.due_date is not None
            and self.due_date < today()
        )


def _action_view(a: DevelopmentAction) -> ActionView:
    return ActionView(
        a.id,
        a.staff_id,
        a.appraisal_id,
        a.need,
        a.action,
        a.category,
        a.due_date,
        a.status,
        a.participation_id,
        a.outcome,
    )


async def add_action(
    session: AsyncSession,
    staff_id: uuid.UUID,
    *,
    need: str,
    action: str,
    category: str,
    due_date: date | None = None,
    appraisal_id: uuid.UUID | None = None,
) -> ActionView:
    staff = await _staff(session, staff_id)
    _category(category)
    if not need.strip() or not action.strip():
        raise ValidationFailed("say what is to be developed and how")
    if appraisal_id is not None:
        found = (
            await session.execute(
                select(Appraisal.id).where(
                    Appraisal.id == appraisal_id, Appraisal.staff_id == staff.id
                )
            )
        ).first()
        if not found:
            raise ValidationFailed("that appraisal is not this staff member's")
    a = DevelopmentAction(
        tenant_id=staff.tenant_id,
        scope_node_id=staff.scope_node_id,
        staff_id=staff.id,
        appraisal_id=appraisal_id,
        need=need.strip()[:300],
        action=action.strip()[:300],
        category=category,
        due_date=due_date,
        status="planned",
    )
    session.add(a)
    await session.flush()
    return _action_view(a)


async def update_action(
    session: AsyncSession,
    action_id: uuid.UUID,
    *,
    status: str,
    outcome: str | None = None,
    participation_id: uuid.UUID | None = None,
) -> ActionView:
    a = (
        await session.execute(select(DevelopmentAction).where(DevelopmentAction.id == action_id))
    ).scalar_one_or_none()
    if a is None:
        raise NotFound("development action not found in your scope")
    if status not in ("planned", "in_progress", "done", "dropped"):
        raise ValidationFailed("status must be planned, in_progress, done or dropped")
    if status in ("done", "dropped") and not (outcome or "").strip():
        raise ValidationFailed("record the outcome (or why it was dropped)")
    if participation_id is not None:
        x = await _participation(session, participation_id)
        if x.staff_id != a.staff_id:
            raise ValidationFailed("that CPD record is another staff member's")
        a.participation_id = x.id
    a.status = status
    a.outcome = (outcome or a.outcome or "").strip()[:500] or None
    await session.flush()
    return _action_view(a)


async def plan_of(session: AsyncSession, staff_id: uuid.UUID) -> list[ActionView]:
    await _staff(session, staff_id)
    rows = (
        await session.execute(
            select(DevelopmentAction)
            .where(DevelopmentAction.staff_id == staff_id)
            .order_by(DevelopmentAction.id)
        )
    ).scalars()
    return [_action_view(a) for a in rows]


# --------------------------------------------------------------------------- training needs


@dataclass(frozen=True, slots=True)
class TrainingNeed:
    place: str
    criterion: str
    cpd_category: str
    staff_rated_low: int  # rated Poor or Fair (1-2)


async def training_needs(session: AsyncSession, cycle_id: uuid.UUID) -> dict[str, Any]:
    """From countersigned appraisals in the caller's scope: where each criterion is weak, the CPD
    category that answers it, and how many appraisals recommended training."""
    cycle = await _cycle(session, cycle_id)
    appraisals = (
        (
            await session.execute(
                select(Appraisal).where(
                    Appraisal.cycle_id == cycle.id, Appraisal.status == "countersigned"
                )
            )
        )
        .scalars()
        .all()
    )
    roll = {v.id: v for v in await nominal_roll(session, limit=100_000)}
    names = {
        c["code"]: c["name"] for cat in ("teaching", "non_teaching") for c in cycle.criteria[cat]
    }
    counts: dict[tuple[str, str], int] = {}
    recommended = 0
    for a in appraisals:
        v = roll.get(a.staff_id)
        place = v.current_post.place_name if v and v.current_post else "(no current post)"
        if a.recommendation == Recommendation.TRAINING.value:
            recommended += 1
        for code, r in (a.ratings or {}).items():
            if isinstance(r, dict) and r.get("rating") in (1, 2):
                counts[(place, code)] = counts.get((place, code), 0) + 1
    needs = [
        TrainingNeed(place, names.get(code, code), CRITERION_CPD.get(code, "other"), n)
        for (place, code), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    by_category: dict[str, int] = {}
    for n in needs:
        by_category[n.cpd_category] = by_category.get(n.cpd_category, 0) + n.staff_rated_low
    return {
        "appraisals": len(appraisals),
        "training_recommended": recommended,
        "needs": needs,
        "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
    }
