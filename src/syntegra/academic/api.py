"""Academic structure services (ADR-029).

* **Calendar.** A board (ASUBEB) sets the school session and its three terms once for every school
  beneath it; a tertiary institution sets its own session and two semesters. Periods are visible
  from their scope, above it and below it, like appraisal cycles.
* **Curriculum.** The national subject list is loaded as a published, immutable curriculum
  version at the state root. The state (or a board) adopts a version for some years from a
  session; a class resolves to the nearest adoption above its school. Schools may add local
  subjects; tertiary institutions keep their own courses (code, units, level, semester).
* **Classes.** One arm of one year in one session, with a capacity, a room and a class teacher.
  Creating a class lays out its subjects from the adopted curriculum.
* **Teaching.** Each offering (a subject in a class, or a course in a semester) gets a lead
  teacher and optional assistants. Only active teaching staff posted to the institution may
  teach there; a weekly load over the limit is refused; a teacher without current TRCN
  registration or a verified teaching qualification is allowed with a warning on record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.academic.domain import (
    ARM,
    MAX_ARM_CAPACITY,
    OFFER_KINDS,
    ROOM_KINDS,
    SEMESTERS,
    TERMS,
    TERTIARY_LEVELS,
    check_session_name,
    class_name,
    national_curriculum,
    year,
)
from syntegra.academic.models import (
    AcademicPeriod,
    ClassGroup,
    Curriculum,
    CurriculumAdoption,
    CurriculumEntry,
    CurriculumVersion,
    Offering,
    Room,
    Subject,
    TeachingAssignment,
)
from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.hr import api as hr
from syntegra.institutions.models import Institution, InstitutionLevel
from syntegra.platform.session import PLATFORM_BYPASS

LOAD_SETTING = "academic.max_periods_per_week"
DEFAULT_MAX_PERIODS = 36  # a full-time teacher's weekly contact periods; configurable
STATE_KEY = "state"


def _bypass() -> dict[str, Any]:
    return {PLATFORM_BYPASS: True}


async def _visible(session: AsyncSession, scope_id: uuid.UUID) -> bool:
    """Set at the caller's scope, above it, or below it."""
    ctx = current_context()
    above = await governance.ancestor_ids(session, ctx.scope_node_id) | {ctx.scope_node_id}
    return scope_id in above or await governance.is_within(session, ctx.scope_node_id, scope_id)


