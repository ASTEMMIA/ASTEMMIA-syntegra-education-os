"""Attendance (CEDA §5, ADR-032): learner registers, twice a day, and staff attendance.

* **School days** are weekdays inside a term of the class's session, except closures: a public
  holiday set by the State (or a board) covers every school beneath it; a school can close itself.
* **Registers** are taken for a whole class, morning (``am``) and afternoon (``pm``), every learner
  marked present, late, absent or excused (excused needs a reason). A class teacher can take a
  missed register up to ``LATE_DAYS`` days late; after the day, changing a mark needs
  ``attendance:amend`` and a reason, kept as an evidential correction.
* **Staff** sign themselves in and out, or the head records the day (present, late, absent, leave,
  sick, official duty). Signing in after the late time (08:00 unless configured) is late.
* **Figures:** times the school opened (registers taken), present (present + late), absent,
  excused, and the attendance rate; below 90% over at least ten registers is persistent absence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.academic.models import AcademicPeriod, ClassGroup
from syntegra.attendance.models import (
    LEARNER_STATUSES,
    STAFF_STATUSES,
    AttendanceCorrection,
    LearnerMark,
    Register,
    SchoolClosure,
    StaffAttendance,
)
from syntegra.core.clock import utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.hr.models import Staff, StaffPost
from syntegra.institutions.models import Institution
from syntegra.learners.models import ClassMembership, Learner
from syntegra.mdm.models import Person
from syntegra.platform.session import PLATFORM_BYPASS

BYPASS: dict[str, Any] = {PLATFORM_BYPASS: True}
LOCAL = ZoneInfo("Africa/Lagos")  # West Africa Time, UTC+1, no daylight saving
LATE_DAYS = 7
PERSISTENT_BELOW = 0.9
PERSISTENT_MIN_REGISTERS = 10
LATE_SETTING = "attendance.staff_late_after"
DEFAULT_LATE_AFTER = time(8, 0)


def local_now() -> datetime:
    return datetime.now(LOCAL)


# --------------------------------------------------------------------------- school days


async def _institution(session: AsyncSession, institution_id: uuid.UUID) -> Institution:
    inst = (
        await session.execute(select(Institution).where(Institution.id == institution_id))
    ).scalar_one_or_none()
    if inst is None:
        raise NotFound("institution not found in your scope")
    return inst


async def _class(session: AsyncSession, class_id: uuid.UUID) -> ClassGroup:
    c = (
        await session.execute(select(ClassGroup).where(ClassGroup.id == class_id))
    ).scalar_one_or_none()
    if c is None:
        raise NotFound("class not found in your scope")
    return c


async def term_on(session: AsyncSession, session_id: uuid.UUID, on: date) -> AcademicPeriod | None:
    """The term (or semester) of this session that contains the date."""
    return (
        await session.execute(
            select(AcademicPeriod)
            .where(
                AcademicPeriod.parent_id == session_id,
                AcademicPeriod.starts_on <= on,
                AcademicPeriod.ends_on >= on,
            )
            .execution_options(**BYPASS)
        )
    ).scalar_one_or_none()


async def closure_on(session: AsyncSession, scope_id: uuid.UUID, on: date) -> str | None:
    """Why there is no school at this scope on this day, if a closure covers it."""
    scopes = await governance.ancestor_ids(session, scope_id) | {scope_id}
    row = (
        await session.execute(
            select(SchoolClosure.reason)
            .where(SchoolClosure.on_date == on, SchoolClosure.scope_node_id.in_(scopes))
            .execution_options(**BYPASS)
        )
    ).first()
    return row[0] if row else None


async def not_a_school_day(
    session: AsyncSession, c: ClassGroup, on: date
) -> tuple[str | None, AcademicPeriod | None]:
    """(why not, the term): why there is no school for this class on this day, else None."""
    if on.weekday() >= 5:
        return "it is a weekend", None
    term = await term_on(session, c.session_id, on)
    if term is None:
        return "it is not in a term of the class's session", None
    why = await closure_on(session, c.scope_node_id, on)
    if why:
        return f"the school is closed ({why})", term
    return None, term


@dataclass(frozen=True, slots=True)
class ClosureView:
    id: uuid.UUID
    on_date: date
    reason: str
    scope_node_id: uuid.UUID


async def add_closure(session: AsyncSession, on: date, reason: str) -> ClosureView:
    """Close every school beneath the caller's scope for a day (a public holiday, a strike)."""
    ctx = current_context()
    why = " ".join(reason.split())
    if not 3 <= len(why) <= 120:
        raise ValidationFailed("say why there is no school (3-120 characters)")
    if (
        await session.execute(
            select(SchoolClosure.id).where(
                SchoolClosure.scope_node_id == ctx.scope_node_id, SchoolClosure.on_date == on
            )
        )
    ).first():
        raise Conflict(f"{on:%d %b %Y} is already a closure here")
    row = SchoolClosure(
        tenant_id=ctx.tenant_id, scope_node_id=ctx.scope_node_id, on_date=on, reason=why
    )
    session.add(row)
    await session.flush()
    return ClosureView(row.id, row.on_date, row.reason, row.scope_node_id)


