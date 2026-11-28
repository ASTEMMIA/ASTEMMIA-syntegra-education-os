"""Academic structure tables (acad schema, CEDA §4-§5, ADR-029)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.platform.db import Base, TenantScoped

S = "acad"


class AcademicPeriod(Base, TenantScoped):
    """A session (2026/2027) and its terms or semesters. A school calendar is set by the board
    (e.g. ASUBEB) and used by every school beneath it; a tertiary institution sets its own."""

    __tablename__ = "academic_period"
    __audit_entity__ = "academic_period"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_node_id", "kind", "name", "parent_id"),
        CheckConstraint("kind IN ('session','term','semester')", name="kind"),
        CheckConstraint("status IN ('planned','current','closed')", name="status"),
        CheckConstraint("ends_on > starts_on", name="dates"),
        Index(None, "tenant_id", "parent_id"),
        {"schema": S},
    )

    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)  # 2026/2027, First term
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.academic_period.id"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    starts_on: Mapped[date] = mapped_column(nullable=False)
    ends_on: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="planned")


class Room(Base, TenantScoped):
    __tablename__ = "room"
    __audit_entity__ = "room"
    __table_args__ = (
        UniqueConstraint("tenant_id", "institution_id", "code"),
        CheckConstraint(
            "kind IN ('classroom','laboratory','workshop','ict','library','hall',"
            "'lecture_theatre','other')",
            name="kind",
        ),
        CheckConstraint("capacity BETWEEN 1 AND 2000", name="capacity"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)  # Block B, room 4
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="classroom")
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    building: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class Subject(Base, TenantScoped):
    """The subject master. State subjects (from the national curriculum) sit at the state root;
    an institution's own subjects and tertiary courses carry its institution_id."""

    __tablename__ = "subject"
    __audit_entity__ = "subject"
    __table_args__ = (
        UniqueConstraint("tenant_id", "institution_key", "code"),
        CheckConstraint("units IS NULL OR units BETWEEN 0 AND 30", name="units"),
        CheckConstraint("semester IS NULL OR semester IN (1, 2)", name="semester"),
        {"schema": S},
    )

    code: Mapped[str] = mapped_column(String(16), nullable=False)  # MTH, CSC 101
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(60))
    institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.institution.id"))
    institution_key: Mapped[str] = mapped_column(String(36), nullable=False)  # id, or "state"
    # Tertiary courses
    units: Mapped[int | None] = mapped_column(Integer)
    year_code: Mapped[str | None] = mapped_column(String(8))
    semester: Mapped[int | None] = mapped_column(Integer)
    department: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class Curriculum(Base, TenantScoped):
    __tablename__ = "curriculum"
    __audit_entity__ = "curriculum"
    __table_args__ = (UniqueConstraint("tenant_id", "code"), {"schema": S})

    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    authority: Mapped[str] = mapped_column(String(150), nullable=False)


class CurriculumVersion(Base, TenantScoped):
    """Immutable once published (CEDA §4): a change is a new version."""

    __tablename__ = "curriculum_version"
    __audit_entity__ = "curriculum_version"
    __table_args__ = (
        UniqueConstraint("tenant_id", "curriculum_id", "version"),
        CheckConstraint("status IN ('draft','published','retired')", name="status"),
        {"schema": S},
    )

    curriculum_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.curriculum.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    note: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None]


class CurriculumEntry(Base, TenantScoped):
    """One subject in one year of a curriculum version: core, elective or optional."""

    __tablename__ = "curriculum_entry"
    __audit_entity__ = "curriculum_entry"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version_id", "year_code", "subject_id"),
        CheckConstraint("kind IN ('core','elective','optional')", name="kind"),
        CheckConstraint(
            "periods_per_week IS NULL OR periods_per_week BETWEEN 0 AND 20", name="periods"
        ),
        {"schema": S},
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.curriculum_version.id"), nullable=False
    )
    year_code: Mapped[str] = mapped_column(String(8), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.subject.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    periods_per_week: Mapped[int | None] = mapped_column(Integer)


class CurriculumAdoption(Base, TenantScoped):
    """A scope (the state, ASUBEB) adopts a version for some years from a session onwards. A
    class resolves to the nearest adoption above its school (CEDA §4: exactly one version)."""

    __tablename__ = "curriculum_adoption"
    __audit_entity__ = "curriculum_adoption"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_node_id", "version_id", "from_session"),
        {"schema": S},
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.curriculum_version.id"), nullable=False
    )
    years: Mapped[str] = mapped_column(String(255), nullable=False)  # "P1,P2,…" or "*"
    from_session: Mapped[str] = mapped_column(String(9), nullable=False)  # 2026/2027
    adopted_by: Mapped[uuid.UUID | None]


class ClassGroup(Base, TenantScoped):
    """One arm of one year in one session: Primary 4A, 2026/2027."""

    __tablename__ = "class_group"
    __audit_entity__ = "class_group"
    __table_args__ = (
        UniqueConstraint("tenant_id", "institution_id", "session_id", "year_code", "arm"),
        CheckConstraint("capacity BETWEEN 1 AND 120", name="capacity"),
        CheckConstraint("status IN ('active','closed')", name="status"),
        Index(None, "tenant_id", "class_teacher_staff_id"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    year_code: Mapped[str] = mapped_column(String(8), nullable=False)
    arm: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.room.id"))
    class_teacher_staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.staff.id"))
    assistant_staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.staff.id"))
    curriculum_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("acad.curriculum_version.id")
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")


class Offering(Base, TenantScoped):
    """A subject taught to a class in a session, or a course run in a semester."""

    __tablename__ = "offering"
    __audit_entity__ = "offering"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_id", "subject_id", "class_key"),
        CheckConstraint("kind IN ('core','elective','optional')", name="kind"),
        CheckConstraint(
            "periods_per_week IS NULL OR periods_per_week BETWEEN 0 AND 20", name="periods"
        ),
        Index(None, "tenant_id", "class_group_id"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.subject.id"), nullable=False)
    class_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.class_group.id"))
    class_key: Mapped[str] = mapped_column(String(36), nullable=False)  # class id, or the year
    year_code: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="core")
    periods_per_week: Mapped[int | None] = mapped_column(Integer)
    units: Mapped[int | None] = mapped_column(Integer)
    capacity: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class TeachingAssignment(Base, TenantScoped):
    """Who teaches an offering: the subject teacher, or the course lecturer(s)."""

    __tablename__ = "teaching_assignment"
    __audit_entity__ = "teaching_assignment"
    __table_args__ = (
        CheckConstraint("role IN ('lead','assistant')", name="role"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="dates"),
        Index(None, "tenant_id", "offering_id"),
        Index(None, "tenant_id", "staff_id"),
        {"schema": S},
    )

    offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.offering.id"), nullable=False)
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="lead")
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_to: Mapped[date | None]
    note: Mapped[str | None] = mapped_column(String(255))
