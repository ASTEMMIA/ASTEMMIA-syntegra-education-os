"""Attendance tables (att schema, ADR-032): learner registers and staff attendance."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.core.clock import utcnow
from syntegra.core.ids import uuid7
from syntegra.platform.db import Base, Evidential, TenantScoped

S = "att"
LEARNER_STATUSES = ("present", "late", "absent", "excused")
STAFF_STATUSES = ("present", "late", "absent", "leave", "sick", "official_duty")


class SchoolClosure(Base, TenantScoped):
    """A weekday with no school: a public holiday (set by the State for everyone beneath it)
    or a school's own closure. Registers are not taken, and it is not counted as a school day."""

    __tablename__ = "school_closure"
    __audit_entity__ = "school_closure"
    __table_args__ = (UniqueConstraint("tenant_id", "scope_node_id", "on_date"), {"schema": S})

    on_date: Mapped[date] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)


class Register(Base, TenantScoped):
    """One register: a class's morning or afternoon register on a school day."""

    __tablename__ = "register"
    __audit_entity__ = "register"
    __table_args__ = (
        UniqueConstraint("tenant_id", "class_group_id", "on_date", "session"),
        CheckConstraint("session IN ('am','pm')", name="session"),
        Index(None, "tenant_id", "institution_id", "on_date"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    class_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.class_group.id"), nullable=False
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    on_date: Mapped[date] = mapped_column(nullable=False)
    session: Mapped[str] = mapped_column(String(2), nullable=False)
    taken_by: Mapped[uuid.UUID | None]
    taken_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class LearnerMark(Base, TenantScoped):
    __tablename__ = "learner_mark"
    __audit_entity__ = "learner_mark"
    __table_args__ = (
        UniqueConstraint("tenant_id", "register_id", "learner_id"),
        CheckConstraint("status IN ('present','late','absent','excused')", name="status"),
        Index(None, "tenant_id", "learner_id"),
        {"schema": S},
    )

    register_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("att.register.id"), nullable=False)
    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    enrolment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.enrolment.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))


class AttendanceCorrection(Base, Evidential):
    """A mark changed after the day it was taken: who, why, from what to what. Never edited."""

    __tablename__ = "attendance_correction"
    __table_args__ = (Index(None, "tenant_id", "mark_id"), {"schema": S})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    mark_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("att.learner_mark.id"), nullable=False)
    old_status: Mapped[str] = mapped_column(String(8), nullable=False)
    new_status: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    by_user: Mapped[uuid.UUID | None]
    at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class StaffAttendance(Base, TenantScoped):
    """A member of staff's day at their institution: signed in themselves, or recorded."""

    __tablename__ = "staff_attendance"
    __audit_entity__ = "staff_attendance"
    __table_args__ = (
        UniqueConstraint("tenant_id", "staff_id", "on_date"),
        CheckConstraint(
            "status IN ('present','late','absent','leave','sick','official_duty')", name="status"
        ),
        Index(None, "tenant_id", "institution_id", "on_date"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    on_date: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(14), nullable=False)
    time_in: Mapped[time | None]
    time_out: Mapped[time | None]
    note: Mapped[str | None] = mapped_column(String(200))
    self_recorded: Mapped[bool] = mapped_column(nullable=False, default=False)
    recorded_by: Mapped[uuid.UUID | None]