async def closures(session: AsyncSession, start: date, end: date) -> list[ClosureView]:
    """Closures that apply at the caller's scope: its own and those set above it."""
    ctx = current_context()
    scopes = await governance.ancestor_ids(session, ctx.scope_node_id) | {ctx.scope_node_id}
    rows = (
        await session.execute(
            select(SchoolClosure)
            .where(
                SchoolClosure.scope_node_id.in_(scopes),
                SchoolClosure.on_date >= start,
                SchoolClosure.on_date <= end,
            )
            .order_by(SchoolClosure.on_date)
            .execution_options(**BYPASS)
        )
    ).scalars()
    return [ClosureView(r.id, r.on_date, r.reason, r.scope_node_id) for r in rows]


# --------------------------------------------------------------------------- registers


async def members_on(
    session: AsyncSession, class_id: uuid.UUID, on: date
) -> list[tuple[uuid.UUID, uuid.UUID, Person]]:
    """(learner, enrolment, person) of everyone in the class on this day, by surname."""
    rows = await session.execute(
        select(ClassMembership.learner_id, ClassMembership.enrolment_id, Person)
        .join(Learner, Learner.id == ClassMembership.learner_id)
        .join(Person, Person.id == Learner.person_id)
        .where(
            ClassMembership.class_group_id == class_id,
            ClassMembership.valid_from <= on,
            (ClassMembership.valid_to.is_(None)) | (ClassMembership.valid_to >= on),
        )
        .order_by(Person.family_name, Person.given_name)
        .execution_options(**BYPASS)
    )
    return [(lid, eid, p) for lid, eid, p in rows.all()]


@dataclass(frozen=True, slots=True)
class MarkView:
    learner_id: uuid.UUID
    name: str
    sex: str
    status: str | None
    reason: str | None
    demo: bool


@dataclass(frozen=True, slots=True)
class RegisterView:
    class_id: uuid.UUID
    class_name: str
    on_date: date
    session: str
    taken: bool
    taken_at: datetime | None
    closed_reason: str | None
    marks: list[MarkView]
    counts: dict[str, int]


def _name(p: Person) -> str:
    return f"{p.family_name}, {p.given_name}" + (f" {p.other_names}" if p.other_names else "")


async def get_register(
    session: AsyncSession, class_id: uuid.UUID, on: date, sess: str
) -> RegisterView:
    """The register for a class and session: the marks if taken, the class list if not."""
    if sess not in ("am", "pm"):
        raise ValidationFailed("session is am or pm")
    c = await _class(session, class_id)
    why, _ = await not_a_school_day(session, c, on)
    reg = (
        await session.execute(
            select(Register)
            .where(
                Register.class_group_id == c.id, Register.on_date == on, Register.session == sess
            )
            .execution_options(**BYPASS)
        )
    ).scalar_one_or_none()
    marks: dict[uuid.UUID, LearnerMark] = {}
    if reg:
        for m in (
            await session.execute(
                select(LearnerMark)
                .where(LearnerMark.register_id == reg.id)
                .execution_options(**BYPASS)
            )
        ).scalars():
            marks[m.learner_id] = m
    from syntegra.mdm.api import is_demo

    views = []
    for lid, _eid, p in await members_on(session, c.id, on):
        mk = marks.get(lid)
        views.append(
            MarkView(
                lid,
                _name(p),
                p.sex_at_registration,
                mk.status if mk else None,
                mk.reason if mk else None,
                is_demo(p.source_system),
            )
        )
    counts = {s: sum(1 for v in views if v.status == s) for s in LEARNER_STATUSES}
    return RegisterView(
        c.id, c.name, on, sess, reg is not None, reg.taken_at if reg else None, why, views, counts
    )


