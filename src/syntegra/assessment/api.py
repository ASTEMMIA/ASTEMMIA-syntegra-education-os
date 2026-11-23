"""Continuous assessment and examinations (CEDA §5, ADR-032).

* **Result sheets.** One per subject (or course) per term. Subject teachers enter each learner's
  scores for every part (tests, examination) or mark them absent; the sheet keeps the rules it
  was opened under. A complete sheet is **submitted**; the head (``results:approve``) **approves**
  it or **returns** it with a note, never their own submission (four eyes). An approved sheet is
  locked; reopening it needs a reason, and every step is kept as an evidential event.
* **Examination timetable.** Papers within the term, with no clash for a class (or, for courses,
  any learner registered for both), a room or an invigilator.
* **Report cards** (schools). When every subject of a class is approved, the head publishes the
  class's report cards: each subject's parts, total, grade, position, class average, highest and
  lowest; the overall total, average and position; attendance; the class teacher's and head's
  remarks; when the next term begins. Exactly what was issued is kept; a correction is a new
  version.
* **Tertiary results.** An approved course result is released; GPA per semester and CGPA are
  weighted by course units, with the class of award.
* **Transcripts.** Every published report card (schools) or approved course (tertiary), issued
  with a reference and a checksum, and kept as issued.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Executable, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.academic.domain import YEARS
from syntegra.academic.models import (
    AcademicPeriod,
    ClassGroup,
    Offering,
    Room,
    Subject,
    TeachingAssignment,
)
from syntegra.assessment.domain import (
    DEFAULTS,
    GROUPS,
    LEVEL_GROUP,
    Policy,
    check_policy,
    competition_rank,
    gpa,
    ordinal,
    round2,
)
from syntegra.assessment.models import (
    AssessmentPolicy,
    ExamPaper,
    ReportCard,
    ReportComment,
    ResultSheet,
    Score,
    SheetEvent,
    TranscriptIssue,
)
from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.hr.models import Staff
from syntegra.institutions.models import Institution
from syntegra.learners.models import (
    ClassMembership,
    Enrolment,
    Learner,
    LearnerGuardian,
    SubjectRegistration,
)
from syntegra.mdm.api import is_demo
from syntegra.mdm.models import Person
from syntegra.platform.sequence import next_value
from syntegra.platform.session import PLATFORM_BYPASS

BYPASS: dict[str, Any] = {PLATFORM_BYPASS: True}
ZERO = Decimal(0)


def _bypass[Sel: Executable](stmt: Sel) -> Sel:
    return stmt.execution_options(**BYPASS)


def _s(v: Decimal | None) -> str | None:
    return None if v is None else str(round2(v))


def _full(p: Person) -> str:
    return " ".join(x for x in (p.given_name, p.other_names, p.family_name) if x)


def _name(p: Person) -> str:
    return f"{p.family_name}, {p.given_name}" + (f" {p.other_names}" if p.other_names else "")


# --------------------------------------------------------------------------- policies


async def policy_for(session: AsyncSession, scope_id: uuid.UUID, level: str) -> Policy:
    """The rules for a level at a scope: the nearest scope's own, else the default."""
    group = LEVEL_GROUP.get(level, "basic")
    scopes = await governance.ancestor_ids(session, scope_id) | {scope_id}
    rows = list(
        (
            await session.execute(
                _bypass(
                    select(AssessmentPolicy).where(
                        AssessmentPolicy.group_code == group,
                        AssessmentPolicy.scope_node_id.in_(scopes),
                    )
                )
            )
        ).scalars()
    )
    if not rows:
        return DEFAULTS[group]
    depth = {r.id: (await governance.get_scope_node(session, r.scope_node_id)).level for r in rows}
    return Policy.from_json(max(rows, key=lambda r: depth[r.id]).rules)


@dataclass(frozen=True, slots=True)
class PolicyView:
    group: str
    source: str  # "default", or the name of the scope that set it
    rules: dict[str, Any]


async def policies(session: AsyncSession) -> list[PolicyView]:
    """The rules in force at the caller's scope, for each group of levels."""
    ctx = current_context()
    scopes = await governance.ancestor_ids(session, ctx.scope_node_id) | {ctx.scope_node_id}
    out = []
    for g in GROUPS:
        rows = list(
            (
                await session.execute(
                    _bypass(
                        select(AssessmentPolicy).where(
                            AssessmentPolicy.group_code == g,
                            AssessmentPolicy.scope_node_id.in_(scopes),
                        )
                    )
                )
            ).scalars()
        )
        if not rows:
            out.append(PolicyView(g, "default", DEFAULTS[g].to_json()))
            continue
        nodes = {r.id: await governance.get_scope_node(session, r.scope_node_id) for r in rows}
        best = max(rows, key=lambda r: nodes[r.id].level)
        out.append(PolicyView(g, nodes[best.id].name, best.rules))
    return out


