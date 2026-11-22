"""Learner registration: admissions, direct entry, class-list import, enrolment, classes,
promotion, subject and course registration, transfers (CEDA §4-§5, §17; ADR-029).

Four ways in, one way of enrolling:

* **Admission application:** submitted at the school (or online), reviewed, optionally assessed,
  offered (or waitlisted, or rejected), accepted by the family, then enrolled. A decision is due
  within 10 working days. The applicant becomes a person and a learner only on enrolment.
* **Direct entry:** the registrar enrols a pupil already sitting in a class.
* **Class-list import:** a class's register as CSV: previewed row by row (duplicates, ages,
  capacity), then committed.
* **Transfer:** the sending school asks; the receiving school accepts and places the learner.

Every route ends in ``_enrol``: the person is matched or created (Person 360 duplicate checks),
the learner record (LRN number) is found or created, guardians linked, the enrolment opened (one
active enrolment per learner), the age checked against the class year, and the learner placed in
a class within its capacity.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.academic import api as academic
from syntegra.academic.domain import TERTIARY_LEVELS, age_problem, next_year, year
from syntegra.academic.models import AcademicPeriod, ClassGroup
from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context, use_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import global_learner_id
from syntegra.governance import api as governance
from syntegra.institutions.models import Institution
from syntegra.learners import events
from syntegra.learners.models import (
    AdmissionApplication,
    ClassMembership,
    Enrolment,
    Learner,
    LearnerGuardian,
    LearnerTransfer,
    SubjectRegistration,
)
from syntegra.mdm import api as mdm
from syntegra.mdm.domain import ContactKind, Sex, normalize_contact
from syntegra.mdm.models import Person
from syntegra.platform.crypto import FieldCrypto
from syntegra.platform.models import BulkJob, BulkJobRow
from syntegra.platform.outbox import publish
from syntegra.platform.sequence import next_value
from syntegra.platform.session import PLATFORM_BYPASS

DECISION_DAYS = 10  # working days (CEDA §17), configurable later
OFFER_DAYS = 14
RELATIONSHIPS = (
    "mother",
    "father",
    "guardian",
    "grandparent",
    "sibling",
    "aunt_uncle",
    "foster_carer",
    "sponsor",
    "self",
    "other",
)
SSS_SUBJECTS = (8, 9)  # WAEC/NECO: eight or nine subjects
UNITS = (15, 24)  # tertiary: units a semester, configurable later
IMPORT_KIND = "class_list"
IMPORT_COLUMNS = (
    "surname",
    "first_name",
    "other_names",
    "sex",
    "date_of_birth",
    "class",
    "admission_number",
    "guardian_name",
    "guardian_phone",
    "guardian_relationship",
)
MAX_IMPORT_ROWS = 2000


def _bypass() -> dict[str, Any]:
    return {PLATFORM_BYPASS: True}


def _emit(row: Any, aggregate: str, event: str, **payload: Any) -> EventEnvelope:
    ctx = current_context()
    return EventEnvelope(
        event_type=event,
        tenant_id=row.tenant_id,
        scope_node_id=row.scope_node_id,
        aggregate_type=aggregate,
        aggregate_id=row.id,
        actor_id=ctx.user_id,
        correlation_id=ctx.correlation_id,
        payload=payload,
    )


def _publish(session: AsyncSession, row: Any, aggregate: str, event: str, **payload: Any) -> None:
    publish(session, _emit(row, aggregate, event, **payload))


def add_working_days(start: date, days: int) -> date:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


def _sex(value: str) -> Sex:
    v = value.strip().upper()[:1]
    if v not in ("F", "M"):
        raise ValidationFailed("sex is F or M")
    return Sex(v)


def _phone(value: str) -> str:
    try:
        return normalize_contact(ContactKind.PHONE, value)
    except ValueError as exc:
        raise ValidationFailed(f"the phone number {value!r} is not valid") from exc


def _names(given: str, family: str) -> tuple[str, str]:
    g, f = " ".join(given.split()), " ".join(family.split())
    if not g or not f:
        raise ValidationFailed("first name and surname are required")
    if len(g) > 100 or len(f) > 100:
        raise ValidationFailed("names are at most 100 characters")
    return g, f


def _relationship(value: str) -> str:
    v = value.strip().lower().replace(" ", "_").replace("/", "_")
    if v not in RELATIONSHIPS:
        raise ValidationFailed(f"relationship must be one of {', '.join(RELATIONSHIPS)}")
    return v


# --------------------------------------------------------------------------- people


@dataclass(frozen=True, slots=True)
class PupilIn:
    """Who is being enrolled, and their main guardian."""

    given_name: str
    family_name: str
    date_of_birth: date
    sex: str
    guardian_name: str
    guardian_phone: str
    guardian_relationship: str
    other_names: str | None = None
    guardian_email: str | None = None
    person_id: uuid.UUID | None = None  # the person is already registered (e.g. a sibling)
    not_duplicate_of: tuple[uuid.UUID, ...] = ()  # confirmed different people
    override_reason: str | None = None


async def _person_for(session: AsyncSession, crypto: FieldCrypto, p: PupilIn) -> uuid.UUID:
    if p.person_id:
        await mdm.get_person(session, p.person_id)  # must exist and be visible
        return p.person_id
    given, family = _names(p.given_name, p.family_name)
    if p.date_of_birth >= today():
        raise ValidationFailed("the date of birth is not in the past")
    data = mdm.PersonIn(
        given_name=given,
        family_name=family,
        other_names=(p.other_names or "").strip() or None,
        date_of_birth=p.date_of_birth,
        sex_at_registration=_sex(p.sex),
        source_system="learners",
    )
    await _same_child(session, crypto, data, p)
    r = await mdm.create_person(
        session,
        crypto,
        data,
        not_duplicate_of=p.not_duplicate_of,
        override_reason=p.override_reason,
    )
    return r.person_id


async def _same_child(
    session: AsyncSession, crypto: FieldCrypto, data: mdm.PersonIn, p: PupilIn
) -> None:
    """A child's own record has no phone, so Person 360 scores a re-registration only on name
    and birth date (review band). For learners the guardian's phone is the deciding signal:
    the same name, date of birth and guardian phone is the same child (CEDA §4)."""
    from syntegra.mdm.models import PersonContact

    phone = _phone(p.guardian_phone)
    for c in await mdm.find_candidates(session, crypto, data):
        if not c.visible or c.person_id in p.not_duplicate_of or c.score < 60:
            continue
        guardians = (
            select(LearnerGuardian.guardian_person_id)
            .join(Learner, Learner.id == LearnerGuardian.learner_id)
            .where(Learner.person_id == c.person_id)
        )
        shared = (
            await session.execute(
                select(PersonContact.id)
                .where(PersonContact.person_id.in_(guardians), PersonContact.value == phone)
                .execution_options(**_bypass())
            )
        ).first()
        if shared:
            raise mdm.DuplicateBlocked(
                "this child is already registered (same name, date of birth and guardian phone)",
                next_action="Use the existing record. If this really is a different child, "
                "confirm that with a reason.",
                candidates=[
                    {
                        "visible": True,
                        "person_id": str(c.person_id),
                        "score": max(c.score, 85),
                        "display_name": c.display_name,
                        "date_of_birth": c.date_of_birth.isoformat() if c.date_of_birth else None,
                        "reasons": [*c.reasons, {"code": "guardian_phone", "points": 20}],
                    }
                ],
            )


async def _guardian_person(
    session: AsyncSession, crypto: FieldCrypto, name: str, phone: str, email: str | None
) -> uuid.UUID:
    """The guardian's person record: matched when already registered (e.g. a sibling's
    parent), created otherwise."""
    parts = " ".join(name.split()).split(" ")
    if len(parts) < 2:
        raise ValidationFailed("give the guardian's first name and surname")
    contacts = [mdm.ContactIn(ContactKind.PHONE, _phone(phone), True)]
    if email:
        contacts.append(mdm.ContactIn(ContactKind.EMAIL, email.strip().lower()))
    data = mdm.PersonIn(
        given_name=" ".join(parts[:-1]),
        family_name=parts[-1],
        contacts=tuple(contacts),
        source_system="learners",
    )
    # A parent of another learner: the same phone and surname is the same guardian (siblings
    # share one record instead of each creating a copy of the parent).
    from syntegra.mdm.domain import normalize_name
    from syntegra.mdm.models import PersonContact

    for person in (
        await session.execute(
            select(Person)
            .join(PersonContact, PersonContact.person_id == Person.id)
            .where(
                PersonContact.kind == "phone",
                PersonContact.value == contacts[0].value,
                Person.id.in_(select(LearnerGuardian.guardian_person_id)),
                Person.status == "active",
            )
            .execution_options(**_bypass())
        )
    ).scalars():
        if normalize_name(person.family_name) == normalize_name(parts[-1]):
            return person.id
    try:
        return (await mdm.create_person(session, crypto, data)).person_id
    except mdm.DuplicateBlocked as dup:
        seen = [c for c in dup.extra.get("candidates", []) if c.get("visible")]
        if seen:
            return uuid.UUID(seen[0]["person_id"])
        raise


async def _learner_for(session: AsyncSession, person_id: uuid.UUID, scope_id: uuid.UUID) -> Learner:
    ctx = current_context()
    found = (
        await session.execute(
            select(Learner)
            .where(Learner.tenant_id == ctx.tenant_id, Learner.person_id == person_id)
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()
    if found:
        return found
    seq = await next_value(session, ctx.tenant_id, "learner")
    learner = Learner(
        tenant_id=ctx.tenant_id,
        scope_node_id=scope_id,
        person_id=person_id,
        learner_number=global_learner_id(seq),
        status="not_enrolled",
    )
    session.add(learner)
    await session.flush()
    return learner


async def _link_guardian(
    session: AsyncSession, learner: Learner, guardian_id: uuid.UUID, relationship: str
) -> None:
    have = (
        await session.execute(
            select(LearnerGuardian.id)
            .where(
                LearnerGuardian.learner_id == learner.id,
                LearnerGuardian.guardian_person_id == guardian_id,
            )
            .execution_options(**_bypass())
        )
    ).first()
    if have:
        return
    n = (
        await session.execute(
            select(func.count())
            .select_from(LearnerGuardian)
            .where(LearnerGuardian.learner_id == learner.id)
            .execution_options(**_bypass())
        )
    ).scalar_one()
    session.add(
        LearnerGuardian(
            tenant_id=learner.tenant_id,
            scope_node_id=learner.scope_node_id,
            learner_id=learner.id,
            guardian_person_id=guardian_id,
            relationship=relationship,
            contact_priority=min(int(n) + 1, 9),
            valid_from=today(),
        )
    )


# --------------------------------------------------------------------------- enrolment


@dataclass(frozen=True, slots=True)
class EnrolmentView:
    id: uuid.UUID
    learner_id: uuid.UUID
    learner_number: str
    learner_name: str
    institution_id: uuid.UUID
    institution_name: str
    session_id: uuid.UUID
    session_name: str
    year_code: str
    year_name: str
    class_id: uuid.UUID | None
    class_name: str | None
    status: str
    source: str
    admission_number: str | None
    valid_from: date
    valid_to: date | None
    exit_reason: str | None
    age_override: str | None


async def _person(session: AsyncSession, person_id: uuid.UUID) -> Person:
    p = (
        await session.execute(
            select(Person).where(Person.id == person_id).execution_options(**_bypass())
        )
    ).scalar_one()
    return p


def _full(p: Person) -> str:
    return " ".join(x for x in (p.given_name, p.other_names, p.family_name) if x)


async def _current_class(session: AsyncSession, enrolment_id: uuid.UUID) -> ClassGroup | None:
    m = (
        await session.execute(
            select(ClassMembership)
            .where(ClassMembership.enrolment_id == enrolment_id, ClassMembership.valid_to.is_(None))
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()
    if m is None:
        return None
    return (
        await session.execute(
            select(ClassGroup)
            .where(ClassGroup.id == m.class_group_id)
            .execution_options(**_bypass())
        )
    ).scalar_one()


async def _eview(session: AsyncSession, e: Enrolment) -> EnrolmentView:
    learner = (
        await session.execute(
            select(Learner).where(Learner.id == e.learner_id).execution_options(**_bypass())
        )
    ).scalar_one()
    person = await _person(session, learner.person_id)
    inst = (
        await session.execute(
            select(Institution)
            .where(Institution.id == e.institution_id)
            .execution_options(**_bypass())
        )
    ).scalar_one()
    sess = (
        await session.execute(
            select(AcademicPeriod)
            .where(AcademicPeriod.id == e.session_id)
            .execution_options(**_bypass())
        )
    ).scalar_one()
    c = await _current_class(session, e.id) if e.status == "active" else None
    return EnrolmentView(
        e.id,
        learner.id,
        learner.learner_number,
        _full(person),
        inst.id,
        inst.name,
        sess.id,
        sess.name,
        e.year_code,
        year(e.year_code).name,
        c.id if c else None,
        c.name if c else None,
        e.status,
        e.source,
        e.admission_number,
        e.valid_from,
        e.valid_to,
        e.exit_reason,
        e.age_override,
    )


async def _members(session: AsyncSession, class_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(ClassMembership)
                .where(
                    ClassMembership.class_group_id == class_id, ClassMembership.valid_to.is_(None)
                )
                .execution_options(**_bypass())
            )
        ).scalar_one()
    )


async def class_sizes(session: AsyncSession, class_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not class_ids:
        return {}
    rows = await session.execute(
        select(ClassMembership.class_group_id, func.count())
        .where(ClassMembership.class_group_id.in_(class_ids), ClassMembership.valid_to.is_(None))
        .group_by(ClassMembership.class_group_id)
        .execution_options(**_bypass())
    )
    sizes = {k: int(v) for k, v in rows.all()}
    return {c: sizes.get(c, 0) for c in class_ids}


async def _place(
    session: AsyncSession, e: Enrolment, class_id: uuid.UUID, *, on: date | None = None
) -> ClassGroup:
    c = await academic.get_class(session, class_id)
    if c.institution_id != e.institution_id or c.session_id != e.session_id:
        raise ValidationFailed("the class is in another school or session")
    if c.year_code != e.year_code:
        raise ValidationFailed(f"{c.name} is not a {year(e.year_code).name} class")
    if c.status != "active":
        raise Conflict(f"{c.name} is closed")
    current = (
        await session.execute(
            select(ClassMembership).where(
                ClassMembership.enrolment_id == e.id, ClassMembership.valid_to.is_(None)
            )
        )
    ).scalar_one_or_none()
    if current and current.class_group_id == c.id:
        return c
    if await _members(session, c.id) >= c.capacity:
        raise Conflict(
            f"{c.name} is full ({c.capacity})",
            next_action="Choose another arm, or raise the class capacity.",
        )
    start = on or today()
    if current:
        current.valid_to = max(current.valid_from, start - timedelta(days=1))
    session.add(
        ClassMembership(
            tenant_id=e.tenant_id,
            scope_node_id=e.scope_node_id,
            enrolment_id=e.id,
            learner_id=e.learner_id,
            class_group_id=c.id,
            valid_from=start,
        )
    )
    await session.flush()
    return c


async def _active(session: AsyncSession, learner_id: uuid.UUID) -> Enrolment | None:
    return (
        await session.execute(
            select(Enrolment)
            .where(Enrolment.learner_id == learner_id, Enrolment.status == "active")
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class EnrolIn:
    session_id: uuid.UUID
    year_code: str
    class_id: uuid.UUID | None = None
    admission_number: str | None = None
    starts_on: date | None = None
    age_override: str | None = None  # why a learner outside the usual age is enrolled
    mode: str = "full_time"


async def _enrol(
    session: AsyncSession,
    crypto: FieldCrypto,
    inst: Institution,
    pupil: PupilIn,
    data: EnrolIn,
    *,
    source: str,
    application_id: uuid.UUID | None = None,
) -> Enrolment:
    sess = await academic.get_session(session, data.session_id)
    try:
        y = year(data.year_code)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    if y.level not in await academic.institution_levels(session, inst.id):
        raise ValidationFailed(f"{inst.name} does not run {y.name}")
    problem = age_problem(pupil.date_of_birth, y, sess.starts_on)
    reason = (data.age_override or "").strip() or None
    if problem and not reason:
        raise ValidationFailed(
            problem, next_action="Give the reason (late start, repeating, special needs) to enrol."
        )
    if data.mode not in ("full_time", "part_time", "distance"):
        raise ValidationFailed("mode is full_time, part_time or distance")
    person_id = await _person_for(session, crypto, pupil)
    learner = await _learner_for(session, person_id, inst.scope_node_id)
    existing = await _active(session, learner.id)
    if existing:
        where = (
            "this school"
            if existing.institution_id == inst.id
            else "another school (ask it to transfer the learner)"
        )
        raise Conflict(f"{learner.learner_number} is already enrolled at {where}")
    guardian = await _guardian_person(
        session, crypto, pupil.guardian_name, pupil.guardian_phone, pupil.guardian_email
    )
    await _link_guardian(session, learner, guardian, _relationship(pupil.guardian_relationship))
    start = data.starts_on or max(today(), sess.starts_on)
    e = Enrolment(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        learner_id=learner.id,
        institution_id=inst.id,
        session_id=sess.id,
        year_code=y.code,
        status="active",
        mode=data.mode,
        source=source,
        admission_number=(data.admission_number or "").strip() or None,
        valid_from=start,
        application_id=application_id,
        age_override=reason if problem else None,
        active_key=str(learner.id),
    )
    session.add(e)
    learner.status = "enrolled"
    learner.scope_node_id = inst.scope_node_id
    learner.first_enrolled_on = learner.first_enrolled_on or start
    await session.flush()
    if data.class_id:
        await _place(session, e, data.class_id, on=start)
    _publish(session, e, "enrolment", events.ENROLMENT_STARTED, source=source)
    return e


async def register_direct(
    session: AsyncSession,
    crypto: FieldCrypto,
    institution_id: uuid.UUID,
    pupil: PupilIn,
    data: EnrolIn,
) -> EnrolmentView:
    """Enrol a pupil the school already teaches (no application)."""
    inst = await academic.institution(session, institution_id)
    e = await _enrol(session, crypto, inst, pupil, data, source="direct")
    return await _eview(session, e)


async def _enrolment(session: AsyncSession, enrolment_id: uuid.UUID) -> Enrolment:
    e = (
        await session.execute(select(Enrolment).where(Enrolment.id == enrolment_id))
    ).scalar_one_or_none()
    if e is None:
        raise NotFound("enrolment not found")
    return e


async def get_enrolment(session: AsyncSession, enrolment_id: uuid.UUID) -> EnrolmentView:
    return await _eview(session, await _enrolment(session, enrolment_id))


async def place_in_class(
    session: AsyncSession, enrolment_id: uuid.UUID, class_id: uuid.UUID
) -> EnrolmentView:
    """Put a learner in a class, or move them to another arm of the same year."""
    e = await _enrolment(session, enrolment_id)
    if e.status != "active":
        raise Conflict(f"this enrolment is {e.status.replace('_', ' ')}")
    await _place(session, e, class_id)
    _publish(session, e, "enrolment", events.ENROLMENT_CHANGED, change="class")
    return await _eview(session, e)


async def end_enrolment(
    session: AsyncSession,
    enrolment_id: uuid.UUID,
    *,
    status: str,
    reason: str,
    on: date | None = None,
) -> EnrolmentView:
    """Withdraw a learner, or record that they completed the level."""
    if status not in ("withdrawn", "graduated", "completed"):
        raise ValidationFailed("status is withdrawn, graduated or completed")
    why = " ".join(reason.split())
    if not why:
        raise ValidationFailed("give the reason")
    e = await _enrolment(session, enrolment_id)
    if e.status != "active":
        raise Conflict(f"this enrolment is already {e.status.replace('_', ' ')}")
    end = on or today()
    e.status, e.valid_to, e.exit_reason, e.active_key = (
        status,
        max(end, e.valid_from),
        why[:255],
        None,
    )
    for m in (
        await session.execute(
            select(ClassMembership).where(
                ClassMembership.enrolment_id == e.id, ClassMembership.valid_to.is_(None)
            )
        )
    ).scalars():
        m.valid_to = max(m.valid_from, end)
    learner = (
        await session.execute(select(Learner).where(Learner.id == e.learner_id))
    ).scalar_one()
    learner.status = "completed" if status == "graduated" else "not_enrolled"
    await session.flush()
    _publish(session, e, "enrolment", events.ENROLMENT_ENDED, status=status)
    return await _eview(session, e)


# --------------------------------------------------------------------------- classes and promotion


@dataclass(frozen=True, slots=True)
class MemberView:
    enrolment_id: uuid.UUID
    learner_id: uuid.UUID
    learner_number: str
    surname: str
    first_name: str
    other_names: str | None
    sex: str
    date_of_birth: date | None
    admission_number: str | None
    since: date


async def class_list(session: AsyncSession, class_id: uuid.UUID) -> list[MemberView]:
    c = await academic.get_class(session, class_id)
    rows = (
        await session.execute(
            select(ClassMembership, Enrolment, Learner, Person)
            .join(Enrolment, Enrolment.id == ClassMembership.enrolment_id)
            .join(Learner, Learner.id == ClassMembership.learner_id)
            .join(Person, Person.id == Learner.person_id)
            .where(ClassMembership.class_group_id == c.id, ClassMembership.valid_to.is_(None))
            .order_by(Person.family_name, Person.given_name)
            .execution_options(**_bypass())
        )
    ).all()
    return [
        MemberView(
            e.id,
            ln.id,
            ln.learner_number,
            p.family_name,
            p.given_name,
            p.other_names,
            p.sex_at_registration,
            p.date_of_birth,
            e.admission_number,
            m.valid_from,
        )
        for m, e, ln, p in rows
    ]


@dataclass(frozen=True, slots=True)
class PromotionResult:
    promoted: int
    repeated: int
    to_class: str
    repeat_class: str | None


async def promote(
    session: AsyncSession,
    from_class_id: uuid.UUID,
    to_class_id: uuid.UUID,
    *,
    repeat_class_id: uuid.UUID | None = None,
    repeaters: list[uuid.UUID] | None = None,
) -> PromotionResult:
    """End-of-session promotion: every learner in the class moves to the next year's class in a
    later session; those named in ``repeaters`` go to ``repeat_class_id`` (same year)."""
    src = await academic.get_class(session, from_class_id)
    dst = await academic.get_class(session, to_class_id)
    nxt = next_year(src.year_code)
    if nxt is None:
        raise ValidationFailed(f"{src.name} is the last year: record the learners as graduated")
    if dst.institution_id != src.institution_id or dst.year_code != nxt.code:
        raise ValidationFailed(f"promote {src.name} into a {nxt.name} class of the same school")
    s_from = await academic.get_session(session, src.session_id)
    s_to = await academic.get_session(session, dst.session_id)
    if s_to.starts_on <= s_from.starts_on:
        raise ValidationFailed("promotion is into a later session")
    rep = None
    if repeaters:
        if repeat_class_id is None:
            raise ValidationFailed("choose the class repeaters go into")
        rep = await academic.get_class(session, repeat_class_id)
        if rep.year_code != src.year_code or rep.session_id != dst.session_id:
            raise ValidationFailed(
                f"repeaters go into a {year(src.year_code).name} class of {s_to.name}"
            )
    members = await class_list(session, src.id)
    stay = set(repeaters or [])
    unknown = stay - {m.learner_id for m in members}
    if unknown:
        raise ValidationFailed("some repeaters are not in this class")
    going = [m for m in members if m.learner_id not in stay]
    if await _members(session, dst.id) + len(going) > dst.capacity:
        raise Conflict(
            f"{dst.name} cannot take {len(going)} more learners (capacity {dst.capacity})"
        )
    done = {"promoted": 0, "repeated": 0}
    for m in members:
        old = await _enrolment(session, m.enrolment_id)
        target = rep if m.learner_id in stay else dst
        assert target is not None
        old.status, old.valid_to, old.active_key = (
            "completed",
            max(old.valid_from, s_from.ends_on),
            None,
        )
        for cm in (
            await session.execute(
                select(ClassMembership).where(
                    ClassMembership.enrolment_id == old.id, ClassMembership.valid_to.is_(None)
                )
            )
        ).scalars():
            cm.valid_to = max(cm.valid_from, s_from.ends_on)
        await session.flush()
        new = Enrolment(
            tenant_id=old.tenant_id,
            scope_node_id=old.scope_node_id,
            learner_id=old.learner_id,
            institution_id=old.institution_id,
            session_id=target.session_id,
            year_code=target.year_code,
            status="active",
            mode=old.mode,
            source="promotion",
            admission_number=old.admission_number,
            valid_from=s_to.starts_on,
            previous_id=old.id,
            active_key=str(old.learner_id),
        )
        session.add(new)
        await session.flush()
        await _place(session, new, target.id, on=s_to.starts_on)
        done["repeated" if m.learner_id in stay else "promoted"] += 1
    return PromotionResult(done["promoted"], done["repeated"], dst.name, rep.name if rep else None)


# --------------------------------------------------------------------------- admission applications


@dataclass(frozen=True, slots=True)
class ApplicationIn:
    session_id: uuid.UUID
    year_code: str
    given_name: str
    family_name: str
    date_of_birth: date
    sex: str
    guardian_name: str
    guardian_phone: str
    guardian_relationship: str
    other_names: str | None = None
    guardian_email: str | None = None
    previous_school: str | None = None


@dataclass(frozen=True, slots=True)
class ApplicationView:
    id: uuid.UUID
    reference: str
    institution_id: uuid.UUID
    session_id: uuid.UUID
    year_code: str
    year_name: str
    status: str
    channel: str
    applicant_name: str
    date_of_birth: date
    sex: str
    previous_school: str | None
    guardian_name: str
    guardian_phone: str
    guardian_relationship: str
    assessment_score: Any
    assessment_note: str | None
    decision_note: str | None
    offer_expires_on: date | None
    due_by: date
    overdue: bool
    age_note: str | None
    enrolment_id: uuid.UUID | None
    history: list[dict[str, Any]] = field(default_factory=list)


OPEN = ("submitted", "under_review", "assessment", "offered", "waitlisted", "accepted")
UNDECIDED = ("submitted", "under_review", "assessment")


async def _aview(session: AsyncSession, a: AdmissionApplication) -> ApplicationView:
    y = year(a.year_code)
    sess = await academic.get_session(session, a.session_id)
    return ApplicationView(
        a.id,
        a.reference,
        a.institution_id,
        a.session_id,
        y.code,
        y.name,
        a.status,
        a.channel,
        " ".join(x for x in (a.given_name, a.other_names, a.family_name) if x),
        a.date_of_birth,
        a.sex,
        a.previous_school,
        a.guardian_name,
        a.guardian_phone,
        a.guardian_relationship,
        a.assessment_score,
        a.assessment_note,
        a.decision_note,
        a.offer_expires_on,
        a.due_by,
        a.status in UNDECIDED and a.due_by < today(),
        age_problem(a.date_of_birth, y, sess.starts_on),
        a.enrolment_id,
        list(a.history or []),
    )


def _log(a: AdmissionApplication, status: str, note: str | None = None) -> None:
    ctx = current_context()
    a.history = [
        *(a.history or []),
        {
            "at": utcnow().isoformat(timespec="seconds"),
            "status": status,
            "by": str(ctx.user_id) if ctx.user_id else None,
            "note": note,
        },
    ]


async def submit_application(
    session: AsyncSession,
    institution_id: uuid.UUID,
    data: ApplicationIn,
    *,
    channel: str = "school",
) -> ApplicationView:
    inst = await academic.institution(session, institution_id)
    sess = await academic.get_session(session, data.session_id)
    try:
        y = year(data.year_code)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    if y.level not in await academic.institution_levels(session, inst.id):
        raise ValidationFailed(f"{inst.name} does not admit into {y.name}")
    given, family = _names(data.given_name, data.family_name)
    if data.date_of_birth >= today():
        raise ValidationFailed("the date of birth is not in the past")
    guardian = " ".join(data.guardian_name.split())
    if len(guardian.split(" ")) < 2:
        raise ValidationFailed("give the parent or guardian's first name and surname")
    ctx = current_context()
    seq = await next_value(session, ctx.tenant_id, f"adm.{today().year}")
    a = AdmissionApplication(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        reference=f"ADM-{today().year}-{seq:06d}",
        institution_id=inst.id,
        session_id=sess.id,
        year_code=y.code,
        status="submitted",
        channel=channel,
        given_name=given,
        family_name=family,
        other_names=(data.other_names or "").strip() or None,
        date_of_birth=data.date_of_birth,
        sex=_sex(data.sex).value,
        previous_school=(data.previous_school or "").strip() or None,
        guardian_name=guardian,
        guardian_phone=_phone(data.guardian_phone),
        guardian_email=(data.guardian_email or "").strip().lower() or None,
        guardian_relationship=_relationship(data.guardian_relationship),
        due_by=add_working_days(today(), DECISION_DAYS),
        history=[],
    )
    _log(a, "submitted", f"via {channel}")
    session.add(a)
    await session.flush()
    _publish(session, a, "admission_application", events.APPLICATION_SUBMITTED)
    return await _aview(session, a)


async def _application(session: AsyncSession, application_id: uuid.UUID) -> AdmissionApplication:
    a = (
        await session.execute(
            select(AdmissionApplication).where(AdmissionApplication.id == application_id)
        )
    ).scalar_one_or_none()
    if a is None:
        raise NotFound("application not found")
    return a


async def get_application(session: AsyncSession, application_id: uuid.UUID) -> ApplicationView:
    return await _aview(session, await _application(session, application_id))


async def applications(
    session: AsyncSession, institution_id: uuid.UUID, *, status: str | None = None
) -> list[ApplicationView]:
    await academic.institution(session, institution_id)
    q = select(AdmissionApplication).where(AdmissionApplication.institution_id == institution_id)
    if status == "open":
        q = q.where(AdmissionApplication.status.in_(OPEN))
    elif status:
        q = q.where(AdmissionApplication.status == status)
    rows = (await session.execute(q.order_by(AdmissionApplication.reference))).scalars()
    return [await _aview(session, a) for a in rows]


# action → (allowed from, to, decision?)
MOVES: dict[str, tuple[tuple[str, ...], str, bool]] = {
    "review": (("submitted",), "under_review", False),
    "assess": (("under_review", "assessment"), "assessment", False),
    "offer": (("under_review", "assessment", "waitlisted"), "offered", True),
    "waitlist": (("under_review", "assessment"), "waitlisted", True),
    "reject": (("under_review", "assessment", "waitlisted"), "rejected", True),
    "accept": (("offered",), "accepted", False),
    "decline": (("offered",), "declined", False),
    "withdraw": (OPEN, "withdrawn", False),
}
DECISIONS = frozenset(k for k, v in MOVES.items() if v[2])


async def move_application(
    session: AsyncSession,
    application_id: uuid.UUID,
    action: str,
    *,
    note: str | None = None,
    score: Any = None,
    offer_days: int = OFFER_DAYS,
) -> ApplicationView:
    a = await _application(session, application_id)
    if action not in MOVES:
        raise ValidationFailed(f"action is one of {', '.join(MOVES)}")
    allowed, to, decision = MOVES[action]
    if a.status not in allowed:
        raise Conflict(
            f"this application is {a.status.replace('_', ' ')}; "
            f"it cannot be {to.replace('_', ' ')} now"
        )
    text = " ".join((note or "").split()) or None
    if action == "assess":
        if score is None:
            raise ValidationFailed("give the assessment score (0-100)")
        from decimal import Decimal, InvalidOperation

        try:
            s = Decimal(str(score))
        except InvalidOperation as exc:
            raise ValidationFailed("the score is a number") from exc
        if not Decimal(0) <= s <= Decimal(100):
            raise ValidationFailed("the score is 0-100")
        a.assessment_score, a.assessment_note = s.quantize(Decimal("0.01")), text
    if action == "reject" and not text:
        raise ValidationFailed("give the reason; the family is told")
    if action == "accept" and a.offer_expires_on and a.offer_expires_on < today():
        a.status = "expired"
        _log(a, "expired")
        await session.flush()
        raise Conflict(
            "the offer has expired", next_action="Make a new offer if a place is still free."
        )
    if action == "offer":
        if not 1 <= offer_days <= 60:
            raise ValidationFailed("an offer stays open 1-60 days")
        a.offer_expires_on = today() + timedelta(days=offer_days)
    if decision:
        ctx = current_context()
        a.decided_by, a.decided_at, a.decision_note = ctx.user_id, utcnow(), text
    a.status = to
    _log(a, to, text)
    await session.flush()
    if decision:
        _publish(session, a, "admission_application", events.APPLICATION_DECIDED, decision=to)
    return await _aview(session, a)


@dataclass(frozen=True, slots=True)
class AdmitIn:
    class_id: uuid.UUID | None = None
    admission_number: str | None = None
    age_override: str | None = None
    person_id: uuid.UUID | None = None
    not_duplicate_of: tuple[uuid.UUID, ...] = ()
    override_reason: str | None = None


async def enrol_applicant(
    session: AsyncSession, crypto: FieldCrypto, application_id: uuid.UUID, data: AdmitIn
) -> EnrolmentView:
    """The last step: an accepted applicant becomes a learner, enrolled and placed in a class."""
    a = await _application(session, application_id)
    if a.status != "accepted":
        raise Conflict(
            "only an accepted offer is enrolled",
            next_action="Record the family's acceptance first.",
        )
    inst = await academic.institution(session, a.institution_id)
    pupil = PupilIn(
        given_name=a.given_name,
        family_name=a.family_name,
        other_names=a.other_names,
        date_of_birth=a.date_of_birth,
        sex=a.sex,
        guardian_name=a.guardian_name,
        guardian_phone=a.guardian_phone,
        guardian_email=a.guardian_email,
        guardian_relationship=a.guardian_relationship,
        person_id=data.person_id,
        not_duplicate_of=data.not_duplicate_of,
        override_reason=data.override_reason,
    )
    e = await _enrol(
        session,
        crypto,
        inst,
        pupil,
        EnrolIn(
            session_id=a.session_id,
            year_code=a.year_code,
            class_id=data.class_id,
            admission_number=data.admission_number,
            age_override=data.age_override,
        ),
        source="admission",
        application_id=a.id,
    )
    learner = (
        await session.execute(select(Learner).where(Learner.id == e.learner_id))
    ).scalar_one()
    a.status, a.person_id, a.enrolment_id = "enrolled", learner.person_id, e.id
    _log(a, "enrolled")
    await session.flush()
    return await _eview(session, e)


# --------------------------------------------------------------------------- class-list import


def import_template() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(IMPORT_COLUMNS)
    w.writerow(
        [
            "Eze",
            "Chidi",
            "Emmanuel",
            "M",
            "2016-05-01",
            "Primary 4A",
            "2016/0142",
            "Ngozi Eze",
            "08031234567",
            "mother",
        ]
    )
    return buf.getvalue()


def _parse_date(value: str) -> date:
    v = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"date of birth {value!r}: use 2016-05-01 or 01/05/2016")


def _parse(csv_text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("﻿")))
    headers = {(h or "").strip().lower().replace(" ", "_"): h for h in reader.fieldnames or []}
    missing = [
        c
        for c in IMPORT_COLUMNS
        if c not in headers and c not in ("other_names", "admission_number")
    ]
    if missing:
        raise ValidationFailed(
            f"these columns are missing: {', '.join(missing)}", next_action="Use the template."
        )
    rows = [
        {k: (raw.get(headers[k]) or "").strip() for k in IMPORT_COLUMNS if k in headers}
        for raw in reader
    ]
    if not rows:
        raise ValidationFailed("the file has a header but no pupils")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValidationFailed(
            f"at most {MAX_IMPORT_ROWS} pupils per file", next_action="Split it by class."
        )
    return rows


async def preview_import(
    session: AsyncSession,
    crypto: FieldCrypto,
    institution_id: uuid.UUID,
    session_id: uuid.UUID,
    csv_text: str,
    *,
    source_name: str | None = None,
) -> uuid.UUID:
    """Check every row (names, sex, dates, class, capacity, duplicates) without registering."""
    ctx = current_context()
    inst = await academic.institution(session, institution_id)
    sess = await academic.get_session(session, session_id)
    by_name = {
        c.name.lower(): c
        for c in await academic.classes(session, inst.id, sess.id)
        if c.status == "active"
    }
    sizes = await class_sizes(session, [c.id for c in by_name.values()])
    rows = _parse(csv_text)
    job = BulkJob(
        tenant_id=ctx.tenant_id,
        scope_node_id=inst.scope_node_id,
        kind=IMPORT_KIND,
        status="previewed",
        source_name=source_name,
        summary={},
        created_by=ctx.user_id,
    )
    session.add(job)
    await session.flush()
    valid = errors = 0
    seen: set[tuple[str, str, str]] = set()
    for n, r in enumerate(rows, start=2):
        msgs: list[str] = []
        clean: dict[str, Any] = dict(r)
        try:
            given, family = _names(r.get("first_name", ""), r.get("surname", ""))
            clean["first_name"], clean["surname"] = given, family
        except ValidationFailed as e:
            msgs.append(e.detail)
        try:
            clean["sex"] = _sex(r.get("sex", "")).value
        except ValidationFailed as e:
            msgs.append(e.detail)
        born = None
        try:
            born = _parse_date(r.get("date_of_birth", ""))
            clean["date_of_birth"] = born.isoformat()
        except ValueError as e:
            msgs.append(str(e))
        cls = by_name.get(" ".join(r.get("class", "").split()).lower())
        if cls is None:
            msgs.append(f"class {r.get('class')!r} is not open for {sess.name}")
        else:
            clean["class_id"] = str(cls.id)
            sizes[cls.id] = sizes.get(cls.id, 0) + 1
            if sizes[cls.id] > cls.capacity:
                msgs.append(f"{cls.name} would be over its capacity of {cls.capacity}")
            if born:
                problem = age_problem(born, year(cls.year_code), sess.starts_on)
                if problem:
                    clean["age_note"] = problem  # an existing pupil: recorded, not refused
        try:
            clean["guardian_phone"] = _phone(r.get("guardian_phone", ""))
            clean["guardian_relationship"] = _relationship(
                r.get("guardian_relationship", "") or "guardian"
            )
            if len(" ".join(r.get("guardian_name", "").split()).split(" ")) < 2:
                msgs.append("give the guardian's first name and surname")
        except ValidationFailed as e:
            msgs.append(e.detail)
        key = (
            clean.get("surname", "").lower(),
            clean.get("first_name", "").lower(),
            clean.get("date_of_birth", ""),
        )
        if key in seen:
            msgs.append("the same pupil appears twice in the file")
        seen.add(key)
        if not msgs and born:
            found = await mdm.find_candidates(
                session,
                crypto,
                mdm.PersonIn(
                    given_name=clean["first_name"],
                    family_name=clean["surname"],
                    date_of_birth=born,
                    sex_at_registration=Sex(clean["sex"]),
                ),
            )
            block = [c for c in found if c.outcome.value == "block"]
            if block:
                who = block[0].display_name or "someone registered elsewhere"
                msgs.append(f"may already be registered ({who}); register this pupil on their own")
        status = "error" if msgs else "valid"
        valid += status == "valid"
        errors += status == "error"
        session.add(BulkJobRow(job_id=job.id, row_no=n, data=clean, status=status, messages=msgs))
    job.summary = {
        "rows": len(rows),
        "valid": valid,
        "errors": errors,
        "institution_id": str(inst.id),
        "session_id": str(sess.id),
    }
    await session.flush()
    return job.id


async def import_job(session: AsyncSession, job_id: uuid.UUID) -> tuple[BulkJob, list[BulkJobRow]]:
    ctx = current_context()
    job = (
        await session.execute(
            select(BulkJob).where(BulkJob.id == job_id, BulkJob.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if (
        job is None
        or job.kind != IMPORT_KIND
        or not await governance.is_within(session, ctx.scope_node_id, job.scope_node_id)
    ):
        raise NotFound("import not found")
    rows = list(
        (
            await session.execute(
                select(BulkJobRow).where(BulkJobRow.job_id == job.id).order_by(BulkJobRow.row_no)
            )
        ).scalars()
    )
    return job, rows


async def commit_import(
    session: AsyncSession, crypto: FieldCrypto, job_id: uuid.UUID
) -> dict[str, Any]:
    """Enrol every valid row into its class. A row that now fails (enrolled since the preview,
    class full) is skipped with the reason; the others still go in."""
    ctx = current_context()
    job, rows = await import_job(session, job_id)
    if job.status != "previewed":
        raise Conflict("this import has already been committed", next_action="Start a new import.")
    inst = await academic.institution(session, uuid.UUID(job.summary["institution_id"]))
    session_id = uuid.UUID(job.summary["session_id"])
    created = skipped = 0
    for row in rows:
        if row.status != "valid":
            continue
        d = row.data
        cls = await academic.get_class(session, uuid.UUID(d["class_id"]))
        pupil = PupilIn(
            given_name=d["first_name"],
            family_name=d["surname"],
            other_names=d.get("other_names") or None,
            date_of_birth=date.fromisoformat(d["date_of_birth"]),
            sex=d["sex"],
            guardian_name=d["guardian_name"],
            guardian_phone=d["guardian_phone"],
            guardian_relationship=d["guardian_relationship"],
        )
        try:
            async with session.begin_nested():
                e = await _enrol(
                    session,
                    crypto,
                    inst,
                    pupil,
                    EnrolIn(
                        session_id=session_id,
                        year_code=cls.year_code,
                        class_id=cls.id,
                        admission_number=d.get("admission_number") or None,
                        age_override="imported from the school's class list"
                        if d.get("age_note")
                        else None,
                    ),
                    source="import",
                )
            row.status, row.result_id = "created", e.id
            created += 1
        except (Conflict, ValidationFailed) as exc:
            row.status, row.messages = "skipped", [exc.detail]
            skipped += 1
    job.status, job.committed_by, job.committed_at = "committed", ctx.user_id, utcnow()
    job.summary = {**job.summary, "created": created, "skipped": skipped}
    await session.flush()
    return job.summary


# --------------------------------------------------------------------------- subjects and courses


async def register_subjects(
    session: AsyncSession, enrolment_id: uuid.UUID, offering_ids: list[uuid.UUID]
) -> list[academic.OfferingView]:
    """Senior secondary electives (eight or nine subjects with core, including a trade subject),
    or a tertiary student's courses for a semester (within the unit limits). Replaces the
    learner's earlier choices for the same class or semester."""
    e = await _enrolment(session, enrolment_id)
    if e.status != "active":
        raise Conflict("only an active enrolment registers subjects")
    y = year(e.year_code)
    chosen = [await academic.get_offering(session, i) for i in dict.fromkeys(offering_ids)]
    if y.level in TERTIARY_LEVELS:
        semesters = {o.period_id for o in chosen}
        if len(semesters) != 1:
            raise ValidationFailed("register one semester's courses at a time")
        units = sum(o.units or 0 for o in chosen)
        if not UNITS[0] <= units <= UNITS[1]:
            raise ValidationFailed(
                f"{units} units: register between {UNITS[0]} and {UNITS[1]} a semester"
            )
        for o in chosen:
            if o.institution_id != e.institution_id:
                raise ValidationFailed(f"{o.subject.code} runs at another institution")
            if o.capacity is not None:
                taken = (
                    await session.execute(
                        select(func.count())
                        .select_from(SubjectRegistration)
                        .where(
                            SubjectRegistration.offering_id == o.id,
                            SubjectRegistration.status == "registered",
                        )
                    )
                ).scalar_one()
                if taken >= o.capacity:
                    raise Conflict(f"{o.subject.code} is full")
        scope_ids = [
            o.id
            for o in await academic.course_offerings(session, e.institution_id, chosen[0].period_id)
        ]
    elif y.level == "sss":
        cls = await _current_class(session, e.id)
        if cls is None:
            raise Conflict("place the student in a class first")
        offered = await academic.class_offerings(session, cls.id)
        by_id = {o.id: o for o in offered}
        if any(o.id not in by_id for o in chosen):
            raise ValidationFailed(f"choose from the subjects offered in {cls.name}")
        core = [o for o in offered if o.kind == "core"]
        electives = [o for o in chosen if o.kind != "core"]
        total = len(core) + len(electives)
        if not SSS_SUBJECTS[0] <= total <= SSS_SUBJECTS[1]:
            raise ValidationFailed(
                f"{total} subjects with the core ones: take {SSS_SUBJECTS[0]} or {SSS_SUBJECTS[1]}"
            )
        if not any(
            (o.subject.group or "") == "Trade/entrepreneurship" for o in [*core, *electives]
        ):
            raise ValidationFailed("include one trade or entrepreneurship subject")
        chosen = [*core, *electives]
        scope_ids = list(by_id)
    else:
        raise ValidationFailed("subjects are chosen in senior secondary and tertiary only")
    existing = {
        r.offering_id: r
        for r in (
            await session.execute(
                select(SubjectRegistration).where(
                    SubjectRegistration.enrolment_id == e.id,
                    SubjectRegistration.offering_id.in_(scope_ids),
                )
            )
        ).scalars()
    }
    wanted = {o.id for o in chosen}
    for oid, r in existing.items():
        r.status = "registered" if oid in wanted else "dropped"
    for oid in wanted - set(existing):
        session.add(
            SubjectRegistration(
                tenant_id=e.tenant_id,
                scope_node_id=e.scope_node_id,
                enrolment_id=e.id,
                offering_id=oid,
                status="registered",
            )
        )
    await session.flush()
    return sorted(chosen, key=lambda o: o.subject.code)