@dataclass(frozen=True, slots=True)
class MarkIn:
    learner_id: uuid.UUID
    status: str
    reason: str | None = None


async def take_register(
    session: AsyncSession,
    class_id: uuid.UUID,
    on: date,
    sess: str,
    marks: list[MarkIn],
    *,
    may_amend: bool = False,
    amend_reason: str | None = None,
) -> RegisterView:
    """Take (or retake on the day) a class's register. Every learner in the class is marked."""
    ctx = current_context()
    if sess not in ("am", "pm"):
        raise ValidationFailed("session is am or pm")
    now = local_now().date()
    if on > now:
        raise ValidationFailed("a register cannot be taken for a day that has not come")
    c = await _class(session, class_id)
    why, term = await not_a_school_day(session, c, on)
    if why or term is None:
        raise ValidationFailed(f"no register on {on:%a %d %b %Y}: {why}")
    members = await members_on(session, c.id, on)
    if not members:
        raise ValidationFailed("the class has no learners on that day")
    given = {m.learner_id: m for m in marks}
    unknown = set(given) - {lid for lid, _, _ in members}
    if unknown:
        raise ValidationFailed(f"{len(unknown)} of the learners marked are not in this class")
    missing = [_name(p) for lid, _, p in members if lid not in given]
    if missing:
        raise ValidationFailed(
            f"mark every learner: {len(missing)} not marked ({', '.join(missing[:5])}"
            + ("…" if len(missing) > 5 else "")
            + ")"
        )
    for mi in marks:
        if mi.status not in LEARNER_STATUSES:
            raise ValidationFailed(f"status is one of {', '.join(LEARNER_STATUSES)}")
        if mi.status == "excused" and not (mi.reason or "").strip():
            raise ValidationFailed("an excused absence needs a reason (sick, bereavement, …)")
    reg = (
        await session.execute(
            select(Register)
            .where(
                Register.class_group_id == c.id, Register.on_date == on, Register.session == sess
            )
            .execution_options(**BYPASS)
        )
    ).scalar_one_or_none()
    past = on < now
    if reg is None:
        if (now - on).days > LATE_DAYS and not may_amend:
            raise Forbidden(
                f"registers can be taken up to {LATE_DAYS} days late",
                next_action="Ask the head teacher to record it.",
            )
        reg = Register(
            tenant_id=c.tenant_id,
            scope_node_id=c.scope_node_id,
            institution_id=c.institution_id,
            class_group_id=c.id,
            term_id=term.id,
            on_date=on,
            session=sess,
            taken_by=ctx.user_id,
        )
        session.add(reg)
        await session.flush()
        existing: dict[uuid.UUID, LearnerMark] = {}
    else:
        existing = {
            m.learner_id: m
            for m in (
                await session.execute(
                    select(LearnerMark)
                    .where(LearnerMark.register_id == reg.id)
                    .execution_options(**BYPASS)
                )
            ).scalars()
        }
    changed = [
        lid
        for lid, m in existing.items()
        if lid in given and (given[lid].status, given[lid].reason or None) != (m.status, m.reason)
    ]
    why_amend = " ".join((amend_reason or "").split())
    if past and changed:
        if not may_amend:
            raise Forbidden(
                "a register cannot be changed after the day",
                next_action="Ask the head teacher to correct it, with the reason.",
            )
        if len(why_amend) < 5:
            raise ValidationFailed("say why the register is being corrected")
    for lid, eid, _p in members:
        g = given[lid]
        reason = (g.reason or "").strip()[:200] or None
        cur = existing.get(lid)
        if cur is None:
            session.add(
                LearnerMark(
                    tenant_id=c.tenant_id,
                    scope_node_id=c.scope_node_id,
                    register_id=reg.id,
                    learner_id=lid,
                    enrolment_id=eid,
                    status=g.status,
                    reason=reason,
                )
            )
        elif (cur.status, cur.reason) != (g.status, reason):
            if past and cur.status != g.status:
                session.add(
                    AttendanceCorrection(
                        tenant_id=c.tenant_id,
                        mark_id=cur.id,
                        old_status=cur.status,
                        new_status=g.status,
                        reason=why_amend[:200],
                        by_user=ctx.user_id,
                    )
                )
            cur.status, cur.reason = g.status, reason
    if not past:
        reg.taken_by, reg.taken_at = ctx.user_id, utcnow()
    await session.flush()
    return await get_register(session, c.id, on, sess)


