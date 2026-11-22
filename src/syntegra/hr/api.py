"""Staff 360 and postings (CEDA §4, §27.1; ADR-024).

* **Staff** is one person's employment with an employer (a tenant), 1:1 with Person 360, with a
  staff number (``STF-NNNN-NNNN-CC``), category, the employer's own file number and dates.
* **Posts** say where someone works, as what, at which grade level and step. A posting, transfer
  or promotion ends the current post and starts a new one, so the record is a history.
* **Posting requests** are decided by a second person (four-eyes) before they take effect.
* The staff record follows the current primary post: the school sees the staff posted to it,
  the LGEA, board and state see everyone beneath them, and a transfer moves visibility too.
* Names and date of birth stay on Person 360. Whoever may see a staff record sees that person's
  name, sex and date of birth with it; the rest of Person 360 still needs ``person:view``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import staff_number
from syntegra.governance import api as governance
from syntegra.hr import events
from syntegra.hr.domain import (
    DEFAULT_RETIREMENT,
    QUALIFICATION_LABEL,
    QUALIFICATION_RANK,
    SALARY_STRUCTURES,
    AppointmentType,
    Category,
    ExitReason,
    QualificationKind,
    RetirementRule,
    StaffStatus,
    check_grade,
    is_teaching_qualification,
    retirement_date,
    years_of_service,
)
from syntegra.hr.models import (
    PostingRequest,
    Staff,
    StaffPost,
    StaffQualification,
    TeacherRegistration,
)
from syntegra.institutions.models import EducationBody, Institution
from syntegra.mdm.models import Person
from syntegra.platform.outbox import publish
from syntegra.platform.sequence import next_value
from syntegra.platform.session import PLATFORM_BYPASS

RETIREMENT_SETTING = "hr.retirement"
CURRENT_REGISTRATION = ("registered", "provisional")


# --------------------------------------------------------------------------- views


@dataclass(frozen=True, slots=True)
class PostView:
    id: uuid.UUID
    place_scope_id: uuid.UUID
    place_name: str
    institution_id: uuid.UUID | None
    institution_code: str | None
    position: str
    grade_level: int | None
    step: int | None
    salary_structure: str | None
    is_primary: bool
    appointment_type: str
    start_date: date
    end_date: date | None
    reference: str | None

    @property
    def current(self) -> bool:
        t = today()
        return self.start_date <= t and (self.end_date is None or self.end_date >= t)


@dataclass(frozen=True, slots=True)
class QualificationView:
    id: uuid.UUID
    kind: str
    label: str
    title: str
    awarding_body: str | None
    year: int | None
    teaching: bool
    verified: bool


@dataclass(frozen=True, slots=True)
class RegistrationView:
    id: uuid.UUID
    body_code: str
    number: str
    status: str
    registered_on: date | None
    expires_on: date | None

    @property
    def current(self) -> bool:
        return self.status in CURRENT_REGISTRATION and (
            self.expires_on is None or self.expires_on >= today()
        )


@dataclass(frozen=True, slots=True)
class StaffView:
    id: uuid.UUID
    staff_number: str
    person_id: uuid.UUID
    surname: str
    first_name: str
    other_names: str | None
    sex: str | None
    date_of_birth: date | None
    category: str
    status: str
    government_file_number: str | None
    first_appointment_date: date | None
    confirmation_date: date | None
    exit_date: date | None
    exit_reason: str | None
    years_of_service: int | None
    retirement_date: date | None
    posts: list[PostView] = field(default_factory=list)
    qualifications: list[QualificationView] = field(default_factory=list)
    registrations: list[RegistrationView] = field(default_factory=list)

    @property
    def current_post(self) -> PostView | None:
        return next((p for p in self.posts if p.is_primary and p.current), None)

    @property
    def highest_qualification(self) -> QualificationView | None:
        ranked = sorted(
            self.qualifications, key=lambda q: QUALIFICATION_RANK.index(QualificationKind(q.kind))
        )
        return ranked[0] if ranked else None

    @property
    def registered_teacher(self) -> bool:
        return any(r.current for r in self.registrations)

    @property
    def qualified_teacher(self) -> bool:
        """A verified teaching qualification and a current TRCN registration."""
        return self.registered_teacher and any(
            q.teaching and q.verified for q in self.qualifications
        )


# --------------------------------------------------------------------------- helpers


def _publish(session: AsyncSession, staff: Staff, event: str, payload: dict[str, Any]) -> None:
    ctx = current_context()
    publish(
        session,
        EventEnvelope(
            event_type=event,
            tenant_id=staff.tenant_id,
            scope_node_id=staff.scope_node_id,
            aggregate_type="staff",
            aggregate_id=staff.id,
            actor_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
            payload=payload,
        ),
    )


async def _rule(session: AsyncSession, category: str) -> RetirementRule:
    ctx = current_context()
    value = await governance.resolve_config(session, ctx.scope_node_id, RETIREMENT_SETTING)
    if isinstance(value, dict) and isinstance(value.get(category), dict):
        v = value[category]
        return RetirementRule(int(v["age"]), int(v["service"]))
    return DEFAULT_RETIREMENT[Category(category)]


async def _staff(session: AsyncSession, staff_id: uuid.UUID, *, lock: bool = False) -> Staff:
    stmt = select(Staff).where(Staff.id == staff_id)
    if lock:
        stmt = stmt.with_for_update()
    s = (await session.execute(stmt)).scalar_one_or_none()
    if s is None:
        raise NotFound("staff member not found in your scope")
    return s


async def _people(session: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Person]:
    """Identity for staff the caller may already see (name, sex, date of birth). A sanctioned
    read across scopes: limited to the tenant and to these people."""
    if not ids:
        return {}
    ctx = current_context()
    rows = (
        await session.execute(
            select(Person)
            .where(Person.id.in_(list(ids)), Person.tenant_id == ctx.tenant_id)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalars()
    return {p.id: p for p in rows}


async def _place(
    session: AsyncSession, *, institution_id: uuid.UUID | None, scope_id: uuid.UUID | None
) -> tuple[uuid.UUID, Institution | None]:
    """Where a post is: an institution in the caller's scope, or an office (a scope node such as
    an LGEA or the board's headquarters) within the caller's scope."""
    ctx = current_context()
    if institution_id is not None:
        inst = (
            await session.execute(select(Institution).where(Institution.id == institution_id))
        ).scalar_one_or_none()
        if inst is None:
            raise NotFound("institution not found in your scope")
        if inst.status != "active":
            raise ValidationFailed("the institution is closed")
        return inst.scope_node_id, inst
    place = scope_id or ctx.scope_node_id
    if not await governance.is_within(session, ctx.scope_node_id, place):
        raise ValidationFailed("that place of work is outside your scope")
    return place, None


async def _views(session: AsyncSession, staff: Sequence[Staff]) -> list[StaffView]:
    ids = [s.id for s in staff]
    if not ids:
        return []
    people = await _people(session, [s.person_id for s in staff])
    posts: dict[uuid.UUID, list[StaffPost]] = {i: [] for i in ids}
    for p in (
        await session.execute(
            select(StaffPost)
            .where(StaffPost.staff_id.in_(ids))
            .order_by(StaffPost.start_date.desc())
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalars():
        posts[p.staff_id].append(p)
    quals: dict[uuid.UUID, list[StaffQualification]] = {i: [] for i in ids}
    for q in (
        await session.execute(
            select(StaffQualification).where(StaffQualification.staff_id.in_(ids))
        )
    ).scalars():
        quals[q.staff_id].append(q)
    regs: dict[uuid.UUID, list[TeacherRegistration]] = {i: [] for i in ids}
    for r in (
        await session.execute(
            select(TeacherRegistration).where(TeacherRegistration.staff_id.in_(ids))
        )
    ).scalars():
        regs[r.staff_id].append(r)
    inst_ids = {p.institution_id for ps in posts.values() for p in ps if p.institution_id}
    insts = (
        {
            i.id: i
            for i in (
                await session.execute(
                    select(Institution)
                    .where(Institution.id.in_(inst_ids))
                    .execution_options(**{PLATFORM_BYPASS: True})
                )
            ).scalars()
        }
        if inst_ids
        else {}
    )
    names: dict[uuid.UUID, str] = {}
    for ps in posts.values():
        for p in ps:
            if p.post_scope_id not in names:
                inst = insts.get(p.institution_id) if p.institution_id else None
                names[p.post_scope_id] = (
                    inst.name
                    if inst
                    else (await governance.get_scope_node(session, p.post_scope_id)).name
                )
    rules = {c: await _rule(session, c) for c in {s.category for s in staff}}
    on = today()
    out = []
    for s in staff:
        person = people.get(s.person_id)
        out.append(
            StaffView(
                s.id,
                s.staff_number,
                s.person_id,
                person.family_name if person else "",
                person.given_name if person else "",
                person.other_names if person else None,
                person.sex_at_registration if person else None,
                person.date_of_birth if person else None,
                s.category,
                s.status,
                s.government_file_number,
                s.first_appointment_date,
                s.confirmation_date,
                s.exit_date,
                s.exit_reason,
                years_of_service(s.first_appointment_date, s.exit_date or on),
                retirement_date(
                    rules[s.category],
                    date_of_birth=person.date_of_birth if person else None,
                    first_appointment=s.first_appointment_date,
                ),
                [
                    PostView(
                        p.id,
                        p.post_scope_id,
                        names[p.post_scope_id],
                        p.institution_id,
                        insts[p.institution_id].institution_code if p.institution_id else None,
                        p.position,
                        p.grade_level,
                        p.step,
                        p.salary_structure,
                        p.is_primary,
                        p.appointment_type,
                        p.start_date,
                        p.end_date,
                        p.reference,
                    )
                    for p in posts[s.id]
                ],
                [
                    QualificationView(
                        q.id,
                        q.kind,
                        QUALIFICATION_LABEL[QualificationKind(q.kind)],
                        q.title,
                        q.awarding_body,
                        q.year,
                        q.teaching,
                        q.verified_at is not None,
                    )
                    for q in quals[s.id]
                ],
                [
                    RegistrationView(
                        r.id, r.body_code, r.number, r.status, r.registered_on, r.expires_on
                    )
                    for r in regs[s.id]
                ],
            )
        )
    return out


async def get_staff(session: AsyncSession, staff_id: uuid.UUID) -> StaffView:
    return (await _views(session, [await _staff(session, staff_id)]))[0]


def _check_post(
    position: str, grade_level: int | None, step: int | None, salary_structure: str | None
) -> list[str]:
    errors = check_grade(grade_level, step)
    if not position.strip():
        errors.append("position is required")
    if salary_structure and salary_structure.upper() not in SALARY_STRUCTURES:
        errors.append(f"salary structure must be one of {', '.join(SALARY_STRUCTURES)}")
    return errors


# --------------------------------------------------------------------------- appointment


@dataclass(frozen=True, slots=True)
class AppointmentIn:
    person_id: uuid.UUID
    category: str
    position: str
    start_date: date
    institution_id: uuid.UUID | None = None
    place_scope_id: uuid.UUID | None = None  # an office, when not at an institution
    grade_level: int | None = None
    step: int | None = None
    salary_structure: str | None = None
    first_appointment_date: date | None = None  # with the employer; defaults to start_date
    confirmation_date: date | None = None
    government_file_number: str | None = None
    reference: str | None = None


async def appoint(session: AsyncSession, data: AppointmentIn) -> StaffView:
    """Make a Person 360 record a staff member of this employer, in a first post."""
    ctx = current_context()
    errors = _check_post(data.position, data.grade_level, data.step, data.salary_structure)
    if data.category not in {c.value for c in Category}:
        errors.append("category must be teaching or non_teaching")
    first = data.first_appointment_date or data.start_date
    if first > data.start_date:
        errors.append("first appointment cannot be after the start of this post")
    if errors:
        raise ValidationFailed("; ".join(errors))
    person = (
        await session.execute(select(Person).where(Person.id == data.person_id))
    ).scalar_one_or_none()
    if person is None or person.status != "active":
        raise NotFound("person not found in your scope", next_action="Register the person first.")
    dup = (
        await session.execute(
            select(Staff.id)
            .where(Staff.tenant_id == ctx.tenant_id, Staff.person_id == data.person_id)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).first()
    if dup:
        raise Conflict(
            "this person is already a staff member here",
            next_action="Open their staff record; post or transfer them instead.",
        )
    file_no = (data.government_file_number or "").strip().upper() or None
    if file_no:
        taken = (
            await session.execute(
                select(Staff.id)
                .where(Staff.tenant_id == ctx.tenant_id, Staff.government_file_number == file_no)
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        ).first()
        if taken:
            raise Conflict(f"file number {file_no} is already used by another staff member")
    place, inst = await _place(
        session, institution_id=data.institution_id, scope_id=data.place_scope_id
    )
    seq = await next_value(session, ctx.tenant_id, "staff")
    staff = Staff(
        tenant_id=ctx.tenant_id,
        scope_node_id=place,
        person_id=person.id,
        staff_number=staff_number(seq),
        employer_scope_id=ctx.scope_node_id,
        category=data.category,
        status=StaffStatus.ACTIVE.value,
        government_file_number=file_no,
        first_appointment_date=first,
        confirmation_date=data.confirmation_date,
    )
    session.add(staff)
    await session.flush()
    session.add(
        StaffPost(
            tenant_id=ctx.tenant_id,
            scope_node_id=place,
            staff_id=staff.id,
            post_scope_id=place,
            institution_id=inst.id if inst else None,
            position=data.position.strip(),
            grade_level=data.grade_level,
            step=data.step,
            salary_structure=(data.salary_structure or "").upper() or None,
            is_primary=True,
            appointment_type=AppointmentType.FIRST_APPOINTMENT.value
            if data.start_date == first
            else AppointmentType.POSTING.value,
            start_date=data.start_date,
            reference=data.reference,
        )
    )
    _publish(session, staff, events.STAFF_APPOINTED, {"category": staff.category})
    await session.flush()
    return await get_staff(session, staff.id)


# --------------------------------------------------------------------------- the nominal roll


@dataclass(frozen=True, slots=True)
class RollFilter:
    institution_id: uuid.UUID | None = None
    category: str | None = None
    status: str | None = "active"
    position: str | None = None
    grade_min: int | None = None
    grade_max: int | None = None
    retiring_within_days: int | None = None
    unregistered_teachers: bool = False


async def nominal_roll(
    session: AsyncSession,
    f: RollFilter = RollFilter(),
    *,
    after: uuid.UUID | None = None,
    limit: int = 200,
) -> list[StaffView]:
    """Staff in the caller's scope, with current post, grade, qualification and registration."""
    stmt = select(Staff).order_by(Staff.id)
    if f.category:
        stmt = stmt.where(Staff.category == f.category)
    if f.status:
        stmt = stmt.where(Staff.status == f.status)
    if after is not None:
        stmt = stmt.where(Staff.id > after)
    if f.institution_id is not None:
        current = select(StaffPost.staff_id).where(
            StaffPost.institution_id == f.institution_id,
            StaffPost.is_primary.is_(True),
            StaffPost.end_date.is_(None),
        )
        stmt = stmt.where(Staff.id.in_(current))
    out: list[StaffView] = []
    batch = 500
    cursor = after
    while len(out) < limit:
        page_stmt = (
            stmt.limit(batch) if cursor is None else stmt.where(Staff.id > cursor).limit(batch)
        )
        rows = list((await session.execute(page_stmt)).scalars())
        if not rows:
            break
        cursor = rows[-1].id
        for v in await _views(session, rows):
            if _keep(v, f):
                out.append(v)
                if len(out) >= limit:
                    break
    return out


