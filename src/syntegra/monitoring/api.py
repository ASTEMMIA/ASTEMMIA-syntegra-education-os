"""The Government Monitoring Dashboard (ADR-031): what the State can see about the institutions
beneath an office, counted from the platform's own records as they stand now.

Figures are counts only: no names, no message text. Demonstration data (ADR-031) is counted
separately, so a figure never passes made-up learners off as real ones. Areas the platform does
not record yet (attendance, results, infrastructure, inspections) are listed as such, never shown
with invented numbers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.academic.domain import YEARS
from syntegra.academic.models import ClassGroup
from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.governance import api as governance
from syntegra.hr.models import Staff, StaffPost
from syntegra.institutions.models import Institution, InstitutionLevel
from syntegra.learners.models import AdmissionApplication, Enrolment, Learner, LearnerTransfer
from syntegra.mdm import api as mdm
from syntegra.mdm.models import Person
from syntegra.messaging.models import Conversation, Message
from syntegra.platform.session import PLATFORM_BYPASS
from syntegra.records.models import Document

BYPASS: dict[str, Any] = {PLATFORM_BYPASS: True}
SCHOOL_TYPES = {"school"}
LEVEL_NAMES = {
    "eccde": "Early childhood",
    "primary": "Primary",
    "jss": "Junior secondary",
    "sss": "Senior secondary",
    "tvet": "Technical (TVET)",
    "nce": "NCE",
    "nd": "ND",
    "hnd": "HND",
    "degree": "Degree",
    "postgraduate": "Postgraduate",
    "adult": "Adult and non-formal",
}
# What the dashboard cannot show yet, and what it needs first. Shown as it is (ADR-031).
NOT_YET = (
    ("Attendance", "Daily registers in the attendance module (not built yet)."),
    ("Academic performance", "Continuous assessment and examination results (not built yet)."),
    (
        "Infrastructure",
        "An infrastructure register: classrooms, broadband, Wi-Fi, solar power, devices, recorded "
        "from site surveys (not built yet).",
    ),
    ("Inspection reports", "The school inspection module (not built yet)."),
)


@dataclass
class InstitutionStat:
    id: uuid.UUID
    name: str
    code: str
    lga: str
    institution_type: str
    levels: list[str]
    latitude: float | None
    longitude: float | None
    learners: int = 0
    girls: int = 0
    boys: int = 0
    demo_learners: int = 0
    teachers: int = 0
    demo_teachers: int = 0
    classes: int = 0
    places: int = 0
    pupil_teacher_ratio: float | None = None


@dataclass
class AreaStat:
    lga: str
    institutions: int = 0
    learners: int = 0
    teachers: int = 0


@dataclass
class Overview:
    as_of: datetime
    scope_name: str
    institutions: int
    institutions_by_type: dict[str, int]
    learners: int
    girls: int
    boys: int
    demo_learners: int
    teachers: int
    demo_teachers: int
    classes: int
    places: int
    pupil_teacher_ratio: float | None
    learners_by_level: dict[str, int]
    admissions_open: int
    admissions_overdue: int
    transfers_waiting: int
    documents: int
    messages_7_days: int
    by_lga: list[AreaStat] = field(default_factory=list)
    schools: list[InstitutionStat] = field(default_factory=list)
    not_yet: list[dict[str, str]] = field(default_factory=list)


def _ratio(learners: int, teachers: int) -> float | None:
    return round(learners / teachers, 1) if teachers else None


async def overview(session: AsyncSession) -> Overview:
    """Everything beneath the caller's active scope (an office sees its institutions; a head
    sees their own)."""
    ctx = current_context()
    node = await governance.get_scope_node(session, ctx.scope_node_id)
    insts = list(
        (
            await session.execute(
                select(Institution)
                .where(Institution.status == "active")
                .order_by(Institution.location, Institution.name)
            )
        ).scalars()
    )
    ids = [i.id for i in insts] or [uuid.UUID(int=0)]
    stats = {
        i.id: InstitutionStat(
            i.id,
            i.name,
            i.institution_code,
            i.location,
            i.institution_type,
            [],
            float(i.latitude) if i.latitude is not None else None,
            float(i.longitude) if i.longitude is not None else None,
        )
        for i in insts
    }
    for inst_id, level in await session.execute(
        select(InstitutionLevel.institution_id, InstitutionLevel.level)
        .where(InstitutionLevel.institution_id.in_(ids))
        .execution_options(**BYPASS)
    ):
        stats[inst_id].levels.append(level)

    by_level: dict[str, int] = {}
    learner_rows = await session.execute(
        select(
            Enrolment.institution_id,
            Enrolment.year_code,
            Person.sex_at_registration,
            Person.source_system,
            func.count(Enrolment.id),
        )
        .join(Learner, Learner.id == Enrolment.learner_id)
        .join(Person, Person.id == Learner.person_id)
        .where(Enrolment.institution_id.in_(ids), Enrolment.status == "active")
        .group_by(
            Enrolment.institution_id,
            Enrolment.year_code,
            Person.sex_at_registration,
            Person.source_system,
        )
        .execution_options(**BYPASS)
    )
    for inst_id, year_code, sex, source, n in learner_rows:
        s = stats[inst_id]
        s.learners += n
        s.girls += n if sex == "F" else 0
        s.boys += n if sex == "M" else 0
        s.demo_learners += n if mdm.is_demo(source) else 0
        y = YEARS.get(year_code)
        level = LEVEL_NAMES.get(y.level, y.level) if y else year_code
        by_level[level] = by_level.get(level, 0) + n

    on = today()
    teacher_rows = await session.execute(
        select(StaffPost.institution_id, Person.source_system, func.count(func.distinct(Staff.id)))
        .join(Staff, Staff.id == StaffPost.staff_id)
        .join(Person, Person.id == Staff.person_id)
        .where(
            StaffPost.institution_id.in_(ids),
            StaffPost.start_date <= on,
            (StaffPost.end_date.is_(None)) | (StaffPost.end_date >= on),
            Staff.category == "teaching",
            Staff.status == "active",
        )
        .group_by(StaffPost.institution_id, Person.source_system)
        .execution_options(**BYPASS)
    )
    for post_inst, source, n in teacher_rows:
        if post_inst in stats:
            stats[post_inst].teachers += n
            stats[post_inst].demo_teachers += n if mdm.is_demo(source) else 0

    for inst_id, n, places in await session.execute(
        select(ClassGroup.institution_id, func.count(ClassGroup.id), func.sum(ClassGroup.capacity))
        .where(ClassGroup.institution_id.in_(ids), ClassGroup.status == "active")
        .group_by(ClassGroup.institution_id)
        .execution_options(**BYPASS)
    ):
        stats[inst_id].classes, stats[inst_id].places = n, int(places or 0)

    for s in stats.values():
        s.pupil_teacher_ratio = _ratio(s.learners, s.teachers)

    admissions_open = (
        await session.execute(
            select(func.count(AdmissionApplication.id)).where(
                AdmissionApplication.institution_id.in_(ids),
                AdmissionApplication.status.in_(("submitted", "under_review", "assessment")),
            )
        )
    ).scalar_one()
    admissions_overdue = (
        await session.execute(
            select(func.count(AdmissionApplication.id)).where(
                AdmissionApplication.institution_id.in_(ids),
                AdmissionApplication.status.in_(("submitted", "under_review", "assessment")),
                AdmissionApplication.due_by < on,
            )
        )
    ).scalar_one()
    transfers = (
        await session.execute(
            select(func.count(LearnerTransfer.id))
            .where(
                LearnerTransfer.to_institution_id.in_(ids), LearnerTransfer.status == "requested"
            )
            .execution_options(**BYPASS)
        )
    ).scalar_one()
    documents = (
        await session.execute(
            select(func.count(Document.id)).where(Document.institution_id.in_(ids))
        )
    ).scalar_one()
    messages = (
        await session.execute(
            select(func.count(Message.id))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.tenant_id == ctx.tenant_id,
                Conversation.scope_node_id.in_(governance.subtree_of(ctx.scope_node_id)),
                Message.sent_at >= utcnow() - timedelta(days=7),
                Message.kind != "system",
            )
        )
    ).scalar_one()

    areas: dict[str, AreaStat] = {}
    by_type: dict[str, int] = {}
    for s in stats.values():
        a = areas.setdefault(s.lga, AreaStat(s.lga))
        a.institutions += 1
        a.learners += s.learners
        a.teachers += s.teachers
        by_type[s.institution_type] = by_type.get(s.institution_type, 0) + 1
    schools = list(stats.values())
    learners = sum(s.learners for s in schools)
    teachers = sum(s.teachers for s in schools)
    return Overview(
        as_of=utcnow(),
        scope_name=node.name,
        institutions=len(schools),
        institutions_by_type=by_type,
        learners=learners,
        girls=sum(s.girls for s in schools),
        boys=sum(s.boys for s in schools),
        demo_learners=sum(s.demo_learners for s in schools),
        teachers=teachers,
        demo_teachers=sum(s.demo_teachers for s in schools),
        classes=sum(s.classes for s in schools),
        places=sum(s.places for s in schools),
        pupil_teacher_ratio=_ratio(learners, teachers),
        learners_by_level=dict(sorted(by_level.items(), key=lambda kv: -kv[1])),
        admissions_open=int(admissions_open),
        admissions_overdue=int(admissions_overdue),
        transfers_waiting=int(transfers),
        documents=int(documents),
        messages_7_days=int(messages),
        by_lga=sorted(areas.values(), key=lambda a: (-a.learners, a.lga)),
        schools=schools,
        not_yet=[{"area": a, "needs": n} for a, n in NOT_YET],
    )