# --------------------------------------------------------------------------- figures


@dataclass
class Summary:
    opened: int = 0  # registers taken while the learner was in the class
    present: int = 0  # present or late
    late: int = 0
    absent: int = 0
    excused: int = 0

    @property
    def rate(self) -> float | None:
        return round(self.present / self.opened, 3) if self.opened else None

    def add(self, status: str, n: int = 1) -> None:
        self.opened += n
        if status in ("present", "late"):
            self.present += n
        if status == "late":
            self.late += n
        if status == "absent":
            self.absent += n
        if status == "excused":
            self.excused += n

    def as_dict(self) -> dict[str, Any]:
        return {
            "opened": self.opened,
            "present": self.present,
            "late": self.late,
            "absent": self.absent,
            "excused": self.excused,
            "rate": self.rate,
        }


async def learner_summary(
    session: AsyncSession, learner_id: uuid.UUID, term_id: uuid.UUID
) -> Summary:
    """A learner's attendance in a term (for the report card)."""
    s = Summary()
    for status, n in await session.execute(
        select(LearnerMark.status, func.count(LearnerMark.id))
        .join(Register, Register.id == LearnerMark.register_id)
        .where(LearnerMark.learner_id == learner_id, Register.term_id == term_id)
        .group_by(LearnerMark.status)
        .execution_options(**BYPASS)
    ):
        s.add(status, n)
    return s


@dataclass(frozen=True, slots=True)
class LearnerLine:
    learner_id: uuid.UUID
    name: str
    summary: dict[str, Any]
    persistent: bool
    demo: bool


@dataclass(frozen=True, slots=True)
class ClassAttendance:
    class_id: uuid.UUID
    class_name: str
    term_id: uuid.UUID
    term_name: str
    registers: int
    rate: float | None
    learners: list[LearnerLine]


async def class_attendance(
    session: AsyncSession, class_id: uuid.UUID, term_id: uuid.UUID
) -> ClassAttendance:
    """Each learner's attendance in a term, with persistent absence flagged."""
    from syntegra.mdm.api import is_demo

    c = await _class(session, class_id)
    term = (
        await session.execute(
            select(AcademicPeriod)
            .where(AcademicPeriod.id == term_id, AcademicPeriod.parent_id == c.session_id)
            .execution_options(**BYPASS)
        )
    ).scalar_one_or_none()
    if term is None:
        raise NotFound("that term is not in the class's session")
    regs = (
        await session.execute(
            select(func.count(Register.id))
            .where(Register.class_group_id == c.id, Register.term_id == term.id)
            .execution_options(**BYPASS)
        )
    ).scalar_one()
    sums: dict[uuid.UUID, Summary] = {}
    for lid, status, n in await session.execute(
        select(LearnerMark.learner_id, LearnerMark.status, func.count(LearnerMark.id))
        .join(Register, Register.id == LearnerMark.register_id)
        .where(Register.class_group_id == c.id, Register.term_id == term.id)
        .group_by(LearnerMark.learner_id, LearnerMark.status)
        .execution_options(**BYPASS)
    ):
        sums.setdefault(lid, Summary()).add(status, n)
    lines = []
    total = Summary()
    on = min(max(term.starts_on, min(local_now().date(), term.ends_on)), term.ends_on)
    for lid, _eid, p in await members_on(session, c.id, on):
        s = sums.get(lid, Summary())
        total.opened += s.opened
        total.present += s.present
        persistent = (
            s.opened >= PERSISTENT_MIN_REGISTERS
            and s.rate is not None
            and s.rate < PERSISTENT_BELOW
        )
        lines.append(LearnerLine(lid, _name(p), s.as_dict(), persistent, is_demo(p.source_system)))
    return ClassAttendance(c.id, c.name, term.id, term.name, int(regs), total.rate, lines)