async def institution(session: AsyncSession, institution_id: uuid.UUID) -> Institution:
    ctx = current_context()
    inst = (
        await session.execute(
            select(Institution).where(
                Institution.id == institution_id, Institution.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if inst is None:
        raise NotFound("institution not found")
    return inst


async def institution_levels(session: AsyncSession, institution_id: uuid.UUID) -> set[str]:
    rows = await session.execute(
        select(InstitutionLevel.level)
        .where(InstitutionLevel.institution_id == institution_id)
        .execution_options(**_bypass())
    )
    return set(rows.scalars())


# --------------------------------------------------------------------------- calendar


@dataclass(frozen=True, slots=True)
class PeriodView:
    id: uuid.UUID
    kind: str
    name: str
    starts_on: date
    ends_on: date
    status: str
    set_at: uuid.UUID
    parts: list[PeriodView] = field(default_factory=list)


def _pview(p: AcademicPeriod, parts: list[AcademicPeriod] | None = None) -> PeriodView:
    return PeriodView(
        p.id,
        p.kind,
        p.name,
        p.starts_on,
        p.ends_on,
        p.status,
        p.scope_node_id,
        [_pview(x) for x in sorted(parts or [], key=lambda x: x.seq)],
    )


@dataclass(frozen=True, slots=True)
class SessionIn:
    name: str  # 2026/2027
    parts: list[tuple[date, date]]  # three terms (school) or two semesters (tertiary)
    tertiary: bool = False


async def create_session(session: AsyncSession, data: SessionIn) -> PeriodView:
    """Set a session and its terms (or semesters) at the caller's scope."""
    ctx = current_context()
    try:
        check_session_name(data.name)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    names = SEMESTERS if data.tertiary else TERMS
    if len(data.parts) != len(names):
        raise ValidationFailed(
            f"give the dates of all {len(names)} {'semesters' if data.tertiary else 'terms'}"
        )
    problems = []
    for i, (a, b) in enumerate(data.parts):
        if b <= a:
            problems.append(f"{names[i]} ends before it starts")
        if i and a <= data.parts[i - 1][1]:
            problems.append(f"{names[i]} starts before {names[i - 1].lower()} ends")
    if problems:
        raise ValidationFailed("; ".join(problems))
    dup = (
        await session.execute(
            select(AcademicPeriod.id).where(
                AcademicPeriod.scope_node_id == ctx.scope_node_id,
                AcademicPeriod.kind == "session",
                AcademicPeriod.name == data.name.strip(),
            )
        )
    ).first()
    if dup:
        raise Conflict(f"session {data.name} is already set here")
    s = AcademicPeriod(
        tenant_id=ctx.tenant_id,
        scope_node_id=ctx.scope_node_id,
        kind="session",
        name=data.name.strip(),
        seq=1,
        starts_on=data.parts[0][0],
        ends_on=data.parts[-1][1],
        status="planned",
    )
    session.add(s)
    await session.flush()
    parts = []
    for i, (a, b) in enumerate(data.parts):
        p = AcademicPeriod(
            tenant_id=ctx.tenant_id,
            scope_node_id=ctx.scope_node_id,
            kind="semester" if data.tertiary else "term",
            name=names[i],
            parent_id=s.id,
            seq=i + 1,
            starts_on=a,
            ends_on=b,
            status="planned",
        )
        session.add(p)
        parts.append(p)
    await session.flush()
    return _pview(s, parts)


async def _period(session: AsyncSession, period_id: uuid.UUID) -> AcademicPeriod:
    ctx = current_context()
    p = (
        await session.execute(
            select(AcademicPeriod)
            .where(AcademicPeriod.id == period_id, AcademicPeriod.tenant_id == ctx.tenant_id)
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()
    if p is None or not await _visible(session, p.scope_node_id):
        raise NotFound("session or term not found")
    return p


async def _parts(session: AsyncSession, session_id: uuid.UUID) -> list[AcademicPeriod]:
    return list(
        (
            await session.execute(
                select(AcademicPeriod)
                .where(AcademicPeriod.parent_id == session_id)
                .execution_options(**_bypass())
            )
        ).scalars()
    )


async def sessions(session: AsyncSession) -> list[PeriodView]:
    """Sessions the caller works under (set here, above, or below), newest first."""
    ctx = current_context()
    rows = (
        await session.execute(
            select(AcademicPeriod)
            .where(AcademicPeriod.tenant_id == ctx.tenant_id, AcademicPeriod.kind == "session")
            .order_by(AcademicPeriod.name.desc())
            .execution_options(**_bypass())
        )
    ).scalars()
    out = []
    for s in rows:
        if await _visible(session, s.scope_node_id):
            out.append(_pview(s, await _parts(session, s.id)))
    return out


async def get_session(session: AsyncSession, session_id: uuid.UUID) -> PeriodView:
    p = await _period(session, session_id)
    if p.kind != "session":
        raise ValidationFailed("that is a term or semester, not a session")
    return _pview(p, await _parts(session, p.id))


async def set_current(session: AsyncSession, period_id: uuid.UUID) -> PeriodView:
    """Make a session (and the term or semester within it) current; earlier ones close."""
    p = await _period(session, period_id)
    ctx = current_context()
    if p.scope_node_id != ctx.scope_node_id:
        raise Conflict("only the office that set this calendar can change it")
    siblings = (
        await session.execute(
            select(AcademicPeriod).where(
                AcademicPeriod.scope_node_id == p.scope_node_id,
                AcademicPeriod.kind == p.kind,
                AcademicPeriod.parent_id == p.parent_id,
                AcademicPeriod.id != p.id,
                AcademicPeriod.status == "current",
            )
        )
    ).scalars()
    for x in siblings:
        x.status = "closed"
    p.status = "current"
    if p.parent_id:
        parent = await _period(session, p.parent_id)
        parent.status = "current"
    await session.flush()
    top = await _period(session, p.parent_id) if p.parent_id else p
    return _pview(top, await _parts(session, top.id))


# --------------------------------------------------------------------------- rooms


@dataclass(frozen=True, slots=True)
class RoomView:
    id: uuid.UUID
    institution_id: uuid.UUID
    code: str
    name: str
    kind: str
    capacity: int
    building: str | None
    active: bool


def _rview(r: Room) -> RoomView:
    return RoomView(
        r.id, r.institution_id, r.code, r.name, r.kind, r.capacity, r.building, r.active
    )


async def add_room(
    session: AsyncSession,
    institution_id: uuid.UUID,
    *,
    code: str,
    name: str,
    capacity: int,
    kind: str = "classroom",
    building: str | None = None,
) -> RoomView:
    inst = await institution(session, institution_id)
    code = code.strip().upper()
    if not code or not name.strip():
        raise ValidationFailed("a room needs a code and a name")
    if kind not in ROOM_KINDS:
        raise ValidationFailed(f"room kind must be one of {', '.join(ROOM_KINDS)}")
    if not 1 <= capacity <= 2000:
        raise ValidationFailed("capacity must be 1-2000")
    dup = (
        await session.execute(
            select(Room.id).where(Room.institution_id == inst.id, Room.code == code)
        )
    ).first()
    if dup:
        raise Conflict(f"room {code} already exists")
    r = Room(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        institution_id=inst.id,
        code=code,
        name=name.strip(),
        kind=kind,
        capacity=capacity,
        building=(building or "").strip() or None,
    )
    session.add(r)
    await session.flush()
    return _rview(r)


async def rooms(session: AsyncSession, institution_id: uuid.UUID) -> list[RoomView]:
    await institution(session, institution_id)
    rows = (
        await session.execute(
            select(Room).where(Room.institution_id == institution_id).order_by(Room.code)
        )
    ).scalars()
    return [_rview(r) for r in rows]


async def _room(session: AsyncSession, room_id: uuid.UUID) -> Room:
    r = (await session.execute(select(Room).where(Room.id == room_id))).scalar_one_or_none()
    if r is None:
        raise NotFound("room not found")
    return r


# --------------------------------------------------------------------------- subjects, curriculum


@dataclass(frozen=True, slots=True)
class SubjectView:
    id: uuid.UUID
    code: str
    name: str
    group: str | None
    institution_id: uuid.UUID | None
    units: int | None
    year_code: str | None
    semester: int | None
    department: str | None


def _sview(s: Subject) -> SubjectView:
    return SubjectView(
        s.id,
        s.code,
        s.name,
        s.group_name,
        s.institution_id,
        s.units,
        s.year_code,
        s.semester,
        s.department,
    )


async def seed_national_curriculum(
    session: AsyncSession, name: str = "ng-basic-and-senior"
) -> uuid.UUID:
    """Load the national subject list as a published version at the state root (idempotent).
    Run in a system context at the tenant root."""
    ctx = current_context()
    d = national_curriculum(name)
    existing = (
        await session.execute(
            select(Curriculum).where(
                Curriculum.tenant_id == ctx.tenant_id, Curriculum.code == d.code
            )
        )
    ).scalar_one_or_none()
    if existing:
        first = (
            (
                await session.execute(
                    select(CurriculumVersion.id).where(
                        CurriculumVersion.curriculum_id == existing.id
                    )
                )
            )
            .scalars()
            .first()
        )
        assert first is not None
        return first
    have = {
        s.code: s
        for s in (
            await session.execute(
                select(Subject).where(
                    Subject.tenant_id == ctx.tenant_id, Subject.institution_key == STATE_KEY
                )
            )
        ).scalars()
    }
    for sd in d.subjects:
        if sd.code not in have:
            s = Subject(
                tenant_id=ctx.tenant_id,
                scope_node_id=ctx.scope_node_id,
                code=sd.code,
                name=sd.name,
                group_name=sd.group,
                institution_key=STATE_KEY,
            )
            session.add(s)
            have[sd.code] = s
    c = Curriculum(
        tenant_id=ctx.tenant_id,
        scope_node_id=ctx.scope_node_id,
        code=d.code,
        name=d.name,
        authority=d.authority,
    )
    session.add(c)
    await session.flush()
    v = CurriculumVersion(
        tenant_id=ctx.tenant_id,
        scope_node_id=ctx.scope_node_id,
        curriculum_id=c.id,
        version=1,
        status="published",
        note=d.note,
        published_at=utcnow(),
    )
    session.add(v)
    await session.flush()
    for e in d.entries:
        session.add(
            CurriculumEntry(
                tenant_id=ctx.tenant_id,
                scope_node_id=ctx.scope_node_id,
                version_id=v.id,
                year_code=e.year_code,
                subject_id=have[e.subject_code].id,
                kind=e.kind,
                periods_per_week=e.periods_per_week,
            )
        )
    await session.flush()
    return v.id


@dataclass(frozen=True, slots=True)
class CurriculumView:
    version_id: uuid.UUID
    code: str
    name: str
    authority: str
    version: int
    status: str
    note: str | None
    adoptions: list[dict[str, Any]]


async def curricula(session: AsyncSession) -> list[CurriculumView]:
    ctx = current_context()
    rows = (
        await session.execute(
            select(CurriculumVersion, Curriculum)
            .join(Curriculum, Curriculum.id == CurriculumVersion.curriculum_id)
            .where(Curriculum.tenant_id == ctx.tenant_id)
            .order_by(Curriculum.code, CurriculumVersion.version)
            .execution_options(**_bypass())
        )
    ).all()
    out = []
    for v, c in rows:
        adopted = (
            await session.execute(
                select(CurriculumAdoption)
                .where(CurriculumAdoption.version_id == v.id)
                .execution_options(**_bypass())
            )
        ).scalars()
        out.append(
            CurriculumView(
                v.id,
                c.code,
                c.name,
                c.authority,
                v.version,
                v.status,
                v.note,
                [
                    {
                        "scope_id": str(a.scope_node_id),
                        "scope_name": (
                            await governance.get_scope_node(session, a.scope_node_id)
                        ).name,
                        "years": a.years,
                        "from_session": a.from_session,
                    }
                    for a in adopted
                ],
            )
        )
    return out


async def _version(session: AsyncSession, version_id: uuid.UUID) -> CurriculumVersion:
    ctx = current_context()
    v = (
        await session.execute(
            select(CurriculumVersion)
            .where(CurriculumVersion.id == version_id, CurriculumVersion.tenant_id == ctx.tenant_id)
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()
    if v is None:
        raise NotFound("curriculum version not found")
    return v


async def adopt(
    session: AsyncSession, version_id: uuid.UUID, *, years: list[str] | None, from_session: str
) -> CurriculumView:
    """Adopt a published version at the caller's scope (the state, a board) for some years."""
    ctx = current_context()
    v = await _version(session, version_id)
    if v.status != "published":
        raise Conflict("only a published curriculum version can be adopted")
    try:
        check_session_name(from_session)
        codes = sorted({year(y).code for y in years}) if years else ["*"]
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    session.add(
        CurriculumAdoption(
            tenant_id=ctx.tenant_id,
            scope_node_id=ctx.scope_node_id,
            version_id=v.id,
            years=",".join(codes),
            from_session=from_session.strip(),
            adopted_by=ctx.user_id,
        )
    )
    await session.flush()
    return next(c for c in await curricula(session) if c.version_id == v.id)


async def resolve_curriculum(
    session: AsyncSession, scope_id: uuid.UUID, year_code: str, session_name: str
) -> CurriculumVersion | None:
    """The version that applies to a year at a school in a session: the nearest adoption above
    the school that covers the year and has started (CEDA §4: exactly one)."""
    ctx = current_context()
    chain = await governance.ancestor_ids(session, scope_id) | {scope_id}
    rows = (
        await session.execute(
            select(CurriculumAdoption)
            .where(
                CurriculumAdoption.tenant_id == ctx.tenant_id,
                CurriculumAdoption.scope_node_id.in_(chain),
                CurriculumAdoption.from_session <= session_name,
            )
            .execution_options(**_bypass())
        )
    ).scalars()
    best: tuple[int, str, CurriculumAdoption] | None = None
    for a in rows:
        if a.years != "*" and year_code not in a.years.split(","):
            continue
        depth = len(await governance.ancestor_ids(session, a.scope_node_id))
        key = (depth, a.from_session)
        if best is None or key > (best[0], best[1]):
            best = (depth, a.from_session, a)
    return await _version(session, best[2].version_id) if best else None


@dataclass(frozen=True, slots=True)
class EntryView:
    subject: SubjectView
    kind: str
    periods_per_week: int | None


async def entries_for(
    session: AsyncSession, version_id: uuid.UUID, year_code: str
) -> list[EntryView]:
    rows = (
        await session.execute(
            select(CurriculumEntry, Subject)
            .join(Subject, Subject.id == CurriculumEntry.subject_id)
            .where(CurriculumEntry.version_id == version_id, CurriculumEntry.year_code == year_code)
            .order_by(CurriculumEntry.kind, Subject.name)
            .execution_options(**_bypass())
        )
    ).all()
    return [EntryView(_sview(s), e.kind, e.periods_per_week) for e, s in rows]


async def subjects(
    session: AsyncSession, institution_id: uuid.UUID | None = None
) -> list[SubjectView]:
    """State subjects, plus an institution's own subjects and courses."""
    ctx = current_context()
    keys = [STATE_KEY] + ([str(institution_id)] if institution_id else [])
    rows = (
        await session.execute(
            select(Subject)
            .where(
                Subject.tenant_id == ctx.tenant_id,
                Subject.institution_key.in_(keys),
                Subject.active.is_(True),
            )
            .order_by(Subject.code)
            .execution_options(**_bypass())
        )
    ).scalars()
    return [_sview(s) for s in rows]


@dataclass(frozen=True, slots=True)
class CourseIn:
    code: str  # CSC 101
    name: str
    units: int | None = None
    year_code: str | None = None  # 100L
    semester: int | None = None
    department: str | None = None
    group: str | None = None


async def add_subject(
    session: AsyncSession, institution_id: uuid.UUID, data: CourseIn
) -> SubjectView:
    """An institution's own subject (a school), or a course (tertiary): code, units, level,
    semester."""
    inst = await institution(session, institution_id)
    code = " ".join(data.code.upper().split())
    if not code or len(code) > 16 or not data.name.strip():
        raise ValidationFailed("a subject needs a code (at most 16 characters) and a name")
    if data.units is not None and not 0 <= data.units <= 30:
        raise ValidationFailed("units must be 0-30")
    if data.semester not in (None, 1, 2):
        raise ValidationFailed("semester is 1 or 2")
    if data.year_code:
        try:
            year(data.year_code)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
    dup = (
        await session.execute(
            select(Subject.id)
            .where(
                Subject.tenant_id == inst.tenant_id,
                Subject.institution_key.in_([STATE_KEY, str(inst.id)]),
                Subject.code == code,
            )
            .execution_options(**_bypass())
        )
    ).first()
    if dup:
        raise Conflict(f"subject code {code} is already used")
    s = Subject(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        code=code,
        name=data.name.strip(),
        group_name=(data.group or "").strip() or None,
        institution_id=inst.id,
        institution_key=str(inst.id),
        units=data.units,
        year_code=year(data.year_code).code if data.year_code else None,
        semester=data.semester,
        department=(data.department or "").strip() or None,
    )
    session.add(s)
    await session.flush()
    return _sview(s)


async def _subject(session: AsyncSession, subject_id: uuid.UUID) -> Subject:
    ctx = current_context()
    s = (
        await session.execute(
            select(Subject)
            .where(Subject.id == subject_id, Subject.tenant_id == ctx.tenant_id)
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()
    if s is None:
        raise NotFound("subject not found")
    return s


# --------------------------------------------------------------------------- classes


@dataclass(frozen=True, slots=True)
class ClassView:
    id: uuid.UUID
    institution_id: uuid.UUID
    session_id: uuid.UUID
    session_name: str
    year_code: str
    year_name: str
    level: str
    arm: str
    name: str
    capacity: int
    room: RoomView | None
    class_teacher_staff_id: uuid.UUID | None
    class_teacher_name: str | None
    assistant_staff_id: uuid.UUID | None
    curriculum_version_id: uuid.UUID | None
    status: str


async def _staff_name(session: AsyncSession, staff_id: uuid.UUID | None) -> str | None:
    if staff_id is None:
        return None
    try:
        v = await hr.get_staff(session, staff_id)
    except NotFound:
        return None
    return f"{v.first_name} {v.surname}"


async def class_view(session: AsyncSession, c: ClassGroup) -> ClassView:
    y = year(c.year_code)
    p = await _period(session, c.session_id)
    return ClassView(
        c.id,
        c.institution_id,
        c.session_id,
        p.name,
        y.code,
        y.name,
        y.level,
        c.arm,
        c.name,
        c.capacity,
        _rview(await _room(session, c.room_id)) if c.room_id else None,
        c.class_teacher_staff_id,
        await _staff_name(session, c.class_teacher_staff_id),
        c.assistant_staff_id,
        c.curriculum_version_id,
        c.status,
    )


async def _check_staff(
    session: AsyncSession, staff_id: uuid.UUID, institution_id: uuid.UUID, *, what: str
) -> tuple[hr.StaffView, list[str]]:
    """Active teaching staff posted to the institution; warnings for TRCN and qualifications."""
    s = await hr.get_staff(session, staff_id)
    if s.status != "active":
        raise Conflict(f"{s.first_name} {s.surname} is {s.status.replace('_', ' ')}, not active")
    if s.category != "teaching":
        raise Conflict(f"only teaching staff can be {what}")
    post = s.current_post
    if post is None or post.institution_id != institution_id:
        raise Conflict(
            f"{s.first_name} {s.surname} is not posted to this institution",
            next_action="Post the teacher here first (Staff 360 → postings).",
        )
    warnings = []
    if not s.registered_teacher:
        warnings.append("no current TRCN registration on record")
    if not any(q.teaching and q.verified for q in s.qualifications):
        warnings.append("no verified teaching qualification on record")
    return s, warnings


@dataclass(frozen=True, slots=True)
class ClassIn:
    session_id: uuid.UUID
    year_code: str
    arm: str
    capacity: int = 40
    room_id: uuid.UUID | None = None
    class_teacher_staff_id: uuid.UUID | None = None


async def create_class(
    session: AsyncSession, institution_id: uuid.UUID, data: ClassIn
) -> ClassView:
    """Open a class (one arm of a year) and lay out its subjects from the adopted curriculum."""
    inst = await institution(session, institution_id)
    sess = await _period(session, data.session_id)
    if sess.kind != "session":
        raise ValidationFailed("a class belongs to a session, not a term")
    try:
        y = year(data.year_code)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    levels = await institution_levels(session, inst.id)
    if y.level not in levels:
        raise ValidationFailed(f"{inst.name} is not registered for {y.level.upper()} ({y.name})")
    arm = data.arm.strip()
    if not ARM.match(arm):
        raise ValidationFailed("an arm is a letter, number or short word (A, B, Gold)")
    if not 1 <= data.capacity <= MAX_ARM_CAPACITY:
        raise ValidationFailed(f"capacity must be 1-{MAX_ARM_CAPACITY}")
    dup = (
        await session.execute(
            select(ClassGroup.id).where(
                ClassGroup.institution_id == inst.id,
                ClassGroup.session_id == sess.id,
                ClassGroup.year_code == y.code,
                ClassGroup.arm == arm,
            )
        )
    ).first()
    if dup:
        raise Conflict(f"{class_name(y, arm)} already exists for {sess.name}")
    if data.room_id:
        r = await _room(session, data.room_id)
        if r.institution_id != inst.id:
            raise ValidationFailed("the room belongs to another institution")
        if r.capacity < data.capacity:
            raise ValidationFailed(
                f"room {r.code} seats {r.capacity}, fewer than the class capacity"
            )
    if data.class_teacher_staff_id:
        await _check_staff(session, data.class_teacher_staff_id, inst.id, what="class teachers")
    version = await resolve_curriculum(session, inst.scope_node_id, y.code, sess.name)
    c = ClassGroup(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        institution_id=inst.id,
        session_id=sess.id,
        year_code=y.code,
        arm=arm,
        name=class_name(y, arm),
        capacity=data.capacity,
        room_id=data.room_id,
        class_teacher_staff_id=data.class_teacher_staff_id,
        curriculum_version_id=version.id if version else None,
        status="active",
    )
    session.add(c)
    await session.flush()
    if version:
        for e in await entries_for(session, version.id, y.code):
            session.add(
                Offering(
                    tenant_id=inst.tenant_id,
                    scope_node_id=inst.scope_node_id,
                    institution_id=inst.id,
                    period_id=sess.id,
                    subject_id=e.subject.id,
                    class_group_id=c.id,
                    class_key=str(c.id),
                    year_code=y.code,
                    kind=e.kind,
                    periods_per_week=e.periods_per_week,
                )
            )
        await session.flush()
    return await class_view(session, c)


async def get_class(session: AsyncSession, class_id: uuid.UUID) -> ClassGroup:
    c = (
        await session.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    ).scalar_one_or_none()
    if c is None:
        raise NotFound("class not found")
    return c


async def classes(
    session: AsyncSession, institution_id: uuid.UUID, session_id: uuid.UUID | None = None
) -> list[ClassView]:
    await institution(session, institution_id)
    q = select(ClassGroup).where(ClassGroup.institution_id == institution_id)
    if session_id:
        q = q.where(ClassGroup.session_id == session_id)
    rows = (await session.execute(q)).scalars().all()
    views = [await class_view(session, c) for c in rows]
    return sorted(views, key=lambda v: (v.session_name, v.level, year(v.year_code).order, v.arm))


@dataclass(frozen=True, slots=True)
class ClassChange:
    capacity: int | None = None
    room_id: uuid.UUID | None = None
    clear_room: bool = False
    class_teacher_staff_id: uuid.UUID | None = None
    assistant_staff_id: uuid.UUID | None = None


async def change_class(
    session: AsyncSession, class_id: uuid.UUID, data: ClassChange, *, members: int = 0
) -> tuple[ClassView, list[str]]:
    """Change the capacity, room, class teacher or assistant. ``members`` is the current size."""
    c = await get_class(session, class_id)
    if c.status != "active":
        raise Conflict("this class is closed")
    warnings: list[str] = []
    if data.capacity is not None:
        if not 1 <= data.capacity <= MAX_ARM_CAPACITY:
            raise ValidationFailed(f"capacity must be 1-{MAX_ARM_CAPACITY}")
        if data.capacity < members:
            raise Conflict(f"the class already has {members} learners")
        c.capacity = data.capacity
    if data.clear_room:
        c.room_id = None
    elif data.room_id:
        r = await _room(session, data.room_id)
        if r.institution_id != c.institution_id:
            raise ValidationFailed("the room belongs to another institution")
        if r.capacity < c.capacity:
            raise ValidationFailed(
                f"room {r.code} seats {r.capacity}, fewer than the class capacity"
            )
        c.room_id = r.id
    if data.class_teacher_staff_id:
        _, warnings = await _check_staff(
            session, data.class_teacher_staff_id, c.institution_id, what="class teachers"
        )
        c.class_teacher_staff_id = data.class_teacher_staff_id
    if data.assistant_staff_id:
        await _check_staff(
            session, data.assistant_staff_id, c.institution_id, what="assistant class teachers"
        )
        c.assistant_staff_id = data.assistant_staff_id
    await session.flush()
    return await class_view(session, c), warnings


async def close_class(session: AsyncSession, class_id: uuid.UUID) -> ClassView:
    c = await get_class(session, class_id)
    c.status = "closed"
    await session.flush()
    return await class_view(session, c)


# --------------------------------------------------------------------------- offerings and teaching


@dataclass(frozen=True, slots=True)
class AssignmentView:
    id: uuid.UUID
    staff_id: uuid.UUID
    staff_name: str | None
    role: str
    valid_from: date
    valid_to: date | None
    note: str | None


@dataclass(frozen=True, slots=True)
class OfferingView:
    id: uuid.UUID
    institution_id: uuid.UUID
    period_id: uuid.UUID
    class_group_id: uuid.UUID | None
    year_code: str
    subject: SubjectView
    kind: str
    periods_per_week: int | None
    units: int | None
    capacity: int | None
    active: bool
    teachers: list[AssignmentView]


async def _current_assignments(
    session: AsyncSession, offering_id: uuid.UUID
) -> list[TeachingAssignment]:
    d = today()
    rows = (
        await session.execute(
            select(TeachingAssignment).where(TeachingAssignment.offering_id == offering_id)
        )
    ).scalars()
    return [a for a in rows if a.valid_from <= d and (a.valid_to is None or a.valid_to >= d)]


async def _oview(session: AsyncSession, o: Offering) -> OfferingView:
    s = await _subject(session, o.subject_id)
    teachers = [
        AssignmentView(
            a.id,
            a.staff_id,
            await _staff_name(session, a.staff_id),
            a.role,
            a.valid_from,
            a.valid_to,
            a.note,
        )
        for a in sorted(await _current_assignments(session, o.id), key=lambda a: a.role != "lead")
    ]
    return OfferingView(
        o.id,
        o.institution_id,
        o.period_id,
        o.class_group_id,
        o.year_code,
        _sview(s),
        o.kind,
        o.periods_per_week,
        o.units,
        o.capacity,
        o.active,
        teachers,
    )


async def class_offerings(session: AsyncSession, class_id: uuid.UUID) -> list[OfferingView]:
    c = await get_class(session, class_id)
    rows = (
        await session.execute(
            select(Offering).where(Offering.class_group_id == c.id, Offering.active.is_(True))
        )
    ).scalars()
    views = [await _oview(session, o) for o in rows]
    return sorted(views, key=lambda v: (OFFER_KINDS.index(v.kind), v.subject.name))


async def add_class_subject(
    session: AsyncSession,
    class_id: uuid.UUID,
    subject_id: uuid.UUID,
    *,
    kind: str = "core",
    periods_per_week: int | None = None,
) -> OfferingView:
    """A subject the curriculum does not list for this class: a local subject, an extra
    elective."""
    c = await get_class(session, class_id)
    s = await _subject(session, subject_id)
    if s.institution_id not in (None, c.institution_id):
        raise ValidationFailed("that subject belongs to another institution")
    if kind not in OFFER_KINDS:
        raise ValidationFailed(f"kind must be one of {', '.join(OFFER_KINDS)}")
    o = (
        await session.execute(
            select(Offering).where(
                Offering.period_id == c.session_id,
                Offering.subject_id == s.id,
                Offering.class_key == str(c.id),
            )
        )
    ).scalar_one_or_none()
    if o and o.active:
        raise Conflict(f"{s.name} is already taught in {c.name}")
    if o:
        o.active, o.kind, o.periods_per_week = True, kind, periods_per_week
    else:
        o = Offering(
            tenant_id=c.tenant_id,
            scope_node_id=c.scope_node_id,
            institution_id=c.institution_id,
            period_id=c.session_id,
            subject_id=s.id,
            class_group_id=c.id,
            class_key=str(c.id),
            year_code=c.year_code,
            kind=kind,
            periods_per_week=periods_per_week,
        )
        session.add(o)
    await session.flush()
    return await _oview(session, o)


async def _offering(session: AsyncSession, offering_id: uuid.UUID) -> Offering:
    o = (
        await session.execute(select(Offering).where(Offering.id == offering_id))
    ).scalar_one_or_none()
    if o is None:
        raise NotFound("subject or course offering not found")
    return o


async def get_offering(session: AsyncSession, offering_id: uuid.UUID) -> OfferingView:
    return await _oview(session, await _offering(session, offering_id))


async def drop_offering(session: AsyncSession, offering_id: uuid.UUID) -> OfferingView:
    o = await _offering(session, offering_id)
    o.active = False
    for a in await _current_assignments(session, o.id):
        await _stop(session, a, today() + timedelta(days=1))
    await session.flush()
    return await _oview(session, o)


async def offer_course(
    session: AsyncSession,
    semester_id: uuid.UUID,
    subject_id: uuid.UUID,
    *,
    capacity: int | None = None,
) -> OfferingView:
    """Run a tertiary course in a semester (its lecturers are assigned next)."""
    sem = await _period(session, semester_id)
    if sem.kind != "semester":
        raise ValidationFailed("courses run in a semester")
    s = await _subject(session, subject_id)
    if s.institution_id is None:
        raise ValidationFailed("only an institution's own courses run in a semester")
    inst = await institution(session, s.institution_id)
    if not (await institution_levels(session, inst.id)) & TERTIARY_LEVELS:
        raise ValidationFailed("courses and semesters are for tertiary institutions")
    if s.semester and s.semester != sem.seq:
        raise ValidationFailed(f"{s.code} is a semester {s.semester} course")
    dup = (
        await session.execute(
            select(Offering.id).where(
                Offering.period_id == sem.id,
                Offering.subject_id == s.id,
                Offering.class_key == (s.year_code or "-"),
            )
        )
    ).first()
    if dup:
        raise Conflict(f"{s.code} already runs this semester")
    o = Offering(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        institution_id=inst.id,
        period_id=sem.id,
        subject_id=s.id,
        class_key=s.year_code or "-",
        year_code=s.year_code or "-",
        kind="core",
        units=s.units,
        capacity=capacity,
    )
    session.add(o)
    await session.flush()
    return await _oview(session, o)


async def course_offerings(
    session: AsyncSession, institution_id: uuid.UUID, semester_id: uuid.UUID
) -> list[OfferingView]:
    await institution(session, institution_id)
    rows = (
        await session.execute(
            select(Offering).where(
                Offering.institution_id == institution_id,
                Offering.period_id == semester_id,
                Offering.active.is_(True),
            )
        )
    ).scalars()
    views = [await _oview(session, o) for o in rows]
    return sorted(views, key=lambda v: v.subject.code)


async def _max_periods(session: AsyncSession, scope_id: uuid.UUID) -> int:
    v = await governance.resolve_config(session, scope_id, LOAD_SETTING)
    return int(v) if isinstance(v, int) and v > 0 else DEFAULT_MAX_PERIODS


async def teaching_load(
    session: AsyncSession, staff_id: uuid.UUID
) -> tuple[int, list[OfferingView]]:
    """Periods per week across the staff member's current lead assignments, and the offerings."""
    d = today()
    rows = (
        await session.execute(
            select(TeachingAssignment, Offering)
            .join(Offering, Offering.id == TeachingAssignment.offering_id)
            .where(TeachingAssignment.staff_id == staff_id, Offering.active.is_(True))
        )
    ).all()
    current = [
        (a, o) for a, o in rows if a.valid_from <= d and (a.valid_to is None or a.valid_to >= d)
    ]
    total = sum((o.periods_per_week or 0) for a, o in current if a.role == "lead")
    return total, [await _oview(session, o) for _, o in current]


@dataclass(frozen=True, slots=True)
class Assigned:
    offering: OfferingView
    warnings: list[str]


async def assign_teacher(
    session: AsyncSession,
    offering_id: uuid.UUID,
    staff_id: uuid.UUID,
    *,
    role: str = "lead",
    starts_on: date | None = None,
    note: str | None = None,
) -> Assigned:
    """Assign the teacher of a subject in a class, or a lecturer of a course. A new lead replaces
    the current one from the start date."""
    o = await _offering(session, offering_id)
    if not o.active:
        raise Conflict("this subject is no longer offered")
    if role not in ("lead", "assistant"):
        raise ValidationFailed("role is lead or assistant")
    _, warnings = await _check_staff(session, staff_id, o.institution_id, what="assigned to teach")
    start = starts_on or today()
    current = await _current_assignments(session, o.id)
    mine = [a for a in current if a.staff_id == staff_id]
    if any(a.role == role for a in mine):
        raise Conflict("this person already teaches it")
    if role == "lead":
        load, _ = await teaching_load(session, staff_id)
        limit = await _max_periods(session, o.scope_node_id)
        if load + (o.periods_per_week or 0) > limit:
            raise Conflict(
                f"that would make {load + (o.periods_per_week or 0)} periods a week; "
                f"the limit is {limit}",
                next_action="Give the subject to another teacher, or reduce their load first.",
            )
        for a in current:
            if a.role == "lead":
                await _stop(session, a, start)
    for a in mine:  # an assistant becoming the lead, or the other way round
        await _stop(session, a, start)
    text = "; ".join(x for x in [(note or "").strip(), *warnings] if x)
    session.add(
        TeachingAssignment(
            tenant_id=o.tenant_id,
            scope_node_id=o.scope_node_id,
            offering_id=o.id,
            staff_id=staff_id,
            role=role,
            valid_from=start,
            note=text[:255] or None,
        )
    )
    await session.flush()
    return Assigned(await _oview(session, o), warnings)


async def _stop(session: AsyncSession, a: TeachingAssignment, start: date) -> None:
    """End an assignment the day before ``start``; one that would never have taken effect is
    removed (the audit trail keeps it)."""
    if a.valid_from >= start:
        await session.delete(a)
    else:
        a.valid_to = start - timedelta(days=1)


async def end_assignment(session: AsyncSession, assignment_id: uuid.UUID) -> OfferingView:
    a = (
        await session.execute(
            select(TeachingAssignment).where(TeachingAssignment.id == assignment_id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise NotFound("assignment not found")
    await _stop(session, a, today() + timedelta(days=1))
    await session.flush()
    return await _oview(session, await _offering(session, a.offering_id))


async def staff_of_person(session: AsyncSession, person_id: uuid.UUID) -> list[uuid.UUID]:
    """The staff records of a person (to find a signed-in teacher's classes)."""
    from syntegra.hr.models import Staff

    rows = await session.execute(
        select(Staff.id).where(Staff.person_id == person_id).execution_options(**_bypass())
    )
    return list(rows.scalars())


async def classes_taught(session: AsyncSession, staff_ids: list[uuid.UUID]) -> list[ClassGroup]:
    """Classes where these staff are the class teacher or teach a subject now."""
    if not staff_ids:
        return []
    d = today()
    by_subject = (
        await session.execute(
            select(Offering.class_group_id)
            .join(TeachingAssignment, TeachingAssignment.offering_id == Offering.id)
            .where(
                TeachingAssignment.staff_id.in_(staff_ids),
                TeachingAssignment.valid_from <= d,
                (TeachingAssignment.valid_to.is_(None)) | (TeachingAssignment.valid_to >= d),
                Offering.class_group_id.is_not(None),
            )
            .execution_options(**_bypass())
        )
    ).scalars()
    ids = set(by_subject)
    rows = (
        await session.execute(
            select(ClassGroup)
            .where(
                (ClassGroup.class_teacher_staff_id.in_(staff_ids))
                | (ClassGroup.assistant_staff_id.in_(staff_ids))
                | (ClassGroup.id.in_(ids or [uuid.UUID(int=0)])),
                ClassGroup.status == "active",
            )
            .execution_options(**_bypass())
        )
    ).scalars()
    return list(rows)