def _keep(v: StaffView, f: RollFilter) -> bool:
    post = v.current_post
    if f.position and not (post and f.position.casefold() in post.position.casefold()):
        return False
    grade = post.grade_level if post else None
    if f.grade_min is not None and (grade is None or grade < f.grade_min):
        return False
    if f.grade_max is not None and (grade is None or grade > f.grade_max):
        return False
    if f.retiring_within_days is not None:
        limit = today() + timedelta(days=f.retiring_within_days)
        if v.retirement_date is None or v.retirement_date > limit:
            return False
    return not (
        f.unregistered_teachers and (v.category != Category.TEACHING.value or v.registered_teacher)
    )


@dataclass(frozen=True, slots=True)
class PlaceSummary:
    place_name: str
    institution_code: str | None
    teaching: int
    non_teaching: int
    qualified_teachers: int
    registered_teachers: int
    retiring_within_a_year: int


async def staffing_summary(session: AsyncSession) -> list[PlaceSummary]:
    """Per place of work, in the caller's scope: who is there and how qualified."""
    roll = await nominal_roll(session, limit=100_000)
    within = today() + timedelta(days=365)
    places: dict[tuple[str, str | None], list[StaffView]] = {}
    for v in roll:
        post = v.current_post
        key = (post.place_name, post.institution_code) if post else ("(no current post)", None)
        places.setdefault(key, []).append(v)
    return [
        PlaceSummary(
            name,
            code,
            sum(v.category == "teaching" for v in vs),
            sum(v.category == "non_teaching" for v in vs),
            sum(v.category == "teaching" and v.qualified_teacher for v in vs),
            sum(v.category == "teaching" and v.registered_teacher for v in vs),
            sum(v.retirement_date is not None and v.retirement_date <= within for v in vs),
        )
        for (name, code), vs in sorted(places.items())
    ]