@dataclass(frozen=True, slots=True)
class ClassDay:
    class_id: uuid.UUID
    class_name: str
    am: bool
    pm: bool
    present: int
    marked: int


@dataclass(frozen=True, slots=True)
class SchoolDay:
    institution_id: uuid.UUID
    on_date: date
    closed_reason: str | None
    classes: list[ClassDay]


async def school_day(session: AsyncSession, institution_id: uuid.UUID, on: date) -> SchoolDay:
    """Which classes have taken their registers on a day (for the head)."""
    inst = await _institution(session, institution_id)
    classes = list(
        (
            await session.execute(
                select(ClassGroup)
                .where(ClassGroup.institution_id == inst.id, ClassGroup.status == "active")
                .order_by(ClassGroup.year_code, ClassGroup.arm)
                .execution_options(**BYPASS)
            )
        ).scalars()
    )
    regs: dict[tuple[uuid.UUID, str], uuid.UUID] = {
        (cid, sess): rid
        for cid, sess, rid in await session.execute(
            select(Register.class_group_id, Register.session, Register.id)
            .where(Register.institution_id == inst.id, Register.on_date == on)
            .execution_options(**BYPASS)
        )
    }
    counts: dict[uuid.UUID, tuple[int, int]] = {}
    for rid, present, marked in await session.execute(
        select(
            LearnerMark.register_id,
            func.sum(case((LearnerMark.status.in_(("present", "late")), 1), else_=0)),
            func.count(LearnerMark.id),
        )
        .where(LearnerMark.register_id.in_(list(regs.values()) or [uuid.UUID(int=0)]))
        .group_by(LearnerMark.register_id)
        .execution_options(**BYPASS)
    ):
        counts[rid] = (int(present or 0), int(marked))
    out = []
    for c in classes:
        why, term = await not_a_school_day(session, c, on)
        if term is None and why:
            continue
        am = regs.get((c.id, "am"))
        p, m = counts.get(am, (0, 0)) if am else (0, 0)
        out.append(ClassDay(c.id, c.name, am is not None, (c.id, "pm") in regs, p, m))
    reason = await closure_on(session, inst.scope_node_id, on)
    if on.weekday() >= 5:
        reason = reason or "weekend"
    return SchoolDay(inst.id, on, reason, out)


# --------------------------------------------------------------------------- staff


@dataclass(frozen=True, slots=True)
class StaffLine:
    staff_id: uuid.UUID
    name: str
    position: str
    status: str | None
    time_in: time | None
    time_out: time | None
    note: str | None
    self_recorded: bool
    demo: bool


async def _late_after(session: AsyncSession, scope_id: uuid.UUID) -> time:
    v = await governance.resolve_config(session, scope_id, LATE_SETTING)
    try:
        return time.fromisoformat(v) if isinstance(v, str) else DEFAULT_LATE_AFTER
    except ValueError:
        return DEFAULT_LATE_AFTER


async def _posted(
    session: AsyncSession, institution_id: uuid.UUID, on: date
) -> list[tuple[Staff, StaffPost, Person]]:
    rows = await session.execute(
        select(Staff, StaffPost, Person)
        .join(StaffPost, StaffPost.staff_id == Staff.id)
        .join(Person, Person.id == Staff.person_id)
        .where(
            StaffPost.institution_id == institution_id,
            StaffPost.start_date <= on,
            (StaffPost.end_date.is_(None)) | (StaffPost.end_date >= on),
            Staff.status.in_(("active", "on_leave")),
        )
        .order_by(Person.family_name, Person.given_name)
        .execution_options(**BYPASS)
    )
    seen: set[uuid.UUID] = set()
    out = []
    for st, post, p in rows.all():
        if st.id not in seen:
            seen.add(st.id)
            out.append((st, post, p))
    return out