async def set_policy(session: AsyncSession, rules: dict[str, Any]) -> PolicyView:
    """Set this scope's own rules for a group of levels. Sheets already opened keep theirs."""
    ctx = current_context()
    try:
        p = Policy.from_json(rules)
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ValidationFailed(f"the rules are not complete: {exc}") from exc
    errors = check_policy(p)
    if errors:
        raise ValidationFailed("; ".join(errors))
    row = (
        await session.execute(
            select(AssessmentPolicy).where(
                AssessmentPolicy.scope_node_id == ctx.scope_node_id,
                AssessmentPolicy.group_code == p.group,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            AssessmentPolicy(
                tenant_id=ctx.tenant_id,
                scope_node_id=ctx.scope_node_id,
                group_code=p.group,
                rules=p.to_json(),
            )
        )
    else:
        row.rules = p.to_json()
    await session.flush()
    node = await governance.get_scope_node(session, ctx.scope_node_id)
    return PolicyView(p.group, node.name, p.to_json())


# --------------------------------------------------------------------------- who takes what


async def _offering(session: AsyncSession, offering_id: uuid.UUID) -> Offering:
    o = (
        await session.execute(select(Offering).where(Offering.id == offering_id))
    ).scalar_one_or_none()
    if o is None:
        raise NotFound("subject not found in your scope")
    return o


async def _period(session: AsyncSession, period_id: uuid.UUID) -> AcademicPeriod:
    p = (
        await session.execute(_bypass(select(AcademicPeriod).where(AcademicPeriod.id == period_id)))
    ).scalar_one_or_none()
    if p is None:
        raise NotFound("term not found")
    return p


async def _term_of(session: AsyncSession, o: Offering, term_id: uuid.UUID) -> AcademicPeriod:
    """A school subject runs all session and is assessed each term; a course is its semester."""
    period = await _period(session, o.period_id)
    if period.kind == "session":
        term = await _period(session, term_id)
        if term.parent_id != period.id:
            raise ValidationFailed("that term is not in the subject's session")
        return term
    if term_id != o.period_id:
        raise ValidationFailed("a course is assessed in its own semester")
    return period


async def takers(
    session: AsyncSession, o: Offering, term: AcademicPeriod
) -> list[tuple[uuid.UUID, uuid.UUID, Person]]:
    """(learner, enrolment, person) of everyone taking the subject in that term."""
    if o.class_group_id is not None and o.kind == "core":
        return await class_members(session, o.class_group_id, term)
    else:
        stmt = (
            select(Enrolment.learner_id, SubjectRegistration.enrolment_id, Person)
            .join(Enrolment, Enrolment.id == SubjectRegistration.enrolment_id)
            .join(Learner, Learner.id == Enrolment.learner_id)
            .join(Person, Person.id == Learner.person_id)
            .where(
                SubjectRegistration.offering_id == o.id,
                SubjectRegistration.status == "registered",
            )
        )
    rows = (
        await session.execute(_bypass(stmt.order_by(Person.family_name, Person.given_name)))
    ).all()
    seen: set[uuid.UUID] = set()
    out = []
    for lid, eid, p in rows:
        if lid not in seen:
            seen.add(lid)
            out.append((lid, eid, p))
    return out


async def class_members(
    session: AsyncSession, class_id: uuid.UUID, term: AcademicPeriod
) -> list[tuple[uuid.UUID, uuid.UUID, Person]]:
    """Everyone in the class at any time during the term."""
    rows = (
        await session.execute(
            _bypass(
                select(ClassMembership.learner_id, ClassMembership.enrolment_id, Person)
                .join(Learner, Learner.id == ClassMembership.learner_id)
                .join(Person, Person.id == Learner.person_id)
                .where(
                    ClassMembership.class_group_id == class_id,
                    ClassMembership.valid_from <= term.ends_on,
                    (ClassMembership.valid_to.is_(None))
                    | (ClassMembership.valid_to >= term.starts_on),
                )
                .order_by(Person.family_name, Person.given_name)
            )
        )
    ).all()
    seen: set[uuid.UUID] = set()
    out = []
    for lid, eid, p in rows:
        if lid not in seen:
            seen.add(lid)
            out.append((lid, eid, p))
    return out


async def teaches(
    session: AsyncSession, staff_ids: list[uuid.UUID], offering_id: uuid.UUID
) -> bool:
    if not staff_ids:
        return False
    d = today()
    row = (
        await session.execute(
            _bypass(
                select(TeachingAssignment.id).where(
                    TeachingAssignment.offering_id == offering_id,
                    TeachingAssignment.staff_id.in_(staff_ids),
                    TeachingAssignment.valid_from <= d,
                    (TeachingAssignment.valid_to.is_(None)) | (TeachingAssignment.valid_to >= d),
                )
            )
        )
    ).first()
    return row is not None


async def is_class_teacher(
    session: AsyncSession, staff_ids: list[uuid.UUID], class_id: uuid.UUID
) -> bool:
    c = (
        await session.execute(_bypass(select(ClassGroup).where(ClassGroup.id == class_id)))
    ).scalar_one_or_none()
    return c is not None and bool({c.class_teacher_staff_id, c.assistant_staff_id} & set(staff_ids))


async def children_of(session: AsyncSession, person_id: uuid.UUID | None) -> set[uuid.UUID]:
    """Learners a parent may see: parental responsibility, and no court order against them."""
    if person_id is None:
        return set()
    rows = await session.execute(
        _bypass(
            select(LearnerGuardian.learner_id).where(
                LearnerGuardian.guardian_person_id == person_id,
                LearnerGuardian.parental_responsibility.is_(True),
                LearnerGuardian.court_order_flag.is_(False),
                LearnerGuardian.valid_to.is_(None),
            )
        )
    )
    return set(rows.scalars())


# --------------------------------------------------------------------------- result sheets


async def _sheet(
    session: AsyncSession, o: Offering, term: AcademicPeriod, *, create: bool
) -> ResultSheet | None:
    sh = (
        await session.execute(
            _bypass(
                select(ResultSheet).where(
                    ResultSheet.offering_id == o.id, ResultSheet.term_id == term.id
                )
            )
        )
    ).scalar_one_or_none()
    if sh is None and create:
        inst = (
            await session.execute(
                _bypass(select(Institution).where(Institution.id == o.institution_id))
            )
        ).scalar_one()
        level = YEARS[o.year_code].level if o.year_code in YEARS else "primary"
        policy = await policy_for(session, inst.scope_node_id, level)
        sh = ResultSheet(
            tenant_id=o.tenant_id,
            scope_node_id=o.scope_node_id,
            institution_id=o.institution_id,
            offering_id=o.id,
            term_id=term.id,
            rules=policy.to_json(),
            status="open",
        )
        session.add(sh)
        await session.flush()
    return sh


@dataclass(frozen=True, slots=True)
class Line:
    learner_id: uuid.UUID
    enrolment_id: uuid.UUID
    name: str
    demo: bool
    scores: dict[str, dict[str, Any]]  # part -> {"score": "12.50" | None, "absent": bool}
    total: str | None
    grade: str | None
    remark: str | None
    position: int | None
    complete: bool


@dataclass(frozen=True, slots=True)
class SheetView:
    id: uuid.UUID | None
    offering_id: uuid.UUID
    subject: str
    subject_code: str
    class_name: str | None
    year_code: str
    term_id: uuid.UUID
    term_name: str
    status: str
    rules: dict[str, Any]
    lines: list[Line]
    count: int
    missing: int
    average: str | None
    highest: str | None
    lowest: str | None
    passed: int
    return_note: str | None
    events: list[dict[str, Any]] = field(default_factory=list)


def _totals(
    policy: Policy, members: list[tuple[uuid.UUID, uuid.UUID, Person]], scores: list[Score]
) -> tuple[list[Line], dict[uuid.UUID, Decimal]]:
    by: dict[uuid.UUID, dict[str, Score]] = {}
    for sc in scores:
        by.setdefault(sc.learner_id, {})[sc.component] = sc
    codes = [c.code for c in policy.components]
    totals: dict[uuid.UUID, Decimal] = {}
    complete: dict[uuid.UUID, bool] = {}
    for lid, _eid, _p in members:
        mine = by.get(lid, {})
        done = all(c in mine and (mine[c].absent or mine[c].score is not None) for c in codes)
        complete[lid] = done
        if done:
            totals[lid] = sum((mine[c].score or ZERO for c in codes if not mine[c].absent), ZERO)
    ranks = competition_rank(totals)
    lines = []
    for lid, eid, p in members:
        mine = by.get(lid, {})
        t = totals.get(lid)
        band = policy.grade(t) if t is not None else None
        lines.append(
            Line(
                lid,
                eid,
                _name(p),
                is_demo(p.source_system),
                {
                    c: {"score": _s(mine[c].score), "absent": mine[c].absent}
                    for c in codes
                    if c in mine
                },
                _s(t),
                band.grade if band else None,
                band.remark if band else None,
                ranks.get(lid),
                complete[lid],
            )
        )
    return lines, totals


async def get_sheet(session: AsyncSession, offering_id: uuid.UUID, term_id: uuid.UUID) -> SheetView:
    o = await _offering(session, offering_id)
    term = await _term_of(session, o, term_id)
    sh = await _sheet(session, o, term, create=False)
    inst = (
        await session.execute(
            _bypass(select(Institution).where(Institution.id == o.institution_id))
        )
    ).scalar_one()
    level = YEARS[o.year_code].level if o.year_code in YEARS else "primary"
    policy = (
        Policy.from_json(sh.rules) if sh else await policy_for(session, inst.scope_node_id, level)
    )
    members = await takers(session, o, term)
    scores = (
        list(
            (await session.execute(_bypass(select(Score).where(Score.sheet_id == sh.id)))).scalars()
        )
        if sh
        else []
    )
    lines, totals = _totals(policy, members, scores)
    subject = (
        await session.execute(_bypass(select(Subject).where(Subject.id == o.subject_id)))
    ).scalar_one()
    cls = (
        (
            await session.execute(
                _bypass(select(ClassGroup).where(ClassGroup.id == o.class_group_id))
            )
        ).scalar_one()
        if o.class_group_id
        else None
    )
    events = []
    if sh:
        for e in (
            await session.execute(
                _bypass(
                    select(SheetEvent).where(SheetEvent.sheet_id == sh.id).order_by(SheetEvent.at)
                )
            )
        ).scalars():
            events.append(
                {
                    "action": e.action,
                    "by": str(e.by_user) if e.by_user else None,
                    "at": e.at.isoformat(),
                    "note": e.note,
                }
            )
    vals = list(totals.values())
    return SheetView(
        sh.id if sh else None,
        o.id,
        subject.name,
        subject.code,
        cls.name if cls else None,
        o.year_code,
        term.id,
        term.name,
        sh.status if sh else "open",
        policy.to_json(),
        lines,
        len(lines),
        sum(1 for ln in lines if not ln.complete),
        _s(sum(vals, ZERO) / len(vals)) if vals else None,
        _s(max(vals)) if vals else None,
        _s(min(vals)) if vals else None,
        sum(1 for v in vals if v >= policy.pass_mark),
        sh.return_note if sh else None,
        events,
    )


@dataclass(frozen=True, slots=True)
class ScoreIn:
    learner_id: uuid.UUID
    component: str
    score: Decimal | None
    absent: bool = False


async def enter_scores(
    session: AsyncSession, offering_id: uuid.UUID, term_id: uuid.UUID, entries: list[ScoreIn]
) -> SheetView:
    """Record scores (or absence) for some learners and parts. Only while the sheet is open."""
    o = await _offering(session, offering_id)
    term = await _term_of(session, o, term_id)
    sh = await _sheet(session, o, term, create=True)
    assert sh is not None
    if sh.status != "open":
        raise Conflict(
            f"the sheet is {sh.status}",
            next_action="Ask the head teacher to return or reopen it to change scores.",
        )
    policy = Policy.from_json(sh.rules)
    maxima = {c.code: c.max_score for c in policy.components}
    members = {lid: eid for lid, eid, _ in await takers(session, o, term)}
    existing = {
        (sc.learner_id, sc.component): sc
        for sc in (
            await session.execute(_bypass(select(Score).where(Score.sheet_id == sh.id)))
        ).scalars()
    }
    problems: list[str] = []
    for e in entries:
        if e.learner_id not in members:
            problems.append("a learner who does not take this subject")
            continue
        if e.component not in maxima:
            problems.append(f"a part that is not on this sheet ({e.component})")
            continue
        value = None
        if not e.absent and e.score is not None:
            value = round2(Decimal(e.score))
            if value != Decimal(e.score) or not ZERO <= value <= maxima[e.component]:
                problems.append(
                    f"{e.component} is out of {maxima[e.component]}, to two decimal places"
                )
                continue
        cur = existing.get((e.learner_id, e.component))
        if cur is None:
            session.add(
                Score(
                    tenant_id=sh.tenant_id,
                    scope_node_id=sh.scope_node_id,
                    sheet_id=sh.id,
                    learner_id=e.learner_id,
                    enrolment_id=members[e.learner_id],
                    component=e.component,
                    score=value,
                    absent=e.absent,
                )
            )
        elif (cur.score, cur.absent) != (value, e.absent):
            cur.score, cur.absent = value, e.absent
    if problems:
        raise ValidationFailed("; ".join(sorted(set(problems))))
    await session.flush()
    return await get_sheet(session, o.id, term.id)


async def _event(session: AsyncSession, sh: ResultSheet, action: str, note: str | None) -> None:
    ctx = current_context()
    session.add(
        SheetEvent(
            tenant_id=sh.tenant_id, sheet_id=sh.id, action=action, by_user=ctx.user_id, note=note
        )
    )


async def _sheet_or_404(
    session: AsyncSession, offering_id: uuid.UUID, term_id: uuid.UUID
) -> tuple[Offering, AcademicPeriod, ResultSheet]:
    o = await _offering(session, offering_id)
    term = await _term_of(session, o, term_id)
    sh = await _sheet(session, o, term, create=False)
    if sh is None:
        raise NotFound("no scores have been entered for this subject and term")
    return o, term, sh


async def submit(session: AsyncSession, offering_id: uuid.UUID, term_id: uuid.UUID) -> SheetView:
    ctx = current_context()
    o, term, sh = await _sheet_or_404(session, offering_id, term_id)
    if sh.status != "open":
        raise Conflict(f"the sheet is already {sh.status}")
    view = await get_sheet(session, o.id, term.id)
    if not view.lines:
        raise ValidationFailed("nobody takes this subject")
    if view.missing:
        raise ValidationFailed(
            f"{view.missing} learner(s) still need a score or 'absent' for every part"
        )
    sh.status, sh.submitted_by, sh.submitted_at, sh.return_note = (
        "submitted",
        ctx.user_id,
        utcnow(),
        None,
    )
    await _event(session, sh, "submitted", None)
    await session.flush()
    return await get_sheet(session, o.id, term.id)


async def approve(session: AsyncSession, offering_id: uuid.UUID, term_id: uuid.UUID) -> SheetView:
    ctx = current_context()
    o, term, sh = await _sheet_or_404(session, offering_id, term_id)
    if sh.status != "submitted":
        raise Conflict("only a submitted sheet can be approved")
    if sh.submitted_by is not None and sh.submitted_by == ctx.user_id:
        raise Forbidden("you cannot approve results you submitted; another officer approves them")
    sh.status, sh.approved_by, sh.approved_at = "approved", ctx.user_id, utcnow()
    await _event(session, sh, "approved", None)
    await session.flush()
    return await get_sheet(session, o.id, term.id)


async def send_back(
    session: AsyncSession, offering_id: uuid.UUID, term_id: uuid.UUID, note: str
) -> SheetView:
    """Return a submitted sheet to the teacher, or reopen an approved one; a reason either way."""
    o, term, sh = await _sheet_or_404(session, offering_id, term_id)
    why = " ".join(note.split())
    if len(why) < 5:
        raise ValidationFailed("say what needs changing")
    if sh.status == "submitted":
        action = "returned"
    elif sh.status == "approved":
        action = "reopened"
    else:
        raise Conflict("the sheet is already open")
    sh.status, sh.return_note, sh.approved_by, sh.approved_at = "open", why[:500], None, None
    await _event(session, sh, action, why[:500])
    await session.flush()
    return await get_sheet(session, o.id, term.id)


@dataclass(frozen=True, slots=True)
class SheetSummary:
    offering_id: uuid.UUID
    subject: str
    class_name: str | None
    year_code: str
    term_id: uuid.UUID
    status: str
    entered: bool
    teachers: list[str]


async def sheets(
    session: AsyncSession, institution_id: uuid.UUID, term_id: uuid.UUID
) -> list[SheetSummary]:
    """Every subject of the institution in a term, and where its results are."""
    term = await _period(session, term_id)
    period_ids = [term.id] + ([term.parent_id] if term.parent_id else [])
    offerings = list(
        (
            await session.execute(
                select(Offering)
                .where(
                    Offering.institution_id == institution_id,
                    Offering.period_id.in_(period_ids),
                    Offering.active.is_(True),
                )
                .order_by(Offering.year_code)
            )
        ).scalars()
    )
    ids = [o.id for o in offerings] or [uuid.UUID(int=0)]
    status = {
        oid: st
        for oid, st in await session.execute(
            _bypass(
                select(ResultSheet.offering_id, ResultSheet.status).where(
                    ResultSheet.offering_id.in_(ids), ResultSheet.term_id == term.id
                )
            )
        )
    }
    subjects = {
        s.id: s
        for s in (
            await session.execute(
                _bypass(
                    select(Subject).where(
                        Subject.id.in_({o.subject_id for o in offerings} or {uuid.UUID(int=0)})
                    )
                )
            )
        ).scalars()
    }
    classes = {
        c.id: c.name
        for c in (
            await session.execute(
                _bypass(
                    select(ClassGroup).where(
                        ClassGroup.id.in_(
                            {o.class_group_id for o in offerings if o.class_group_id}
                            or {uuid.UUID(int=0)}
                        )
                    )
                )
            )
        ).scalars()
    }
    teachers: dict[uuid.UUID, list[str]] = {}
    d = today()
    for oid, given, family in await session.execute(
        _bypass(
            select(TeachingAssignment.offering_id, Person.given_name, Person.family_name)
            .join(Staff, Staff.id == TeachingAssignment.staff_id)
            .join(Person, Person.id == Staff.person_id)
            .where(
                TeachingAssignment.offering_id.in_(ids),
                TeachingAssignment.valid_from <= d,
                (TeachingAssignment.valid_to.is_(None)) | (TeachingAssignment.valid_to >= d),
            )
        )
    ):
        teachers.setdefault(oid, []).append(f"{given} {family}")
    out = [
        SheetSummary(
            o.id,
            subjects[o.subject_id].name,
            classes.get(o.class_group_id) if o.class_group_id else None,
            o.year_code,
            term.id,
            status.get(o.id, "open"),
            o.id in status,
            teachers.get(o.id, []),
        )
        for o in offerings
    ]
    return sorted(out, key=lambda s: (s.class_name or s.year_code, s.subject))


@dataclass(frozen=True, slots=True)
class Taught:
    offering_id: uuid.UUID
    subject: str
    class_name: str | None
    year_code: str
    session_id: uuid.UUID
    terms: list[dict[str, Any]]


async def taught_by(session: AsyncSession, staff_ids: list[uuid.UUID]) -> list[Taught]:
    """The subjects a teacher teaches now, and the terms they can be assessed in."""
    if not staff_ids:
        return []
    d = today()
    rows = (
        await session.execute(
            _bypass(
                select(Offering, Subject)
                .join(TeachingAssignment, TeachingAssignment.offering_id == Offering.id)
                .join(Subject, Subject.id == Offering.subject_id)
                .where(
                    TeachingAssignment.staff_id.in_(staff_ids),
                    TeachingAssignment.valid_from <= d,
                    (TeachingAssignment.valid_to.is_(None)) | (TeachingAssignment.valid_to >= d),
                    Offering.active.is_(True),
                )
            )
        )
    ).all()
    out = []
    seen: set[uuid.UUID] = set()
    for o, subj in rows:
        if o.id in seen:
            continue
        seen.add(o.id)
        period = await _period(session, o.period_id)
        if period.kind == "session":
            terms = list(
                (
                    await session.execute(
                        _bypass(
                            select(AcademicPeriod)
                            .where(AcademicPeriod.parent_id == period.id)
                            .order_by(AcademicPeriod.seq)
                        )
                    )
                ).scalars()
            )
            session_id = period.id
        else:
            terms, session_id = [period], period.parent_id or period.id
        cls = (
            (
                await session.execute(
                    _bypass(select(ClassGroup).where(ClassGroup.id == o.class_group_id))
                )
            ).scalar_one()
            if o.class_group_id
            else None
        )
        out.append(
            Taught(
                o.id,
                subj.name,
                cls.name if cls else None,
                o.year_code,
                session_id,
                [
                    {
                        "id": str(t.id),
                        "name": t.name,
                        "starts_on": t.starts_on.isoformat(),
                        "ends_on": t.ends_on.isoformat(),
                    }
                    for t in terms
                ],
            )
        )
    return sorted(out, key=lambda t: (t.class_name or t.year_code, t.subject))


# --------------------------------------------------------------------------- exam timetable


@dataclass(frozen=True, slots=True)
class PaperView:
    id: uuid.UUID
    offering_id: uuid.UUID
    subject: str
    class_name: str | None
    year_code: str
    starts_at: datetime
    ends_at: datetime
    room: str | None
    invigilator: str | None
    status: str
    note: str | None


async def _paper_view(session: AsyncSession, p: ExamPaper) -> PaperView:
    o = (
        await session.execute(_bypass(select(Offering).where(Offering.id == p.offering_id)))
    ).scalar_one()
    subj = (
        await session.execute(_bypass(select(Subject).where(Subject.id == o.subject_id)))
    ).scalar_one()
    cls = (
        (
            await session.execute(
                _bypass(select(ClassGroup).where(ClassGroup.id == o.class_group_id))
            )
        ).scalar_one()
        if o.class_group_id
        else None
    )
    room = (
        (await session.execute(_bypass(select(Room).where(Room.id == p.room_id)))).scalar_one()
        if p.room_id
        else None
    )
    inv = None
    if p.invigilator_staff_id:
        person = (
            await session.execute(
                _bypass(
                    select(Person)
                    .join(Staff, Staff.person_id == Person.id)
                    .where(Staff.id == p.invigilator_staff_id)
                )
            )
        ).scalar_one()
        inv = f"{person.given_name} {person.family_name}"
    return PaperView(
        p.id,
        o.id,
        subj.name,
        cls.name if cls else None,
        o.year_code,
        p.starts_at,
        p.starts_at + timedelta(minutes=p.duration_minutes),
        room.name if room else None,
        inv,
        p.status,
        p.note,
    )


@dataclass(frozen=True, slots=True)
class PaperIn:
    offering_id: uuid.UUID
    term_id: uuid.UUID
    starts_at: datetime
    duration_minutes: int
    room_id: uuid.UUID | None = None
    invigilator_staff_id: uuid.UUID | None = None
    note: str | None = None


async def schedule_paper(session: AsyncSession, data: PaperIn) -> PaperView:
    """Put a paper on the timetable, refusing any clash."""
    o = await _offering(session, data.offering_id)
    term = await _term_of(session, o, data.term_id)
    if not 10 <= data.duration_minutes <= 360:
        raise ValidationFailed("a paper lasts 10 minutes to 6 hours")
    start = data.starts_at.replace(tzinfo=None, second=0, microsecond=0)
    end = start + timedelta(minutes=data.duration_minutes)
    if not term.starts_on <= start.date() <= term.ends_on:
        raise ValidationFailed(
            f"the paper is outside {term.name} ({term.starts_on} to {term.ends_on})"
        )
    if data.room_id:
        room = (
            await session.execute(_bypass(select(Room).where(Room.id == data.room_id)))
        ).scalar_one_or_none()
        if room is None or room.institution_id != o.institution_id:
            raise ValidationFailed("that room is not at this institution")
    others = list(
        (
            await session.execute(
                _bypass(
                    select(ExamPaper).where(
                        ExamPaper.institution_id == o.institution_id,
                        ExamPaper.status == "scheduled",
                        ExamPaper.starts_at < end,
                        ExamPaper.starts_at > start - timedelta(hours=6),
                    )
                )
            )
        ).scalars()
    )
    overlapping = [x for x in others if x.starts_at + timedelta(minutes=x.duration_minutes) > start]
    mine = {lid for lid, _, _ in await takers(session, o, term)}
    for x in overlapping:
        if x.offering_id == o.id:
            raise Conflict("this subject already has a paper at that time")
        xo = (
            await session.execute(_bypass(select(Offering).where(Offering.id == x.offering_id)))
        ).scalar_one()
        xt = await _period(session, x.term_id)
        if mine & {lid for lid, _, _ in await takers(session, xo, xt)}:
            v = await _paper_view(session, x)
            raise Conflict(
                f"learners taking this subject have {v.subject} ({v.class_name or v.year_code}) "
                f"then, {v.starts_at:%H:%M}-{v.ends_at:%H:%M}"
            )
        if data.room_id and x.room_id == data.room_id:
            raise Conflict("the room is in use for another paper then")
        if data.invigilator_staff_id and x.invigilator_staff_id == data.invigilator_staff_id:
            raise Conflict("the invigilator is supervising another paper then")
    p = ExamPaper(
        tenant_id=o.tenant_id,
        scope_node_id=o.scope_node_id,
        institution_id=o.institution_id,
        offering_id=o.id,
        term_id=term.id,
        starts_at=start,
        duration_minutes=data.duration_minutes,
        room_id=data.room_id,
        invigilator_staff_id=data.invigilator_staff_id,
        note=(data.note or "").strip()[:200] or None,
    )
    session.add(p)
    await session.flush()
    return await _paper_view(session, p)


async def cancel_paper(session: AsyncSession, paper_id: uuid.UUID) -> PaperView:
    p = (
        await session.execute(select(ExamPaper).where(ExamPaper.id == paper_id))
    ).scalar_one_or_none()
    if p is None:
        raise NotFound("paper not found")
    p.status = "cancelled"
    await session.flush()
    return await _paper_view(session, p)


async def timetable(
    session: AsyncSession, institution_id: uuid.UUID, term_id: uuid.UUID
) -> list[PaperView]:
    rows = (
        await session.execute(
            select(ExamPaper)
            .where(
                ExamPaper.institution_id == institution_id,
                ExamPaper.term_id == term_id,
                ExamPaper.status == "scheduled",
            )
            .order_by(ExamPaper.starts_at)
        )
    ).scalars()
    return [await _paper_view(session, p) for p in rows]


# --------------------------------------------------------------------------- report cards


async def set_comment(
    session: AsyncSession,
    learner_id: uuid.UUID,
    term_id: uuid.UUID,
    *,
    class_teacher: str | None = None,
    head: str | None = None,
) -> None:
    ln = (
        await session.execute(select(Learner).where(Learner.id == learner_id))
    ).scalar_one_or_none()
    if ln is None:
        raise NotFound("learner not found in your scope")
    row = (
        await session.execute(
            _bypass(
                select(ReportComment).where(
                    ReportComment.learner_id == ln.id, ReportComment.term_id == term_id
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ReportComment(
            tenant_id=ln.tenant_id,
            scope_node_id=ln.scope_node_id,
            learner_id=ln.id,
            term_id=term_id,
        )
        session.add(row)
    if class_teacher is not None:
        row.class_teacher = " ".join(class_teacher.split())[:500] or None
    if head is not None:
        row.head = " ".join(head.split())[:500] or None
    await session.flush()


@dataclass(frozen=True, slots=True)
class ClassResults:
    class_id: uuid.UUID
    class_name: str
    term_id: uuid.UUID
    term_name: str
    ready: bool
    waiting: list[dict[str, str]]  # subjects not approved yet
    published: int  # learners with a published card for this term
    cards: list[dict[str, Any]]  # what would be published (preview)


async def _class_term(
    session: AsyncSession, class_id: uuid.UUID, term_id: uuid.UUID
) -> tuple[ClassGroup, AcademicPeriod]:
    c = (
        await session.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    ).scalar_one_or_none()
    if c is None:
        raise NotFound("class not found in your scope")
    term = await _period(session, term_id)
    if term.parent_id != c.session_id:
        raise ValidationFailed("that term is not in the class's session")
    return c, term


async def _cards(
    session: AsyncSession, c: ClassGroup, term: AcademicPeriod
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Each class member's report card for the term, and the subjects not approved yet."""
    from syntegra.attendance.api import learner_summary

    offerings = list(
        (
            await session.execute(
                _bypass(
                    select(Offering).where(
                        Offering.class_group_id == c.id, Offering.active.is_(True)
                    )
                )
            )
        ).scalars()
    )
    session_period = await _period(session, c.session_id)
    members = {lid: (eid, p) for lid, eid, p in await class_members(session, c.id, term)}
    inst = (
        await session.execute(
            _bypass(select(Institution).where(Institution.id == c.institution_id))
        )
    ).scalar_one()
    waiting: list[dict[str, str]] = []
    subjects: dict[uuid.UUID, list[dict[str, Any]]] = {lid: [] for lid in members}
    bands_seen: dict[str, Any] = {}
    for o in sorted(offerings, key=lambda x: x.kind):
        view = await get_sheet(session, o.id, term.id)
        if not view.lines:
            continue
        if view.status != "approved":
            waiting.append({"subject": view.subject, "status": view.status})
            continue
        bands_seen = view.rules
        for ln in view.lines:
            # Someone who joined the class after the sheet was approved has no result in it.
            if ln.learner_id not in subjects or ln.total is None:
                continue
            subjects[ln.learner_id].append(
                {
                    "subject": view.subject,
                    "code": view.subject_code,
                    "parts": {
                        k: v["score"] if not v["absent"] else "ABS" for k, v in ln.scores.items()
                    },
                    "total": ln.total,
                    "grade": ln.grade,
                    "remark": ln.remark,
                    "position": ordinal(ln.position) if ln.position else None,
                    "class_average": view.average,
                    "highest": view.highest,
                    "lowest": view.lowest,
                }
            )
    averages = {
        lid: round2(sum((Decimal(s["total"]) for s in subs), ZERO) / len(subs))
        for lid, subs in subjects.items()
        if subs
    }
    ranks = competition_rank(averages)
    class_avg = _s(sum(averages.values(), ZERO) / len(averages)) if averages else None
    nxt = (
        await session.execute(
            _bypass(
                select(AcademicPeriod.starts_on).where(
                    AcademicPeriod.parent_id == session_period.id,
                    AcademicPeriod.seq == term.seq + 1,
                )
            )
        )
    ).scalar_one_or_none()
    comments = {
        r.learner_id: r
        for r in (
            await session.execute(
                _bypass(
                    select(ReportComment).where(
                        ReportComment.term_id == term.id,
                        ReportComment.learner_id.in_(list(members) or [uuid.UUID(int=0)]),
                    )
                )
            )
        ).scalars()
    }
    cards = []
    for lid, (eid, p) in members.items():
        subs = subjects[lid]
        if not subs:
            continue
        lr = (await session.execute(_bypass(select(Learner).where(Learner.id == lid)))).scalar_one()
        e = (
            await session.execute(_bypass(select(Enrolment).where(Enrolment.id == eid)))
        ).scalar_one()
        att = await learner_summary(session, lid, term.id)
        cm = comments.get(lid)
        total = sum((Decimal(s["total"]) for s in subs), ZERO)
        cards.append(
            {
                "learner": {
                    "id": str(lid),
                    "name": _full(p),
                    "learner_number": lr.learner_number,
                    "admission_number": e.admission_number,
                    "sex": p.sex_at_registration,
                    "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
                    "demo": is_demo(p.source_system),
                },
                "school": {"name": inst.name, "code": inst.institution_code, "lga": inst.location},
                "class": {"id": str(c.id), "name": c.name, "year": c.year_code},
                "session": session_period.name,
                "term": {"id": str(term.id), "name": term.name},
                "subjects": sorted(subs, key=lambda s: s["subject"]),
                "summary": {
                    "subjects": len(subs),
                    "total": _s(total),
                    "average": _s(averages[lid]),
                    "position": ordinal(ranks[lid]),
                    "out_of": len(averages),
                    "class_average": class_avg,
                },
                "attendance": att.as_dict(),
                "comments": {
                    "class_teacher": cm.class_teacher if cm else None,
                    "head": cm.head if cm else None,
                },
                "rules": bands_seen,
                "next_term_begins": nxt.isoformat() if nxt else None,
            }
        )
    return cards, waiting


def _sha(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


async def class_results(
    session: AsyncSession, class_id: uuid.UUID, term_id: uuid.UUID
) -> ClassResults:
    c, term = await _class_term(session, class_id, term_id)
    cards, waiting = await _cards(session, c, term)
    published = (
        await session.execute(
            _bypass(
                select(func.count(func.distinct(ReportCard.learner_id))).where(
                    ReportCard.class_group_id == c.id, ReportCard.term_id == term.id
                )
            )
        )
    ).scalar_one()
    return ClassResults(
        c.id,
        c.name,
        term.id,
        term.name,
        not waiting and bool(cards),
        waiting,
        int(published),
        cards,
    )


async def publish_reports(
    session: AsyncSession, class_id: uuid.UUID, term_id: uuid.UUID
) -> ClassResults:
    """Issue the class's report cards for the term. Only when every subject is approved; a card
    that has not changed since it was last issued is not issued again."""
    ctx = current_context()
    c, term = await _class_term(session, class_id, term_id)
    cards, waiting = await _cards(session, c, term)
    if waiting:
        raise Conflict(
            f"{len(waiting)} subject(s) are not approved yet: "
            + ", ".join(w["subject"] for w in waiting[:6])
        )
    if not cards:
        raise ValidationFailed("there are no results to publish")
    issued = 0
    for content in cards:
        lid = uuid.UUID(content["learner"]["id"])
        latest = (
            await session.execute(
                _bypass(
                    select(ReportCard)
                    .where(ReportCard.learner_id == lid, ReportCard.term_id == term.id)
                    .order_by(ReportCard.version.desc())
                    .limit(1)
                )
            )
        ).scalar_one_or_none()
        digest = _sha(content)
        if latest and latest.content_sha256 == digest:
            continue
        session.add(
            ReportCard(
                tenant_id=c.tenant_id,
                scope_node_id=c.scope_node_id,
                learner_id=lid,
                institution_id=c.institution_id,
                class_group_id=c.id,
                term_id=term.id,
                version=(latest.version + 1) if latest else 1,
                content=content,
                content_sha256=digest,
                published_by=ctx.user_id,
            )
        )
        issued += 1
    if not issued:
        raise Conflict("the report cards are already published and nothing has changed")
    await session.flush()
    return await class_results(session, c.id, term.id)


@dataclass(frozen=True, slots=True)
class CardView:
    id: uuid.UUID
    learner_id: uuid.UUID
    term_id: uuid.UUID
    version: int
    published_at: datetime
    content: dict[str, Any]
    content_sha256: str


def _card(r: ReportCard) -> CardView:
    return CardView(
        r.id, r.learner_id, r.term_id, r.version, r.published_at, r.content, r.content_sha256
    )


async def report_cards(session: AsyncSession, learner_id: uuid.UUID) -> list[CardView]:
    """A learner's published report cards, the latest version of each term, newest first."""
    rows = list(
        (
            await session.execute(
                _bypass(
                    select(ReportCard)
                    .where(ReportCard.learner_id == learner_id)
                    .order_by(ReportCard.published_at.desc(), ReportCard.version.desc())
                )
            )
        ).scalars()
    )
    latest: dict[uuid.UUID, ReportCard] = {}
    for r in rows:
        if r.term_id not in latest or r.version > latest[r.term_id].version:
            latest[r.term_id] = r
    return [_card(r) for r in sorted(latest.values(), key=lambda r: r.published_at, reverse=True)]


async def get_report_card(session: AsyncSession, card_id: uuid.UUID) -> CardView:
    r = (
        await session.execute(_bypass(select(ReportCard).where(ReportCard.id == card_id)))
    ).scalar_one_or_none()
    if r is None:
        raise NotFound("report card not found")
    return _card(r)


# --------------------------------------------------------------------------- tertiary results


@dataclass(frozen=True, slots=True)
class SemesterResult:
    term_id: uuid.UUID
    term_name: str
    session_name: str
    courses: list[dict[str, Any]]
    units: int
    gpa: str | None
    cgpa: str | None


async def course_results(session: AsyncSession, learner_id: uuid.UUID) -> list[SemesterResult]:
    """Approved course results by semester, with GPA and CGPA (weighted by course units)."""
    rows = (
        await session.execute(
            _bypass(
                select(ResultSheet, Offering, Subject, AcademicPeriod)
                .join(Offering, Offering.id == ResultSheet.offering_id)
                .join(Subject, Subject.id == Offering.subject_id)
                .join(AcademicPeriod, AcademicPeriod.id == ResultSheet.term_id)
                .join(Score, Score.sheet_id == ResultSheet.id)
                .where(Score.learner_id == learner_id, ResultSheet.status == "approved")
                .order_by(AcademicPeriod.starts_on, Subject.code)
            )
        )
    ).all()
    by_term: dict[uuid.UUID, list[dict[str, Any]]] = {}
    terms: dict[uuid.UUID, AcademicPeriod] = {}
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for sh, o, subj, term in rows:
        policy = Policy.from_json(sh.rules)
        if not policy.tertiary or (sh.id, term.id) in seen:
            continue
        seen.add((sh.id, term.id))
        scores = list(
            (
                await session.execute(
                    _bypass(
                        select(Score).where(Score.sheet_id == sh.id, Score.learner_id == learner_id)
                    )
                )
            ).scalars()
        )
        total = sum((s.score or ZERO for s in scores if not s.absent), ZERO)
        band = policy.grade(total)
        units = o.units or subj.units or 1
        terms[term.id] = term
        by_term.setdefault(term.id, []).append(
            {
                "code": subj.code,
                "title": subj.name,
                "units": units,
                "total": _s(total),
                "grade": band.grade,
                "points": str(band.points) if band.points is not None else None,
                "policy": policy.group,
            }
        )
    out = []
    so_far: list[tuple[Decimal | None, int]] = []
    for tid, courses in by_term.items():
        term = terms[tid]
        rows_ = [(Decimal(cr["points"]) if cr["points"] else None, cr["units"]) for cr in courses]
        so_far += rows_
        parent = await _period(session, term.parent_id) if term.parent_id else term
        out.append(
            SemesterResult(
                tid,
                term.name,
                parent.name,
                courses,
                sum(u for _, u in rows_),
                _s(gpa(rows_)),
                _s(gpa(so_far)),
            )
        )
    return out


# --------------------------------------------------------------------------- transcripts


async def transcript(session: AsyncSession, learner_id: uuid.UUID) -> dict[str, Any]:
    """Everything a learner has been awarded on the platform, as a transcript."""
    ln = (
        await session.execute(select(Learner).where(Learner.id == learner_id))
    ).scalar_one_or_none()
    if ln is None:
        raise NotFound("learner not found in your scope")
    p = (
        await session.execute(_bypass(select(Person).where(Person.id == ln.person_id)))
    ).scalar_one()
    cards = await report_cards(session, ln.id)
    school_terms = [
        {
            "session": c.content["session"],
            "term": c.content["term"]["name"],
            "school": c.content["school"]["name"],
            "class": c.content["class"]["name"],
            "subjects": [
                {"subject": s["subject"], "total": s["total"], "grade": s["grade"]}
                for s in c.content["subjects"]
            ],
            "average": c.content["summary"]["average"],
            "position": f"{c.content['summary']['position']} of {c.content['summary']['out_of']}",
            "report_card": str(c.id),
        }
        for c in sorted(cards, key=lambda c: (c.content["session"], c.content["term"]["name"]))
    ]
    semesters = await course_results(session, ln.id)
    award = None
    if semesters and semesters[-1].cgpa:
        group = semesters[-1].courses[-1]["policy"]
        award = DEFAULTS[group].award(Decimal(semesters[-1].cgpa))
    return {
        "learner": {
            "name": _full(p),
            "learner_number": ln.learner_number,
            "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
            "sex": p.sex_at_registration,
            "demo": is_demo(p.source_system),
        },
        "school_terms": school_terms,
        "semesters": [
            {
                "session": s.session_name,
                "semester": s.term_name,
                "courses": s.courses,
                "units": s.units,
                "gpa": s.gpa,
                "cgpa": s.cgpa,
            }
            for s in semesters
        ],
        "cgpa": semesters[-1].cgpa if semesters else None,
        "standing": award,
    }


@dataclass(frozen=True, slots=True)
class TranscriptView:
    id: uuid.UUID
    reference: str
    learner_id: uuid.UUID
    purpose: str
    issued_on: date
    content: dict[str, Any]
    content_sha256: str


def _tview(t: TranscriptIssue) -> TranscriptView:
    return TranscriptView(
        t.id, t.reference, t.learner_id, t.purpose, t.issued_on, t.content, t.content_sha256
    )


async def issue_transcript(
    session: AsyncSession, learner_id: uuid.UUID, purpose: str
) -> TranscriptView:
    ctx = current_context()
    why = " ".join(purpose.split())
    if len(why) < 3:
        raise ValidationFailed("say what the transcript is for (admission, transfer, …)")
    content = await transcript(session, learner_id)
    if not content["school_terms"] and not content["semesters"]:
        raise ValidationFailed("there are no published results to put on a transcript")
    ln = (await session.execute(select(Learner).where(Learner.id == learner_id))).scalar_one()
    n = await next_value(session, ctx.tenant_id, f"transcript-{today().year}")
    t = TranscriptIssue(
        tenant_id=ln.tenant_id,
        scope_node_id=ln.scope_node_id,
        learner_id=ln.id,
        reference=f"TRN-{today().year}-{n:06d}",
        purpose=why[:200],
        content=content,
        content_sha256=_sha(content),
        issued_by=ctx.user_id,
        issued_on=today(),
    )
    session.add(t)
    await session.flush()
    return _tview(t)


async def transcripts(session: AsyncSession, learner_id: uuid.UUID) -> list[TranscriptView]:
    rows = (
        await session.execute(
            _bypass(
                select(TranscriptIssue)
                .where(TranscriptIssue.learner_id == learner_id)
                .order_by(TranscriptIssue.issued_on.desc())
            )
        )
    ).scalars()
    return [_tview(t) for t in rows]


async def get_transcript(session: AsyncSession, transcript_id: uuid.UUID) -> TranscriptView:
    t = (
        await session.execute(
            _bypass(select(TranscriptIssue).where(TranscriptIssue.id == transcript_id))
        )
    ).scalar_one_or_none()
    if t is None:
        raise NotFound("transcript not found")
    return _tview(t)


# --------------------------------------------------------------------------- monitoring figures


@dataclass
class Performance:
    results: int = 0  # learner results on approved sheets
    passed: int = 0
    total_points: Decimal = ZERO
    by_institution: dict[uuid.UUID, tuple[int, int, Decimal]] = field(default_factory=dict)

    @property
    def average(self) -> str | None:
        return _s(self.total_points / self.results) if self.results else None


async def performance(session: AsyncSession, institution_ids: list[uuid.UUID]) -> Performance:
    """Approved results across institutions: how many, the average, how many passed."""
    perf = Performance()
    sheets_ = list(
        (
            await session.execute(
                _bypass(
                    select(ResultSheet).where(
                        ResultSheet.institution_id.in_(institution_ids or [uuid.UUID(int=0)]),
                        ResultSheet.status == "approved",
                    )
                )
            )
        ).scalars()
    )
    for sh in sheets_:
        pass_mark = Policy.from_json(sh.rules).pass_mark
        totals: dict[uuid.UUID, Decimal] = {}
        for lid, sc, absent in await session.execute(
            _bypass(
                select(Score.learner_id, Score.score, Score.absent).where(Score.sheet_id == sh.id)
            )
        ):
            totals[lid] = totals.get(lid, ZERO) + (ZERO if absent or sc is None else sc)
        n, ok, pts = perf.by_institution.get(sh.institution_id, (0, 0, ZERO))
        passed = sum(1 for t in totals.values() if t >= pass_mark)
        s = sum(totals.values(), ZERO)
        perf.by_institution[sh.institution_id] = (n + len(totals), ok + passed, pts + s)
        perf.results += len(totals)
        perf.passed += passed
        perf.total_points += s
    return perf