# --------------------------------------------------------------------------- qualifications


async def add_qualification(
    session: AsyncSession,
    staff_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    awarding_body: str | None = None,
    year: int | None = None,
) -> StaffView:
    staff = await _staff(session, staff_id)
    try:
        k = QualificationKind(kind)
    except ValueError:
        raise ValidationFailed(
            f"qualification '{kind}' is not known",
            next_action="Use " + ", ".join(q.value for q in QualificationKind) + ".",
        ) from None
    if not title.strip():
        raise ValidationFailed("the qualification needs a title, e.g. B.Ed Mathematics")
    if year is not None and not 1950 <= year <= today().year:
        raise ValidationFailed("year is not plausible")
    session.add(
        StaffQualification(
            tenant_id=staff.tenant_id,
            scope_node_id=staff.scope_node_id,
            staff_id=staff.id,
            kind=k.value,
            title=title.strip(),
            awarding_body=(awarding_body or "").strip() or None,
            year=year,
            teaching=is_teaching_qualification(k, title),
        )
    )
    await session.flush()
    return await get_staff(session, staff.id)


async def verify_qualification(session: AsyncSession, qualification_id: uuid.UUID) -> StaffView:
    """Certificate seen and checked. The staff member cannot verify their own."""
    ctx = current_context()
    q = (
        await session.execute(
            select(StaffQualification).where(StaffQualification.id == qualification_id)
        )
    ).scalar_one_or_none()
    if q is None:
        raise NotFound("qualification not found in your scope")
    staff = await _staff(session, q.staff_id)
    person = (await _people(session, [staff.person_id])).get(staff.person_id)
    if ctx.user_id is not None and person is not None:
        from syntegra.iam.models import AppUser  # the user's own person record

        mine = (
            await session.execute(
                select(AppUser.person_id)
                .where(AppUser.id == ctx.user_id)
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        ).scalar_one_or_none()
        if mine == person.id:
            raise Conflict("you cannot verify your own qualification")
    if q.verified_at is None:
        q.verified_at, q.verified_by = utcnow(), ctx.user_id
        _publish(session, staff, events.QUALIFICATION_VERIFIED, {"qualification_id": str(q.id)})
        await session.flush()
    return await get_staff(session, staff.id)


async def set_registration(
    session: AsyncSession,
    staff_id: uuid.UUID,
    *,
    number: str,
    status: str = "registered",
    body_code: str = "TRCN",
    registered_on: date | None = None,
    expires_on: date | None = None,
) -> StaffView:
    """Record (or update) registration with a teaching council."""
    staff = await _staff(session, staff_id)
    code = body_code.strip().upper()
    body = (
        await session.execute(select(EducationBody).where(EducationBody.code == code))
    ).scalar_one_or_none()
    if body is None or "professional_registration" not in body.roles:
        raise ValidationFailed(
            f"{code} is not a teacher registration body in the register",
            next_action="Use TRCN (seed the education bodies first if the list is empty).",
        )
    if status not in ("registered", "provisional", "expired", "suspended"):
        raise ValidationFailed("status must be registered, provisional, expired or suspended")
    number = number.strip().upper()
    if not number:
        raise ValidationFailed("the registration number is required")
    if registered_on and expires_on and expires_on < registered_on:
        raise ValidationFailed("expiry is before registration")
    other = (
        await session.execute(
            select(TeacherRegistration.staff_id)
            .where(
                TeacherRegistration.tenant_id == staff.tenant_id,
                TeacherRegistration.body_code == code,
                TeacherRegistration.number == number,
            )
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one_or_none()
    if other is not None and other != staff.id:
        raise Conflict(f"{code} number {number} is recorded for another staff member")
    row = (
        await session.execute(
            select(TeacherRegistration).where(
                TeacherRegistration.staff_id == staff.id, TeacherRegistration.body_code == code
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = TeacherRegistration(
            tenant_id=staff.tenant_id,
            scope_node_id=staff.scope_node_id,
            staff_id=staff.id,
            body_code=code,
            number=number,
            status=status,
        )
        session.add(row)
    row.number, row.status = number, status
    row.registered_on, row.expires_on = registered_on, expires_on
    await session.flush()
    _publish(session, staff, events.REGISTRATION_RECORDED, {"body_code": code, "status": status})
    await session.flush()
    return await get_staff(session, staff.id)


# --------------------------------------------------------------------------- postings


@dataclass(frozen=True, slots=True)
class PostingIn:
    appointment_type: str
    effective_date: date
    institution_id: uuid.UUID | None = None
    place_scope_id: uuid.UUID | None = None  # keep None with no institution for a promotion
    position: str | None = None  # defaults to the current position
    grade_level: int | None = None  # defaults to the current grade
    step: int | None = None
    salary_structure: str | None = None
    reason: str | None = None
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class PostingView:
    id: uuid.UUID
    staff_id: uuid.UUID
    staff_number: str
    to_place: str
    position: str
    grade_level: int | None
    step: int | None
    appointment_type: str
    effective_date: date
    status: str
    reason: str | None
    reference: str | None
    requested_by: uuid.UUID | None
    decided_by: uuid.UUID | None
    decision_note: str | None


async def _current_primary(session: AsyncSession, staff: Staff) -> StaffPost | None:
    return (
        await session.execute(
            select(StaffPost)
            .where(
                StaffPost.staff_id == staff.id,
                StaffPost.is_primary.is_(True),
                StaffPost.end_date.is_(None),
            )
            .execution_options(**{PLATFORM_BYPASS: True})
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _posting_view(session: AsyncSession, r: PostingRequest) -> PostingView:
    staff = (
        await session.execute(
            select(Staff).where(Staff.id == r.staff_id).execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one()
    if r.to_institution_id:
        inst = (
            await session.execute(
                select(Institution)
                .where(Institution.id == r.to_institution_id)
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        ).scalar_one()
        place = inst.name
    else:
        place = (await governance.get_scope_node(session, r.to_scope_id)).name
    return PostingView(
        r.id,
        r.staff_id,
        staff.staff_number,
        place,
        r.position,
        r.grade_level,
        r.step,
        r.appointment_type,
        r.effective_date,
        r.status,
        r.reason,
        r.reference,
        r.requested_by,
        r.decided_by,
        r.decision_note,
    )


async def request_posting(
    session: AsyncSession, staff_id: uuid.UUID, data: PostingIn
) -> PostingView:
    """Propose a posting, transfer, promotion, acting appointment or secondment."""
    ctx = current_context()
    staff = await _staff(session, staff_id)
    if staff.status == StaffStatus.EXITED.value:
        raise Conflict("this staff member has left")
    try:
        kind = AppointmentType(data.appointment_type)
    except ValueError:
        raise ValidationFailed(f"'{data.appointment_type}' is not a kind of posting") from None
    if kind in (AppointmentType.FIRST_APPOINTMENT, AppointmentType.CONTRACT):
        raise ValidationFailed("a first appointment is made by appointing, not posting")
    current = await _current_primary(session, staff)
    if current is None:
        raise Conflict("this staff member has no current post")
    if data.institution_id is None and data.place_scope_id is None:
        place, inst = current.post_scope_id, None
        inst_id = current.institution_id
    else:
        place, inst = await _place(
            session, institution_id=data.institution_id, scope_id=data.place_scope_id
        )
        inst_id = inst.id if inst else None
    position = (data.position or current.position).strip()
    grade = data.grade_level if data.grade_level is not None else current.grade_level
    step = (
        data.step if data.step is not None else (current.step if data.grade_level is None else None)
    )
    structure = (data.salary_structure or current.salary_structure or "").upper() or None
    errors = _check_post(position, grade, step, structure)
    if data.effective_date <= current.start_date:
        errors.append("the effective date must be after the current post started")
    if kind is AppointmentType.PROMOTION and (
        grade is None or current.grade_level is None or grade <= current.grade_level
    ):
        errors.append("a promotion needs a higher grade level than the current one")
    if (
        kind in (AppointmentType.TRANSFER, AppointmentType.POSTING, AppointmentType.REDEPLOYMENT)
        and place == current.post_scope_id
    ):
        errors.append("a transfer or posting needs a different place of work")
    if errors:
        raise ValidationFailed("; ".join(errors))
    pending = (
        await session.execute(
            select(PostingRequest.id).where(
                PostingRequest.staff_id == staff.id, PostingRequest.status == "pending"
            )
        )
    ).first()
    if pending:
        raise Conflict(
            "a posting for this staff member is already waiting for a decision",
            next_action="Decide or cancel it first.",
        )
    r = PostingRequest(
        tenant_id=staff.tenant_id,
        scope_node_id=ctx.scope_node_id,
        staff_id=staff.id,
        to_scope_id=place,
        to_institution_id=inst_id,
        position=position,
        grade_level=grade,
        step=step,
        salary_structure=structure,
        appointment_type=kind.value,
        effective_date=data.effective_date,
        reason=(data.reason or "").strip()[:255] or None,
        reference=(data.reference or "").strip()[:60] or None,
        status="pending",
        requested_by=ctx.user_id,
    )
    session.add(r)
    await session.flush()
    _publish(session, staff, events.POSTING_REQUESTED, {"request_id": str(r.id)})
    await session.flush()
    return await _posting_view(session, r)


async def _request(session: AsyncSession, request_id: uuid.UUID) -> PostingRequest:
    r = (
        await session.execute(
            select(PostingRequest).where(PostingRequest.id == request_id).with_for_update()
        )
    ).scalar_one_or_none()
    if r is None:
        raise NotFound("posting request not found in your scope")
    if r.status != "pending":
        raise Conflict(f"this posting request is already {r.status}")
    return r


async def decide_posting(
    session: AsyncSession, request_id: uuid.UUID, *, approve: bool, note: str | None = None
) -> PostingView:
    """Approve (it takes effect) or reject. The requester cannot decide their own request."""
    ctx = current_context()
    r = await _request(session, request_id)
    if r.requested_by is not None and r.requested_by == ctx.user_id:
        raise Conflict(
            "someone other than the requester must decide a posting (four-eyes)",
            next_action="Ask another officer with posting rights to decide it.",
        )
    if not approve and not (note or "").strip():
        raise ValidationFailed("give a reason for rejecting the posting")
    staff = await _staff(session, r.staff_id, lock=True)
    r.status = "approved" if approve else "rejected"
    r.decided_by, r.decided_at = ctx.user_id, utcnow()
    r.decision_note = (note or "").strip()[:255] or None
    if approve:
        current = await _current_primary(session, staff)
        if current is None or r.effective_date <= current.start_date:
            raise Conflict("the staff member's posts have changed; make a new request")
        current.end_date = r.effective_date - timedelta(days=1)
        session.add(
            StaffPost(
                tenant_id=staff.tenant_id,
                scope_node_id=r.to_scope_id,
                staff_id=staff.id,
                post_scope_id=r.to_scope_id,
                institution_id=r.to_institution_id,
                position=r.position,
                grade_level=r.grade_level,
                step=r.step,
                salary_structure=r.salary_structure,
                is_primary=True,
                appointment_type=r.appointment_type,
                start_date=r.effective_date,
                reference=r.reference,
            )
        )
        await _move(session, staff, r.to_scope_id)
        _publish(
            session,
            staff,
            events.STAFF_POSTED,
            {"request_id": str(r.id), "appointment_type": r.appointment_type},
        )
    _publish(session, staff, events.POSTING_DECIDED, {"request_id": str(r.id), "status": r.status})
    await session.flush()
    return await _posting_view(session, r)


async def _move(session: AsyncSession, staff: Staff, scope: uuid.UUID) -> None:
    """The staff record, qualifications and registrations follow the person to their new place."""
    if staff.scope_node_id == scope:
        return
    staff.scope_node_id = scope
    for q in (
        await session.execute(
            select(StaffQualification).where(StaffQualification.staff_id == staff.id)
        )
    ).scalars():
        q.scope_node_id = scope
    for r in (
        await session.execute(
            select(TeacherRegistration).where(TeacherRegistration.staff_id == staff.id)
        )
    ).scalars():
        r.scope_node_id = scope


async def cancel_posting(session: AsyncSession, request_id: uuid.UUID) -> PostingView:
    ctx = current_context()
    r = await _request(session, request_id)
    if r.requested_by != ctx.user_id:
        raise Conflict("only the requester can cancel a posting request")
    r.status = "cancelled"
    await session.flush()
    return await _posting_view(session, r)


async def posting_requests(
    session: AsyncSession, *, status: str | None = "pending"
) -> list[PostingView]:
    stmt = select(PostingRequest).order_by(PostingRequest.id)
    if status:
        stmt = stmt.where(PostingRequest.status == status)
    return [await _posting_view(session, r) for r in (await session.execute(stmt)).scalars()]


# --------------------------------------------------------------------------- exits


async def end_employment(
    session: AsyncSession, staff_id: uuid.UUID, *, exit_date: date, reason: str
) -> StaffView:
    staff = await _staff(session, staff_id, lock=True)
    if staff.status == StaffStatus.EXITED.value:
        raise Conflict("this staff member has already left")
    try:
        why = ExitReason(reason)
    except ValueError:
        raise ValidationFailed(
            f"'{reason}' is not an exit reason",
            next_action="Use " + ", ".join(e.value for e in ExitReason) + ".",
        ) from None
    posts = (
        (
            await session.execute(
                select(StaffPost)
                .where(StaffPost.staff_id == staff.id, StaffPost.end_date.is_(None))
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        )
        .scalars()
        .all()
    )
    for p in posts:
        if exit_date < p.start_date:
            raise ValidationFailed("the exit date is before a current post started")
        p.end_date = exit_date
    for r in (
        await session.execute(
            select(PostingRequest).where(
                PostingRequest.staff_id == staff.id, PostingRequest.status == "pending"
            )
        )
    ).scalars():
        r.status = "cancelled"
    staff.status, staff.exit_date, staff.exit_reason = "exited", exit_date, why.value
    _publish(session, staff, events.STAFF_EXITED, {"reason": why.value})
    await session.flush()
    return await get_staff(session, staff.id)