async def staff_day(session: AsyncSession, institution_id: uuid.UUID, on: date) -> list[StaffLine]:
    """Everyone posted to the institution on a day, and how their day was recorded."""
    from syntegra.mdm.api import is_demo

    inst = await _institution(session, institution_id)
    rows = {
        r.staff_id: r
        for r in (
            await session.execute(
                select(StaffAttendance)
                .where(StaffAttendance.institution_id == inst.id, StaffAttendance.on_date == on)
                .execution_options(**BYPASS)
            )
        ).scalars()
    }
    out = []
    for st, post, p in await _posted(session, inst.id, on):
        r = rows.get(st.id)
        out.append(
            StaffLine(
                st.id,
                f"{p.given_name} {p.family_name}",
                post.position,
                r.status if r else None,
                r.time_in if r else None,
                r.time_out if r else None,
                r.note if r else None,
                r.self_recorded if r else False,
                is_demo(p.source_system),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class StaffIn:
    staff_id: uuid.UUID
    status: str
    time_in: time | None = None
    time_out: time | None = None
    note: str | None = None


async def record_staff(
    session: AsyncSession, institution_id: uuid.UUID, on: date, entries: list[StaffIn]
) -> list[StaffLine]:
    """The head records staff attendance for a day (overriding what staff recorded)."""
    ctx = current_context()
    inst = await _institution(session, institution_id)
    if on > local_now().date():
        raise ValidationFailed("attendance cannot be recorded for a day that has not come")
    posted = {st.id for st, _, _ in await _posted(session, inst.id, on)}
    for e in entries:
        if e.staff_id not in posted:
            raise ValidationFailed("one of the staff is not posted to this institution that day")
        if e.status not in STAFF_STATUSES:
            raise ValidationFailed(f"status is one of {', '.join(STAFF_STATUSES)}")
        if e.time_in and e.time_out and e.time_out < e.time_in:
            raise ValidationFailed("time out is before time in")
        row = (
            await session.execute(
                select(StaffAttendance)
                .where(StaffAttendance.staff_id == e.staff_id, StaffAttendance.on_date == on)
                .execution_options(**BYPASS)
            )
        ).scalar_one_or_none()
        note = (e.note or "").strip()[:200] or None
        if row is None:
            session.add(
                StaffAttendance(
                    tenant_id=inst.tenant_id,
                    scope_node_id=inst.scope_node_id,
                    staff_id=e.staff_id,
                    institution_id=inst.id,
                    on_date=on,
                    status=e.status,
                    time_in=e.time_in,
                    time_out=e.time_out,
                    note=note,
                    self_recorded=False,
                    recorded_by=ctx.user_id,
                )
            )
        else:
            row.status, row.time_in, row.time_out, row.note = (
                e.status,
                e.time_in or row.time_in,
                e.time_out or row.time_out,
                note,
            )
            row.self_recorded, row.recorded_by = False, ctx.user_id
    await session.flush()
    return await staff_day(session, inst.id, on)


@dataclass(frozen=True, slots=True)
class MyDay:
    staff_id: uuid.UUID | None
    institution_id: uuid.UUID | None
    institution_name: str | None
    on_date: date
    status: str | None
    time_in: time | None
    time_out: time | None
    late_after: time


async def _my_post(
    session: AsyncSession, person_id: uuid.UUID | None, on: date
) -> tuple[Staff, StaffPost, Institution] | None:
    if person_id is None:
        return None
    row = (
        await session.execute(
            select(Staff, StaffPost, Institution)
            .join(StaffPost, StaffPost.staff_id == Staff.id)
            .join(Institution, Institution.id == StaffPost.institution_id)
            .where(
                Staff.person_id == person_id,
                Staff.status == "active",
                StaffPost.start_date <= on,
                (StaffPost.end_date.is_(None)) | (StaffPost.end_date >= on),
                StaffPost.is_primary.is_(True),
            )
            .execution_options(**BYPASS)
        )
    ).first()
    return (row[0], row[1], row[2]) if row else None


async def my_day(session: AsyncSession, person_id: uuid.UUID | None) -> MyDay:
    on = local_now().date()
    post = await _my_post(session, person_id, on)
    if post is None:
        return MyDay(None, None, None, on, None, None, None, DEFAULT_LATE_AFTER)
    st, _, inst = post
    row = (
        await session.execute(
            select(StaffAttendance)
            .where(StaffAttendance.staff_id == st.id, StaffAttendance.on_date == on)
            .execution_options(**BYPASS)
        )
    ).scalar_one_or_none()
    return MyDay(
        st.id,
        inst.id,
        inst.name,
        on,
        row.status if row else None,
        row.time_in if row else None,
        row.time_out if row else None,
        await _late_after(session, inst.scope_node_id),
    )


async def sign(session: AsyncSession, person_id: uuid.UUID | None, action: str) -> MyDay:
    """A member of staff signs in (present, or late after the late time) or out, for today."""
    ctx = current_context()
    now = local_now()
    on = now.date()
    post = await _my_post(session, person_id, on)
    if post is None:
        raise Forbidden("you are not posted to an institution today")
    st, _, inst = post
    row = (
        await session.execute(
            select(StaffAttendance)
            .where(StaffAttendance.staff_id == st.id, StaffAttendance.on_date == on)
            .execution_options(**BYPASS)
        )
    ).scalar_one_or_none()
    t = now.time().replace(second=0, microsecond=0)
    if action == "in":
        if row and row.time_in:
            raise Conflict(f"you signed in at {row.time_in:%H:%M}")
        status = "late" if t > await _late_after(session, inst.scope_node_id) else "present"
        if row is None:
            session.add(
                StaffAttendance(
                    tenant_id=inst.tenant_id,
                    scope_node_id=inst.scope_node_id,
                    staff_id=st.id,
                    institution_id=inst.id,
                    on_date=on,
                    status=status,
                    time_in=t,
                    self_recorded=True,
                    recorded_by=ctx.user_id,
                )
            )
        else:
            row.time_in, row.status = t, status
    elif action == "out":
        if row is None or row.time_in is None:
            raise Conflict("sign in first")
        if row.time_out:
            raise Conflict(f"you signed out at {row.time_out:%H:%M}")
        row.time_out = t
    else:
        raise ValidationFailed("sign in or out")
    await session.flush()
    return await my_day(session, person_id)


# --------------------------------------------------------------------------- monitoring figures


@dataclass
class Rates:
    learner_marks: int = 0
    learner_present: int = 0
    staff_days: int = 0
    staff_present: int = 0
    by_institution: dict[uuid.UUID, tuple[int, int]] = field(default_factory=dict)


async def rates(
    session: AsyncSession, institution_ids: list[uuid.UUID], since: date, until: date
) -> Rates:
    """Learner and staff attendance across institutions over a period (monitoring)."""
    r = Rates()
    ids = institution_ids or [uuid.UUID(int=0)]
    for inst_id, status, n in await session.execute(
        select(Register.institution_id, LearnerMark.status, func.count(LearnerMark.id))
        .join(Register, Register.id == LearnerMark.register_id)
        .where(
            Register.institution_id.in_(ids),
            Register.on_date >= since,
            Register.on_date <= until,
        )
        .group_by(Register.institution_id, LearnerMark.status)
        .execution_options(**BYPASS)
    ):
        marks, present = r.by_institution.get(inst_id, (0, 0))
        ok = n if status in ("present", "late") else 0
        r.by_institution[inst_id] = (marks + n, present + ok)
        r.learner_marks += n
        r.learner_present += ok
    for status, n in await session.execute(
        select(StaffAttendance.status, func.count(StaffAttendance.id))
        .where(
            StaffAttendance.institution_id.in_(ids),
            StaffAttendance.on_date >= since,
            StaffAttendance.on_date <= until,
        )
        .group_by(StaffAttendance.status)
        .execution_options(**BYPASS)
    ):
        r.staff_days += n
        r.staff_present += n if status in ("present", "late", "official_duty") else 0
    return r


def school_days_back(on: date, n: int) -> list[date]:
    """The last ``n`` weekdays up to ``on`` (for demonstration activity)."""
    out: list[date] = []
    d = on
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]