async def registered_subjects(
    session: AsyncSession, enrolment_id: uuid.UUID
) -> list[academic.OfferingView]:
    e = await _enrolment(session, enrolment_id)
    ids = (
        await session.execute(
            select(SubjectRegistration.offering_id).where(
                SubjectRegistration.enrolment_id == e.id, SubjectRegistration.status == "registered"
            )
        )
    ).scalars()
    return [await academic.get_offering(session, i) for i in ids]


# --------------------------------------------------------------------------- transfers


@dataclass(frozen=True, slots=True)
class TransferView:
    id: uuid.UUID
    learner_id: uuid.UUID
    learner_number: str
    learner_name: str
    from_institution: str
    to_institution: str
    to_institution_id: uuid.UUID
    year_code: str
    effective_on: date
    reason: str
    status: str
    decision_note: str | None
    new_enrolment_id: uuid.UUID | None


async def _tview(session: AsyncSession, t: LearnerTransfer) -> TransferView:
    learner = (
        await session.execute(
            select(Learner).where(Learner.id == t.learner_id).execution_options(**_bypass())
        )
    ).scalar_one()
    p = await _person(session, learner.person_id)
    names = {
        i.id: i.name
        for i in (
            await session.execute(
                select(Institution)
                .where(Institution.id.in_([t.from_institution_id, t.to_institution_id]))
                .execution_options(**_bypass())
            )
        ).scalars()
    }
    return TransferView(
        t.id,
        learner.id,
        learner.learner_number,
        _full(p),
        names[t.from_institution_id],
        names[t.to_institution_id],
        t.to_institution_id,
        t.year_code,
        t.effective_on,
        t.reason,
        t.status,
        t.decision_note,
        t.new_enrolment_id,
    )


