"""Pilot deployments (ADR-031): the pilot institutions, and demonstration data for them.

**The pilot list is confidential** until it is formally approved, so it is never in the
repository: the operator supplies it at deployment as a CSV (the institution template plus a
``board`` column naming the body that registers each one). It goes through the same checks as any
institution import, so a local government area that does not exist is refused.

**Demonstration data** is made up and marked as such on every person record
(``source_system = "demo"``): the screens label it, the monitoring dashboard counts it separately,
and Person 360 never matches it with a real person. It is refused in production.
"""

from __future__ import annotations

import csv
import io
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.academic import api as academic
from syntegra.academic.domain import YEARS, Year
from syntegra.academic.models import ClassGroup, CurriculumAdoption, Offering
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.governance import api as governance
from syntegra.hr import api as hr
from syntegra.institutions import api as institutions
from syntegra.institutions.domain import TEMPLATE_COLUMNS
from syntegra.learners import api as learners
from syntegra.learners.models import Learner
from syntegra.mdm import api as mdm
from syntegra.mdm.domain import Sex
from syntegra.mdm.models import Person
from syntegra.pilot.names import Names
from syntegra.platform.crypto import FieldCrypto
from syntegra.platform.session import PLATFORM_BYPASS

BYPASS = {PLATFORM_BYPASS: True}

SESSION = "2026/2027"
TERMS = (
    (date(2026, 9, 14), date(2026, 12, 18)),
    (date(2027, 1, 11), date(2027, 4, 9)),
    (date(2027, 4, 26), date(2027, 7, 23)),
)
SEMESTERS = ((date(2026, 10, 5), date(2027, 2, 26)), (date(2027, 3, 15), date(2027, 7, 30)))
ARM_SIZE = 40
LEARNERS_PER_LECTURER = 25
SEMESTER_LEVELS = frozenset({"nce", "nd", "hnd", "degree", "postgraduate"})
NO_CLASS_LEVELS = SEMESTER_LEVELS  # tertiary learners take courses, not classes (ADR-029)
DEFAULT_AGE = {"tvet": 15, "nce": 18, "nd": 18, "hnd": 20, "degree": 18, "postgraduate": 23}


def _system(tenant_id: uuid.UUID, scope_id: uuid.UUID, purpose: str) -> RequestContext:
    return RequestContext(tenant_id, scope_id, None, system=True, purpose=purpose)


# --------------------------------------------------------------------------- the institutions


@dataclass(frozen=True, slots=True)
class PilotInstitution:
    id: uuid.UUID
    name: str
    board: str
    board_scope_id: uuid.UUID
    scope_node_id: uuid.UUID
    lga: str
    created: bool


