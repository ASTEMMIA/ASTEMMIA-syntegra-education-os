"""Learner registration tables (acad schema, CEDA §4 Learner 360, §5 admissions and enrolment,
ADR-029)."""

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

from syntegra.platform.db import Base, TenantScoped

S = "acad"


class Learner(Base, TenantScoped):
    """One learner master record for life (1:1 with a person). Its scope follows the current
    enrolment, so the school a learner attends sees them and the bodies above see them all."""

    __tablename__ = "learner"
    __audit_entity__ = "learner"
    __table_args__ = (
        UniqueConstraint("tenant_id", "person_id"),
        UniqueConstraint("tenant_id", "learner_number"),
        CheckConstraint("status IN ('enrolled','not_enrolled','completed')", name="status"),
        {"schema": S},
    )

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    learner_number: Mapped[str] = mapped_column(String(20), nullable=False)  # LRN-NNNN-NNNN-CC
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="not_enrolled")
    first_enrolled_on: Mapped[date | None]


class LearnerGuardian(Base, TenantScoped):
    __tablename__ = "learner_guardian"
    __audit_entity__ = "learner_guardian"
    __table_args__ = (
        UniqueConstraint("tenant_id", "learner_id", "guardian_person_id"),
        CheckConstraint(
            "relationship IN ('mother','father','guardian','grandparent','sibling','aunt_uncle',"
            "'foster_carer','sponsor','self','other')",
            name="relationship",
        ),
        CheckConstraint("contact_priority BETWEEN 1 AND 9", name="priority"),
        Index(None, "tenant_id", "guardian_person_id"),
        {"schema": S},
    )

    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    guardian_person_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mdm.person.id"), nullable=False
    )
    relationship: Mapped[str] = mapped_column(String(14), nullable=False)
    parental_responsibility: Mapped[bool] = mapped_column(nullable=False, default=True)
    contact_priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lives_with: Mapped[bool] = mapped_column(nullable=False, default=True)
    court_order_flag: Mapped[bool] = mapped_column(nullable=False, default=False)
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_to: Mapped[date | None]


class AdmissionApplication(Base, TenantScoped):
    """Application → review → assessment → decision → offer → acceptance → enrolment (CEDA §17).
    Held at the school; the applicant becomes a person and a learner only on enrolment, so
    applications that go nowhere leave no master records behind."""

    __tablename__ = "admission_application"
    __audit_entity__ = "admission_application"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference"),
        CheckConstraint(
            "status IN ('submitted','under_review','assessment','offered','waitlisted',"
            "'rejected','accepted','declined','enrolled','withdrawn','expired')",
            name="status",
        ),
        CheckConstraint("channel IN ('school','online','transfer')", name="channel"),
        CheckConstraint(
            "assessment_score IS NULL OR assessment_score BETWEEN 0 AND 100", name="score"
        ),
        Index(None, "tenant_id", "institution_id", "status"),
        {"schema": S},
    )

    reference: Mapped[str] = mapped_column(String(20), nullable=False)  # ADM-2026-000001
    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    year_code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="submitted")
    channel: Mapped[str] = mapped_column(String(8), nullable=False, default="school")
    given_name: Mapped[str] = mapped_column(String(100), nullable=False)
    family_name: Mapped[str] = mapped_column(String(100), nullable=False)
    other_names: Mapped[str | None] = mapped_column(String(150))
    date_of_birth: Mapped[date] = mapped_column(nullable=False)
    sex: Mapped[str] = mapped_column(String(1), nullable=False)
    previous_school: Mapped[str | None] = mapped_column(String(200))
    guardian_name: Mapped[str] = mapped_column(String(200), nullable=False)
    guardian_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    guardian_email: Mapped[str | None] = mapped_column(String(254))
    guardian_relationship: Mapped[str] = mapped_column(String(14), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mdm.person.id"))
    assessment_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    assessment_note: Mapped[str | None] = mapped_column(String(500))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    offer_expires_on: Mapped[date | None]
    due_by: Mapped[date] = mapped_column(nullable=False)  # decision SLA (10 working days)
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    enrolment_id: Mapped[uuid.UUID | None]
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class Enrolment(Base, TenantScoped):
    """A learner at an institution in a session, in a year. One active enrolment per learner:
    ``active_key`` holds the learner id while active and is unique (CEDA §6)."""

    __tablename__ = "enrolment"
    __audit_entity__ = "enrolment"
    __table_args__ = (
        UniqueConstraint("tenant_id", "active_key"),
        CheckConstraint(
            "status IN ('active','completed','transferred_out','withdrawn','graduated')",
            name="status",
        ),
        CheckConstraint("mode IN ('full_time','part_time','distance')", name="mode"),
        CheckConstraint(
            "source IN ('admission','direct','import','transfer','promotion')", name="source"
        ),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="dates"),
        Index(None, "tenant_id", "learner_id"),
        Index(None, "tenant_id", "institution_id", "session_id", "status"),
        {"schema": S},
    )

    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.academic_period.id"), nullable=False
    )
    year_code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="full_time")
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    admission_number: Mapped[str | None] = mapped_column(String(30))  # the school's own number
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_to: Mapped[date | None]
    exit_reason: Mapped[str | None] = mapped_column(String(255))
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("acad.admission_application.id")
    )
    previous_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.enrolment.id"))
    age_override: Mapped[str | None] = mapped_column(String(255))  # why an out-of-band age
    active_key: Mapped[str | None] = mapped_column(String(36))


class ClassMembership(Base, TenantScoped):
    """Which class a learner sits in, over time (moving arms is a new row)."""

    __tablename__ = "class_membership"
    __audit_entity__ = "class_membership"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="dates"),
        Index(None, "tenant_id", "class_group_id"),
        Index(None, "tenant_id", "enrolment_id"),
        {"schema": S},
    )

    enrolment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.enrolment.id"), nullable=False)
    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    class_group_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.class_group.id"), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_to: Mapped[date | None]


class SubjectRegistration(Base, TenantScoped):
    """Electives a senior secondary student takes, or courses a tertiary student registers."""

    __tablename__ = "subject_registration"
    __audit_entity__ = "subject_registration"
    __table_args__ = (
        UniqueConstraint("tenant_id", "enrolment_id", "offering_id"),
        CheckConstraint("status IN ('registered','dropped')", name="status"),
        Index(None, "tenant_id", "offering_id"),
        {"schema": S},
    )

    enrolment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.enrolment.id"), nullable=False)
    offering_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.offering.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="registered")


class LearnerTransfer(Base, TenantScoped):
    """Request → receiving school accepts → enrolled there (CEDA §17). Held at the sending
    school; the receiving school sees transfers addressed to it."""

    __tablename__ = "learner_transfer"
    __audit_entity__ = "learner_transfer"
    __table_args__ = (
        CheckConstraint("status IN ('requested','accepted','declined','cancelled')", name="status"),
        Index(None, "tenant_id", "to_institution_id", "status"),
        Index(None, "tenant_id", "learner_id"),
        {"schema": S},
    )

    learner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.learner.id"), nullable=False)
    from_enrolment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.enrolment.id"), nullable=False
    )
    from_institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    to_institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    year_code: Mapped[str] = mapped_column(String(8), nullable=False)
    effective_on: Mapped[date] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="requested")
    requested_by: Mapped[uuid.UUID | None]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(String(255))
    new_enrolment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.enrolment.id"))