async def request_transfer(
    session: AsyncSession,
    enrolment_id: uuid.UUID,
    to_institution_id: uuid.UUID,
    *,
    reason: str,
    effective_on: date | None = None,
) -> TransferView:
    """The sending school asks another school on the platform to take a learner."""
    e = await _enrolment(session, enrolment_id)
    if e.status != "active":
        raise Conflict("only an enrolled learner is transferred")
    ctx = current_context()
    to = (
        await session.execute(
            select(Institution)
            .where(Institution.id == to_institution_id, Institution.tenant_id == ctx.tenant_id)
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()
    if to is None or to.status != "active":
        raise NotFound("the receiving school was not found")
    if to.id == e.institution_id:
        raise ValidationFailed("that is the learner's own school")
    if year(e.year_code).level not in await academic.institution_levels(session, to.id):
        raise ValidationFailed(f"{to.name} does not run {year(e.year_code).name}")
    why = " ".join(reason.split())
    if not why:
        raise ValidationFailed("give the reason (e.g. the family is moving)")
    open_one = (
        await session.execute(
            select(LearnerTransfer.id)
            .where(
                LearnerTransfer.learner_id == e.learner_id, LearnerTransfer.status == "requested"
            )
            .execution_options(**_bypass())
        )
    ).first()
    if open_one:
        raise Conflict("a transfer for this learner is already waiting")
    t = LearnerTransfer(
        tenant_id=e.tenant_id,
        scope_node_id=e.scope_node_id,
        learner_id=e.learner_id,
        from_enrolment_id=e.id,
        from_institution_id=e.institution_id,
        to_institution_id=to.id,
        year_code=e.year_code,
        effective_on=effective_on or today(),
        reason=why[:255],
        status="requested",
        requested_by=ctx.user_id,
    )
    session.add(t)
    await session.flush()
    _publish(session, t, "learner_transfer", events.TRANSFER_REQUESTED)
    return await _tview(session, t)


async def transfers(
    session: AsyncSession,
    institution_id: uuid.UUID,
    *,
    direction: str = "incoming",
    status: str | None = "requested",
) -> list[TransferView]:
    """Transfers into the school (from anywhere) or out of it."""
    inst = await academic.institution(session, institution_id)
    col = (
        LearnerTransfer.to_institution_id
        if direction == "incoming"
        else LearnerTransfer.from_institution_id
    )
    q = select(LearnerTransfer).where(col == inst.id).execution_options(**_bypass())
    if status:
        q = q.where(LearnerTransfer.status == status)
    rows = (await session.execute(q.order_by(LearnerTransfer.created_at))).scalars()
    return [await _tview(session, t) for t in rows]


@dataclass(frozen=True, slots=True)
class SchoolHit:
    id: uuid.UUID
    name: str
    institution_code: str
    lga: str


async def find_schools(session: AsyncSession, text: str, limit: int = 20) -> list[SchoolHit]:
    """Directory lookup across the state (name, code, LGA of active institutions only), to
    choose the school a learner transfers to."""
    ctx = current_context()
    q = " ".join(text.split()).lower()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    rows = (
        await session.execute(
            select(Institution)
            .where(
                Institution.tenant_id == ctx.tenant_id,
                Institution.status == "active",
                or_(
                    func.lower(Institution.name).like(like),
                    func.lower(Institution.institution_code).like(like),
                ),
            )
            .order_by(Institution.name)
            .limit(min(limit, 50))
            .execution_options(**_bypass())
        )
    ).scalars()
    return [SchoolHit(i.id, i.name, i.institution_code, i.location) for i in rows]


async def _transfer(session: AsyncSession, transfer_id: uuid.UUID) -> LearnerTransfer:
    ctx = current_context()
    t = (
        await session.execute(
            select(LearnerTransfer)
            .where(LearnerTransfer.id == transfer_id, LearnerTransfer.tenant_id == ctx.tenant_id)
            .execution_options(**_bypass())
        )
    ).scalar_one_or_none()
    if t is None:
        raise NotFound("transfer not found")
    return t


async def decide_transfer(
    session: AsyncSession,
    transfer_id: uuid.UUID,
    *,
    accept: bool,
    session_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    note: str | None = None,
) -> TransferView:
    """The receiving school accepts (and places the learner) or declines."""
    ctx = current_context()
    t = await _transfer(session, transfer_id)
    to = await academic.institution(session, t.to_institution_id)  # must be in the caller's scope
    if t.status != "requested":
        raise Conflict(f"this transfer is {t.status}")
    text = " ".join((note or "").split()) or None
    if not accept:
        if not text:
            raise ValidationFailed("say why the school cannot take the learner")
        await _settle(session, t, status="declined", note=text)
        return await _tview(session, t)
    if class_id:
        cls = await academic.get_class(session, class_id)
        if cls.institution_id != to.id:
            raise ValidationFailed("the class is in another school")
        session_id = cls.session_id
    if session_id is None:
        raise ValidationFailed("choose the class (or at least the session) the learner joins")
    old = (
        await session.execute(
            select(Enrolment)
            .where(Enrolment.id == t.from_enrolment_id)
            .execution_options(**_bypass())
        )
    ).scalar_one()
    if old.status != "active":
        raise Conflict("the learner is no longer enrolled at the sending school")
    # Both schools' records change together: work at the office above both, for this purpose only.
    shared = await governance.lowest_common_ancestor(session, old.scope_node_id, to.scope_node_id)
    with use_context(replace(ctx, scope_node_id=shared, purpose="learner-transfer")):
        old.status, old.valid_to, old.active_key = (
            "transferred_out",
            max(old.valid_from, t.effective_on),
            None,
        )
        old.exit_reason = f"transferred to {to.name}"[:255]
        for m in (
            await session.execute(
                select(ClassMembership).where(
                    ClassMembership.enrolment_id == old.id, ClassMembership.valid_to.is_(None)
                )
            )
        ).scalars():
            m.valid_to = max(m.valid_from, t.effective_on)
        learner = (
            await session.execute(select(Learner).where(Learner.id == t.learner_id))
        ).scalar_one()
        learner.scope_node_id = to.scope_node_id
        for g in (
            await session.execute(
                select(LearnerGuardian).where(LearnerGuardian.learner_id == learner.id)
            )
        ).scalars():
            g.scope_node_id = to.scope_node_id
        await session.flush()
        new = Enrolment(
            tenant_id=old.tenant_id,
            scope_node_id=to.scope_node_id,
            learner_id=learner.id,
            institution_id=to.id,
            session_id=session_id,
            year_code=t.year_code,
            status="active",
            mode=old.mode,
            source="transfer",
            valid_from=t.effective_on,
            previous_id=old.id,
            active_key=str(learner.id),
        )
        session.add(new)
        await session.flush()
        await _settle(session, t, status="accepted", note=text, new_enrolment=new.id)
    if class_id:
        await _place(session, new, class_id, on=t.effective_on)
    _publish(session, new, "enrolment", events.ENROLMENT_STARTED, source="transfer")
    return await _tview(session, t)


async def _settle(
    session: AsyncSession,
    t: LearnerTransfer,
    *,
    status: str,
    note: str | None,
    new_enrolment: uuid.UUID | None = None,
) -> None:
    ctx = current_context()
    with use_context(
        replace(ctx, scope_node_id=t.scope_node_id, purpose="learner-transfer", system=True)
    ):
        t.status, t.decided_by, t.decided_at, t.decision_note = status, ctx.user_id, utcnow(), note
        t.new_enrolment_id = new_enrolment
        await session.flush()
        _publish(session, t, "learner_transfer", events.TRANSFER_DECIDED, decision=status)


async def cancel_transfer(session: AsyncSession, transfer_id: uuid.UUID) -> TransferView:
    t = await _transfer(session, transfer_id)
    await academic.institution(session, t.from_institution_id)  # the sending school's own
    if t.status != "requested":
        raise Conflict(f"this transfer is {t.status}")
    t.status = "cancelled"
    await session.flush()
    return await _tview(session, t)


# --------------------------------------------------------------------------- Learner 360


@dataclass(frozen=True, slots=True)
class GuardianView:
    person_id: uuid.UUID
    name: str
    relationship: str
    parental_responsibility: bool
    contact_priority: int
    phone: str | None
    court_order_flag: bool
    has_account: bool = False


@dataclass(frozen=True, slots=True)
class LearnerView:
    id: uuid.UUID
    learner_number: str
    person_id: uuid.UUID
    surname: str
    first_name: str
    other_names: str | None
    sex: str
    date_of_birth: date | None
    status: str
    first_enrolled_on: date | None
    current: EnrolmentView | None
    history: list[EnrolmentView]
    guardians: list[GuardianView]


async def _learner(session: AsyncSession, learner_id: uuid.UUID) -> Learner:
    ln = (
        await session.execute(select(Learner).where(Learner.id == learner_id))
    ).scalar_one_or_none()
    if ln is None:
        raise NotFound("learner not found in your scope")
    return ln


async def get_learner(session: AsyncSession, learner_id: uuid.UUID) -> LearnerView:
    from syntegra.mdm.models import PersonContact

    ln = await _learner(session, learner_id)
    p = await _person(session, ln.person_id)
    enrolments = (
        (
            await session.execute(
                select(Enrolment)
                .where(Enrolment.learner_id == ln.id)
                .order_by(Enrolment.valid_from.desc(), Enrolment.created_at.desc())
                .execution_options(**_bypass())
            )
        )
        .scalars()
        .all()
    )
    views = [await _eview(session, e) for e in enrolments]
    from syntegra.iam.models import AppUser

    with_accounts = set(
        (
            await session.execute(
                select(AppUser.person_id).where(
                    AppUser.person_id.in_(
                        select(LearnerGuardian.guardian_person_id).where(
                            LearnerGuardian.learner_id == ln.id
                        )
                    )
                )
            )
        ).scalars()
    )
    guardians = []
    for g in (
        await session.execute(
            select(LearnerGuardian)
            .where(LearnerGuardian.learner_id == ln.id)
            .order_by(LearnerGuardian.contact_priority)
        )
    ).scalars():
        gp = await _person(session, g.guardian_person_id)
        phone = None
        if not g.court_order_flag:
            phone = (
                (
                    await session.execute(
                        select(PersonContact.value)
                        .where(PersonContact.person_id == gp.id, PersonContact.kind == "phone")
                        .execution_options(**_bypass())
                    )
                )
                .scalars()
                .first()
            )
        guardians.append(
            GuardianView(
                gp.id,
                _full(gp),
                g.relationship,
                g.parental_responsibility,
                g.contact_priority,
                phone,
                g.court_order_flag,
                gp.id in with_accounts,
            )
        )
    return LearnerView(
        ln.id,
        ln.learner_number,
        p.id,
        p.family_name,
        p.given_name,
        p.other_names,
        p.sex_at_registration,
        p.date_of_birth,
        ln.status,
        ln.first_enrolled_on,
        next((v for v in views if v.status == "active"), None),
        views,
        guardians,
    )


async def find_learners(
    session: AsyncSession, text: str, *, institution_id: uuid.UUID | None = None, limit: int = 50
) -> list[EnrolmentView]:
    """Learners enrolled in the caller's scope, by name, LRN or admission number."""
    q = " ".join(text.split()).lower()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    stmt = (
        select(Enrolment)
        .join(Learner, Learner.id == Enrolment.learner_id)
        .join(Person, Person.id == Learner.person_id)
        .where(Enrolment.status == "active")
        .where(
            or_(
                func.lower(Person.family_name).like(like),
                func.lower(Person.given_name).like(like),
                func.lower(Learner.learner_number).like(like),
                func.lower(Enrolment.admission_number).like(like),
            )
        )
        .limit(min(limit, 200))
    )
    if institution_id:
        stmt = stmt.where(Enrolment.institution_id == institution_id)
    return [await _eview(session, e) for e in (await session.execute(stmt)).scalars()]


# --------------------------------------------------------------------------- parent accounts


@dataclass(frozen=True, slots=True)
class ParentAccount:
    user_id: uuid.UUID
    login: str
    display_name: str
    created: bool


async def give_guardian_account(
    session: AsyncSession,
    learner_id: uuid.UUID,
    guardian_person_id: uuid.UUID,
    email: str,
    *,
    sign_in_url: str,
) -> ParentAccount:
    """Give a parent or guardian an account (ADR-030): the Parent role at the child's school,
    which shows only their own children, and sign-in by a code sent to this email. Only a
    guardian with parental responsibility, and never one withheld by a court order (CEDA §26.1)."""
    from email_validator import EmailNotValidError, validate_email

    from syntegra.iam import api as iam
    from syntegra.iam.models import AppUser
    from syntegra.platform.email import queue_email

    ln = await _learner(session, learner_id)
    link = (
        await session.execute(
            select(LearnerGuardian).where(
                LearnerGuardian.learner_id == ln.id,
                LearnerGuardian.guardian_person_id == guardian_person_id,
                LearnerGuardian.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise NotFound("that person is not this learner's guardian")
    if link.court_order_flag:
        raise Conflict("this guardian's contact is withheld by a court order")
    if not link.parental_responsibility:
        raise Conflict("only a guardian with parental responsibility gets an account")
    e = await _active(session, ln.id)
    if e is None:
        raise Conflict("the learner is not enrolled anywhere")
    school = (
        await session.execute(
            select(Institution)
            .where(Institution.id == e.institution_id)
            .execution_options(**_bypass())
        )
    ).scalar_one()
    try:
        address = validate_email(email.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValidationFailed(f"the email address is not valid: {exc}") from exc
    ctx = current_context()
    guardian = await _person(session, guardian_person_id)
    user = (
        await session.execute(
            select(AppUser).where(
                AppUser.tenant_id == ctx.tenant_id, AppUser.person_id == guardian.id
            )
        )
    ).scalar_one_or_none()
    created = user is None
    if user is None:
        taken = await iam.user_by_login(session, ctx.tenant_id, address)
        if taken:
            raise Conflict("that email address already belongs to another account")
        view = await iam.create_user(
            session, tenant_id=ctx.tenant_id, login=address, display_name=_full(guardian)
        )
        user = (await session.execute(select(AppUser).where(AppUser.id == view.id))).scalar_one()
        user.person_id = guardian.id
        user.mfa_required = False  # the emailed one-time code is the factor (ADR-030)
    grants = {(g.role_code, g.scope_node_id) for g in await iam.grants_for(session, user.id)}
    if ("PAR", school.scope_node_id) not in grants:
        await iam.grant_role(
            session, user_id=user.id, role_code="PAR", scope_node_id=school.scope_node_id
        )
    await session.flush()
    if created:
        queue_email(
            session,
            tenant_id=ctx.tenant_id,
            purpose="parent-account",
            to=user.login,
            subject=f"Your parent account for {school.name}",
            body=(
                f"Dear {user.display_name},\n\n{school.name} has given you an account to follow "
                "your child's learning and to message the school.\n\n"
                f"Sign in at {sign_in_url} with this email address; a one-time code is sent to "
                "you each time.\n"
            ),
        )
    return ParentAccount(user.id, user.login, user.display_name, created)
