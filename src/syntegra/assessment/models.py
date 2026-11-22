"""Assessment and examination tables (acad schema, ADR-032)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.core.clock import utcnow
from syntegra.core.ids import uuid7
from syntegra.platform.db import Base, Evidential, TenantScoped

S = "acad"


class AssessmentPolicy(Base, TenantScoped):
    """A scope's own rules for a group of levels (parts, grade bands, pass mark), overriding the
    defaults for everything beneath it."""

    __tablename__ = "assessment_policy"
    __audit_entity__ = "assessment_policy"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_node_id", "group_code"),
        CheckConstraint("group_code IN ('basic','senior','nbte','nuc')", name="group_code"),
        {"schema": S},
    )

    group_code: Mapped[str] = mapped_column(String(8), nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ResultSheet(Base, TenantScoped):
    """One subject (or course) for one term: its scores, and where they are in approval.
    It keeps the rules it was opened under, so later changes do not regrade it."""

    __tablename__ = "result_sheet"
    __audit_entity__ = "result_sheet"
    __table_args__ = (
        UniqueConstraint("tenant_id", "offering_id", "term_id"),
        CheckConstraint("status IN ('open','submitted','approved')", name="status"),
        Index(None, "tenant_id", "institution_id", "term_id"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.offering.id"), nullable=False)
    term_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    submitted_by: Mapped[uuid.UUID | None]
    submitted_at: Mapped[datetime | None]
    approved_by: Mapped[uuid.UUID | None]
    approved_at: Mapped[datetime | None]
    return_note: Mapped[str | None] = mapped_column(String(500))


class Score(Base, TenantScoped):
    __tablename__ = "score"
    __audit_entity__ = "score"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sheet_id", "learner_id", "component"),
        CheckConstraint("score IS NULL OR score >= 0", name="score"),
        Index(None, "tenant_id", "learner_id"),
        {"schema": S},
    )

    sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.result_sheet.id"), nullable=False)
    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    enrolment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.enrolment.id"), nullable=False)
    component: Mapped[str] = mapped_column(String(8), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    absent: Mapped[bool] = mapped_column(nullable=False, default=False)


class SheetEvent(Base, Evidential):
    """Submitted, approved, returned or reopened: who, when and why. Never edited."""

    __tablename__ = "sheet_event"
    __table_args__ = (
        CheckConstraint("action IN ('submitted','approved','returned','reopened')", name="action"),
        Index(None, "tenant_id", "sheet_id"),
        {"schema": S},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    sheet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.result_sheet.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    by_user: Mapped[uuid.UUID | None]
    at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    note: Mapped[str | None] = mapped_column(String(500))


class ExamPaper(Base, TenantScoped):
    """A paper on the examination timetable."""

    __tablename__ = "exam_paper"
    __audit_entity__ = "exam_paper"
    __table_args__ = (
        CheckConstraint("duration_minutes BETWEEN 10 AND 360", name="duration"),
        CheckConstraint("status IN ('scheduled','cancelled')", name="status"),
        Index(None, "tenant_id", "institution_id", "starts_at"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.offering.id"), nullable=False)
    term_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(nullable=False)  # local time (West Africa Time)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    room_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.room.id"))
    invigilator_staff_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.staff.id"))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="scheduled")
    note: Mapped[str | None] = mapped_column(String(200))


class ReportComment(Base, TenantScoped):
    """The class teacher's and the head's remarks on a learner's term."""

    __tablename__ = "report_comment"
    __audit_entity__ = "report_comment"
    __table_args__ = (UniqueConstraint("tenant_id", "learner_id", "term_id"), {"schema": S})

    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    term_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    class_teacher: Mapped[str | None] = mapped_column(String(500))
    head: Mapped[str | None] = mapped_column(String(500))


class ReportCard(Base, TenantScoped, Evidential):
    """A published report card: exactly what was issued. A correction is a new version."""

    __tablename__ = "report_card"
    __audit_entity__ = "report_card"
    __table_args__ = (
        UniqueConstraint("tenant_id", "learner_id", "term_id", "version"),
        Index(None, "tenant_id", "class_group_id", "term_id"),
        {"schema": S},
    )

    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    class_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.class_group.id"), nullable=False
    )
    term_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_by: Mapped[uuid.UUID | None]
    published_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class TranscriptIssue(Base, TenantScoped, Evidential):
    """A transcript as issued, with a reference and checksum for verification. Never edited."""

    __tablename__ = "transcript_issue"
    __audit_entity__ = "transcript_issue"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference"),
        Index(None, "tenant_id", "learner_id"),
        {"schema": S},
    )

    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    reference: Mapped[str] = mapped_column(String(24), nullable=False)  # TRN-2026-000001
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_by: Mapped[uuid.UUID | None]
    issued_on: Mapped[date] = mapped_column(nullable=False)