def _split(text: str) -> dict[str, str]:
    """The pilot file, as one institution-import CSV per registering body."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
    if "board" not in headers:
        raise ValidationFailed(
            "the pilot file needs a 'board' column (ASUBEB, MIN-TERTIARY-EDUCATION, ABIA-SAME)"
        )
    groups: dict[str, list[dict[str, str]]] = {}
    for raw in reader:
        board = (raw.get(headers["board"]) or "").strip().upper()
        if not board:
            raise ValidationFailed(f"no board given for {raw.get(headers.get('name', ''), '?')}")
        groups.setdefault(board, []).append(
            {c: (raw.get(headers[c]) or "").strip() for c in TEMPLATE_COLUMNS if c in headers}
        )
    if not groups:
        raise ValidationFailed("the pilot file has no institutions")
    out = {}
    for board, rows in groups.items():
        buf = io.StringIO()
        cols = [c for c in TEMPLATE_COLUMNS if c in headers]
        w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        out[board] = buf.getvalue()
    return out


async def load_institutions(
    session: AsyncSession, tenant_id: uuid.UUID, text: str
) -> list[PilotInstitution]:
    """Register the pilot institutions, all or nothing: every row of every body is checked
    first, and one problem (an LGA that does not exist, say) stops the whole load. Institutions
    already registered are found again, not duplicated."""
    groups = _split(text)
    jobs: list[tuple[str, uuid.UUID, uuid.UUID, list[str]]] = []
    problems: list[str] = []
    for board, csv_text in groups.items():
        scope = await governance.scope_node_by_code(session, tenant_id, board)
        if scope is None:
            raise ValidationFailed(f"{board} is not a body in this state")
        names = [r["name"] for r in csv.DictReader(io.StringIO(csv_text))]
        with use_context(_system(tenant_id, scope.id, "pilot-institutions")):
            job_id = await institutions.preview_import(session, csv_text, source_name="pilot")
            _, rows = await institutions.get_job(session, job_id)
        for r in rows:
            dup = r.status == "error" and all("already registered" in m for m in r.messages)
            if r.status == "error" and not dup:
                problems.append(f"{board} row {r.row_no}: " + "; ".join(r.messages))
        jobs.append((board, scope.id, job_id, names))
    if problems:
        raise ValidationFailed(
            "the pilot file has problems; nothing was registered",
            next_action="Correct these rows and load it again: " + " | ".join(problems),
        )
    out: list[PilotInstitution] = []
    for board, scope_id, job_id, names in jobs:
        node = await governance.get_scope_node(session, scope_id)
        with use_context(_system(tenant_id, scope_id, "pilot-institutions")):
            summary = await institutions.commit_import(
                session, job_id, managing_body=node.name[:64]
            )
            _, rows = await institutions.get_job(session, job_id)
            created = {r.result_id for r in rows if r.status == "created"}
            registered = {
                v.name: v for v in await institutions.list_institutions(session, limit=500)
            }
        del summary
        for name in names:
            v = registered.get(name)
            if v is None:
                raise Conflict(f"{name} was not registered under {board}")
            out.append(
                PilotInstitution(
                    v.id, v.name, board, scope_id, v.scope_node_id, v.location, v.id in created
                )
            )
    return out


# --------------------------------------------------------------------------- demonstration data


@dataclass
class DemoSchool:
    name: str
    learners: int = 0
    teachers: int = 0
    classes: int = 0
    years: list[str] = field(default_factory=list)


@dataclass
class DemoSummary:
    learners: int
    teachers: int
    classes: int
    schools: list[DemoSchool]


def single_sex(name: str) -> str | None:
    """A girls' or boys' school takes only girls or only boys."""
    if re.search(r"\bgirls?'?\b", name, re.IGNORECASE):
        return "F"
    if re.search(r"\bboys?'?\b", name, re.IGNORECASE):
        return "M"
    return None


def _years(levels: set[str]) -> list[Year]:
    return sorted(
        (y for y in YEARS.values() if y.level in levels),
        key=lambda y: list(YEARS).index(y.code),
    )


def allocate(total: int, weights: list[int]) -> list[int]:
    """Split ``total`` in proportion to ``weights`` (largest remainders), summing exactly."""
    whole = sum(weights)
    if whole == 0:
        return [0] * len(weights)
    raw = [total * w / whole for w in weights]
    base = [math.floor(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)[
        : total - sum(base)
    ]:
        base[i] += 1
    return base


async def demo_loaded(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    return int(
        (
            await session.execute(
                select(func.count(Learner.id))
                .join(Person, Person.id == Learner.person_id)
                .where(Learner.tenant_id == tenant_id, Person.source_system == mdm.DEMO_SOURCE)
                .execution_options(**BYPASS)
            )
        ).scalar_one()
    )


async def _curriculum(session: AsyncSession, tenant_id: uuid.UUID, root: uuid.UUID) -> None:
    with use_context(_system(tenant_id, root, "pilot-demo")):
        version = await academic.seed_national_curriculum(session)
        adopted = (
            await session.execute(
                select(CurriculumAdoption.id)
                .where(CurriculumAdoption.version_id == version)
                .execution_options(**BYPASS)
            )
        ).first()
        if not adopted:
            await academic.adopt(session, version, years=None, from_session=SESSION)


async def _session(
    session: AsyncSession, tenant_id: uuid.UUID, scope_id: uuid.UUID, *, semesters: bool
) -> uuid.UUID:
    """The 2026/2027 session visible at this scope (set above it, or set here)."""
    with use_context(_system(tenant_id, scope_id, "pilot-demo")):
        for s in await academic.sessions(session):
            if s.name == SESSION:
                return s.id
        made = await academic.create_session(
            session,
            academic.SessionIn(SESSION, list(SEMESTERS if semesters else TERMS), semesters),
        )
        return made.id


async def _teacher(
    session: AsyncSession,
    crypto: FieldCrypto,
    names: Names,
    tenant_id: uuid.UUID,
    p: PilotInstitution,
    position: str,
    salary_structure: str = "CONPSS",
) -> uuid.UUID:
    """A made-up member of teaching staff, posted to the institution by its employer."""
    with use_context(_system(tenant_id, p.board_scope_id, "pilot-demo")):
        for _ in range(20):
            given, family, sex, born = names.adult()
            try:
                person = await mdm.create_person(
                    session,
                    crypto,
                    mdm.PersonIn(
                        given_name=given,
                        family_name=family,
                        date_of_birth=born,
                        sex_at_registration=Sex(sex),
                        source_system=mdm.DEMO_SOURCE,
                        scope_node_id=p.scope_node_id,
                    ),
                )
            except mdm.DuplicateBlocked:
                continue
            start = date(max(born.year + 24, 2000), 9, 1)
            staff = await hr.appoint(
                session,
                hr.AppointmentIn(
                    person_id=person.person_id,
                    category="teaching",
                    position=position,
                    start_date=start,
                    institution_id=p.id,
                    grade_level=8,
                    step=2,
                    salary_structure=salary_structure,
                ),
            )
            return staff.id
    raise RuntimeError("could not make a distinct demonstration teacher")


async def load_demo(
    session: AsyncSession,
    crypto: FieldCrypto,
    tenant_id: uuid.UUID,
    pilots: list[PilotInstitution],
    *,
    learners_total: int = 2500,
    seed: int = 2026,
) -> DemoSummary:
    """Fill the pilot institutions with made-up learners, guardians, teachers and classes.

    Learners are shared out in proportion to the number of class years each institution runs;
    schools get arms of up to 40 with a class teacher each; tertiary learners are enrolled in
    their year (they take courses, not classes) with a lecturer for every 25."""
    if not 1 <= learners_total <= 20_000:
        raise ValidationFailed("give between 1 and 20,000 demonstration learners")
    if await demo_loaded(session, tenant_id):
        raise Conflict("demonstration learners are already loaded in this state")
    tenant = await governance.get_tenant(session, tenant_id)
    await _curriculum(session, tenant_id, tenant.root_scope_node_id)
    names = Names(seed)
    plans = []
    for p in pilots:
        with use_context(_system(tenant_id, p.scope_node_id, "pilot-demo")):
            levels = await academic.institution_levels(session, p.id)
        plans.append((p, _years(levels)))
    shares = allocate(learners_total, [len(ys) for _, ys in plans])
    summary = DemoSummary(0, 0, 0, [])
    serial = 0
    for (p, ys), share in zip(plans, shares, strict=True):
        school = DemoSchool(p.name, years=[y.code for y in ys])
        summary.schools.append(school)
        if not ys or not share:
            continue
        semesters = any(y.level in SEMESTER_LEVELS for y in ys)
        # A board's calendar covers its schools; an institution under the Ministry sets its own.
        where = p.board_scope_id if p.board == "ASUBEB" else p.scope_node_id
        session_id = await _session(session, tenant_id, where, semesters=semesters)
        sess = None
        for y, n in zip(ys, allocate(share, [1] * len(ys)), strict=True):
            if not n:
                continue
            class_ids: list[uuid.UUID | None] = [None]
            if y.level not in NO_CLASS_LEVELS:
                arms = math.ceil(n / ARM_SIZE)
                class_ids = []
                for a in range(arms):
                    teacher = await _teacher(
                        session, crypto, names, tenant_id, p, "Classroom teacher"
                    )
                    school.teachers += 1
                    with use_context(_system(tenant_id, p.scope_node_id, "pilot-demo")):
                        c = await academic.create_class(
                            session,
                            p.id,
                            academic.ClassIn(
                                session_id,
                                y.code,
                                "ABCDEFGHJK"[a],
                                capacity=ARM_SIZE,
                                class_teacher_staff_id=teacher,
                            ),
                        )
                    class_ids.append(c.id)
                    school.classes += 1
            with use_context(_system(tenant_id, p.scope_node_id, "pilot-demo")):
                sess = sess or await academic.get_session(session, session_id)
                age = y.typical_age if y.typical_age is not None else (
                    DEFAULT_AGE.get(y.level, 18) + y.order - 1
                )  # fmt: skip
                for i in range(n):
                    serial += 1
                    ch = names.child(age, sess.starts_on, single_sex(p.name))
                    await learners.register_direct(
                        session,
                        crypto,
                        p.id,
                        learners.PupilIn(
                            given_name=ch.given,
                            family_name=ch.family,
                            other_names=ch.other,
                            date_of_birth=ch.born,
                            sex=ch.sex,
                            guardian_name=ch.guardian,
                            guardian_phone=ch.guardian_phone,
                            guardian_relationship=ch.guardian_relationship,
                            demo=True,
                        ),
                        learners.EnrolIn(
                            session_id,
                            y.code,
                            class_id=class_ids[i % len(class_ids)],
                            admission_number=f"DEMO/{serial:05d}",
                            starts_on=sess.starts_on,
                        ),
                    )
                    school.learners += 1
        if semesters:
            for _ in range(math.ceil(school.learners / LEARNERS_PER_LECTURER)):
                await _teacher(session, crypto, names, tenant_id, p, "Lecturer", "CONTEDISS")
                school.teachers += 1
    summary.learners = sum(s.learners for s in summary.schools)
    summary.teachers = sum(s.teachers for s in summary.schools)
    summary.classes = sum(s.classes for s in summary.schools)
    return summary


# --------------------------------------------------------------------------- demonstration activity


@dataclass
class ActivitySummary:
    registers: int = 0
    marks: int = 0
    staff_days: int = 0
    scores: int = 0
    days: list[date] = field(default_factory=list)


async def load_activity(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    pilots: list[PilotInstitution],
    *,
    days: int = 10,
    seed: int = 2026,
) -> ActivitySummary:
    """Registers for the last school days, staff sign-ins, and the term's first two tests, for
    the demonstration learners and teachers only. No examination results or report cards: the
    term's examinations have not happened yet."""
    import random
    from datetime import time as clock
    from decimal import Decimal

    from syntegra.assessment import api as assessment
    from syntegra.attendance import api as attendance

    if not 1 <= days <= 60:
        raise ValidationFailed("give between 1 and 60 school days")
    r = random.Random(seed)  # noqa: S311 - reproducible made-up activity, not secrets
    out = ActivitySummary()
    today_local = attendance.local_now().date()
    candidates = attendance.school_days_back(today_local, days * 2)
    statuses = ("present", "late", "absent", "excused")
    weights = (0.91, 0.05, 0.03, 0.01)
    for p in pilots:
        with use_context(_system(tenant_id, p.scope_node_id, "pilot-activity")):
            classes = list(
                (
                    await session.execute(
                        select(ClassGroup).where(
                            ClassGroup.institution_id == p.id,
                            ClassGroup.status == "active",
                        )
                    )
                ).scalars()
            )
            taken_days: set[date] = set()
            for c in classes:
                open_days = []
                for d in reversed(candidates):
                    why, term = await attendance.not_a_school_day(session, c, d)
                    if not why and term is not None:
                        open_days.append(d)
                    if len(open_days) == days:
                        break
                for d in sorted(open_days):
                    members = await attendance.members_on(session, c.id, d)
                    if not members or not all(
                        mdm.is_demo(person.source_system) for _, _, person in members
                    ):
                        continue  # only made-up classes get made-up registers
                    for sess in ("am", "pm"):
                        marks = []
                        for lid, _eid, _person in members:
                            st = r.choices(statuses, weights)[0]
                            marks.append(
                                attendance.MarkIn(lid, st, "Sick" if st == "excused" else None)
                            )
                        await attendance.take_register(
                            session, c.id, d, sess, marks, may_amend=True, amend_reason="demo"
                        )
                        out.registers += 1
                        out.marks += len(marks)
                    taken_days.add(d)
            for d in sorted(taken_days):
                lines = await attendance.staff_day(session, p.id, d)
                entries = []
                for ln in lines:
                    if not ln.demo:
                        continue
                    roll = r.random()
                    if roll < 0.04:
                        entries.append(attendance.StaffIn(ln.staff_id, "absent"))
                    elif roll < 0.06:
                        entries.append(attendance.StaffIn(ln.staff_id, "sick"))
                    else:
                        minute = r.randint(0, 45)
                        t_in = clock(7 + (25 + minute) // 60, (25 + minute) % 60)
                        late = t_in > clock(8, 0)
                        entries.append(
                            attendance.StaffIn(
                                ln.staff_id,
                                "late" if late else "present",
                                t_in,
                                clock(14, r.randint(0, 50)),
                            )
                        )
                if entries:
                    await attendance.record_staff(session, p.id, d, entries)
                    out.staff_days += len(entries)
            out.days = sorted(set(out.days) | taken_days)
            # The first two tests of the current term, for every core subject of each class.
            for c in classes:
                term = await attendance.term_on(session, c.session_id, today_local)
                if term is None:
                    continue
                offerings = (
                    await session.execute(
                        select(Offering).where(
                            Offering.class_group_id == c.id,
                            Offering.kind == "core",
                            Offering.active.is_(True),
                        )
                    )
                ).scalars()
                for o in offerings:
                    sheet = await assessment.get_sheet(session, o.id, term.id)
                    parts = {x["code"]: x["max_score"] for x in sheet.rules["components"]}
                    tests = [code for code in parts if code != "EXAM"][:2]
                    entries_ = []
                    for row in sheet.lines:
                        if not row.demo:
                            continue
                        ability = r.gauss(0.62, 0.15)
                        for code in tests:
                            v = max(0.0, min(1.0, r.gauss(ability, 0.08))) * parts[code]
                            entries_.append(
                                assessment.ScoreIn(
                                    row.learner_id, code, Decimal(str(round(v * 2) / 2))
                                )
                            )
                    if entries_:
                        await assessment.enter_scores(session, o.id, term.id, entries_)
                        out.scores += len(entries_)
    return out
