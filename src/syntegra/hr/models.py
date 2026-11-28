"""Staff 360 tables (acad schema, CEDA §4, §11.1, §27.1; ADR-024)."""

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


class Staff(Base, TenantScoped):
    """One person's employment with one employer (a tenant). ``scope_node_id`` follows the
    current primary post, so a school sees the staff posted to it and the bodies above see them
    all; ``employer_scope_id`` is the employing body (e.g. ASUBEB)."""

    __tablename__ = "staff"
    __audit_entity__ = "staff"
    __table_args__ = (
        UniqueConstraint("tenant_id", "person_id"),
        UniqueConstraint("tenant_id", "staff_number"),
        UniqueConstraint("tenant_id", "government_file_number"),
        CheckConstraint("category IN ('teaching','non_teaching')", name="category"),
        CheckConstraint("status IN ('active','suspended','on_leave','exited')", name="status"),
        Index(None, "tenant_id", "status"),
        {"schema": S},
    )

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    staff_number: Mapped[str] = mapped_column(String(20), nullable=False)  # STF-NNNN-NNNN-CC
    employer_scope_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    government_file_number: Mapped[str | None] = mapped_column(String(40))  # employer's own no.
    first_appointment_date: Mapped[date | None]
    confirmation_date: Mapped[date | None]
    exit_date: Mapped[date | None]
    exit_reason: Mapped[str | None] = mapped_column(String(20))


class StaffPost(Base, TenantScoped):
    """A post held over a period: where, what, at which grade. Ending one and starting another is
    how postings, transfers and promotions are recorded; nothing is overwritten."""

    __tablename__ = "staff_post"
    __audit_entity__ = "staff_post"
    __table_args__ = (
        CheckConstraint("grade_level IS NULL OR grade_level BETWEEN 1 AND 17", name="grade"),
        CheckConstraint("step IS NULL OR step BETWEEN 1 AND 15", name="step"),
        CheckConstraint("fte > 0 AND fte <= 1", name="fte"),
        CheckConstraint(
            "appointment_type IN ('first_appointment','posting','transfer','promotion','acting',"
            "'secondment','redeployment','contract')",
            name="appointment_type",
        ),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="dates"),
        Index(None, "tenant_id", "staff_id"),
        Index(None, "tenant_id", "institution_id"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    post_scope_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # the place of work
    institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.institution.id"))
    position: Mapped[str] = mapped_column(String(100), nullable=False)
    grade_level: Mapped[int | None] = mapped_column(Integer)
    step: Mapped[int | None] = mapped_column(Integer)
    salary_structure: Mapped[str | None] = mapped_column(String(16))
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=True)
    fte: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("1.00"))
    appointment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(nullable=False)
    end_date: Mapped[date | None]
    reference: Mapped[str | None] = mapped_column(String(60))  # e.g. posting letter number


class StaffQualification(Base, TenantScoped):
    __tablename__ = "staff_qualification"
    __audit_entity__ = "staff_qualification"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('phd','masters','pgde','degree','hnd','nce','nd','diploma','certificate',"
            "'other')",
            name="kind",
        ),
        Index(None, "tenant_id", "staff_id"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)  # e.g. B.Ed Mathematics
    awarding_body: Mapped[str | None] = mapped_column(String(200))
    year: Mapped[int | None] = mapped_column(Integer)
    teaching: Mapped[bool] = mapped_column(nullable=False, default=False)
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[uuid.UUID | None]


class TeacherRegistration(Base, TenantScoped):
    """Registration with a teaching council (TRCN in Nigeria), checked before teaching."""

    __tablename__ = "teacher_registration"
    __audit_entity__ = "teacher_registration"
    __table_args__ = (
        UniqueConstraint("tenant_id", "body_code", "number"),
        CheckConstraint(
            "status IN ('registered','provisional','expired','suspended')", name="status"
        ),
        Index(None, "tenant_id", "staff_id"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    body_code: Mapped[str] = mapped_column(String(24), nullable=False, default="TRCN")
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    registered_on: Mapped[date | None]
    expires_on: Mapped[date | None]


class PostingRequest(Base, TenantScoped):
    """A proposed posting, transfer or promotion. A second person decides it (four-eyes)."""

    __tablename__ = "posting_request"
    __audit_entity__ = "posting_request"
    __table_args__ = (
        CheckConstraint("status IN ('pending','approved','rejected','cancelled')", name="status"),
        Index(None, "tenant_id", "status"),
        Index(None, "tenant_id", "staff_id"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    to_scope_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    to_institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.institution.id"))
    position: Mapped[str] = mapped_column(String(100), nullable=False)
    grade_level: Mapped[int | None] = mapped_column(Integer)
    step: Mapped[int | None] = mapped_column(Integer)
    salary_structure: Mapped[str | None] = mapped_column(String(16))
    appointment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_date: Mapped[date] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    reference: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID | None]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(String(255))


# --------------------------------------------------------------------------- appraisal (ADR-025)


class AppraisalCycle(Base, TenantScoped):
    """One appraisal period for an employer (e.g. the 2026 annual appraisal), with the criteria
    used, frozen for the cycle so reports stay comparable."""

    __tablename__ = "appraisal_cycle"
    __audit_entity__ = "appraisal_cycle"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_node_id", "name"),
        CheckConstraint("status IN ('open','closed')", name="status"),
        CheckConstraint("period_end >= period_start", name="period"),
        {"schema": S},
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[date] = mapped_column(nullable=False)
    period_end: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="open")
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Appraisal(Base, TenantScoped):
    """A staff member's appraisal for a cycle: draft → submitted → countersigned (or returned
    to the appraiser). Kept in the staff member's scope."""

    __tablename__ = "appraisal"
    __audit_entity__ = "appraisal"
    __table_args__ = (
        UniqueConstraint("tenant_id", "cycle_id", "staff_id", "kind"),
        CheckConstraint(
            "status IN ('draft','submitted','returned','countersigned')", name="status"
        ),
        CheckConstraint("kind IN ('annual','probation')", name="kind"),
        CheckConstraint(
            "recommendation IN ('none','promotion','commendation','training','improvement_plan',"
            "'confirm_appointment','extend_probation','terminate_appointment')",
            name="recommendation",
        ),
        Index(None, "tenant_id", "staff_id"),
        {"schema": S},
    )

    cycle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.appraisal_cycle.id"), nullable=False
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="annual")
    status: Mapped[str] = mapped_column(String(14), nullable=False, default="draft")
    appraiser_id: Mapped[uuid.UUID | None]  # app_user
    countersigner_id: Mapped[uuid.UUID | None]
    ratings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    band: Mapped[str | None] = mapped_column(String(16))
    strengths: Mapped[str | None] = mapped_column(String(1000))
    improvements: Mapped[str | None] = mapped_column(String(1000))
    development_plan: Mapped[str | None] = mapped_column(String(1000))
    recommendation: Mapped[str] = mapped_column(String(24), nullable=False, default="none")
    # The staff member's own ratings, given before or alongside the appraiser's (ADR-026).
    self_ratings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    self_assessed_at: Mapped[datetime | None]
    competency_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    objectives_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    appraiser_comment: Mapped[str | None] = mapped_column(String(1000))
    appraisee_comment: Mapped[str | None] = mapped_column(String(1000))
    countersigner_comment: Mapped[str | None] = mapped_column(String(1000))
    submitted_at: Mapped[datetime | None]
    countersigned_at: Mapped[datetime | None]


class LessonObservation(Base, TenantScoped):
    """A lesson watched and rated, evidence for the teacher's appraisal."""

    __tablename__ = "lesson_observation"
    __audit_entity__ = "lesson_observation"
    __table_args__ = (Index(None, "tenant_id", "staff_id"), {"schema": S})

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    observed_by: Mapped[uuid.UUID | None]  # app_user
    observed_on: Mapped[date] = mapped_column(nullable=False)
    class_name: Mapped[str] = mapped_column(String(60), nullable=False)  # e.g. Primary 4B
    subject: Mapped[str] = mapped_column(String(80), nullable=False)
    topic: Mapped[str | None] = mapped_column(String(200))
    ratings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    strengths: Mapped[str | None] = mapped_column(String(1000))
    improvements: Mapped[str | None] = mapped_column(String(1000))


# ------------------------------------------------------------------- performance and CPD (ADR-026)


class PerformanceObjective(Base, TenantScoped):
    """An objective agreed for a staff member for a cycle (Performance Management System):
    agreed at the start, reviewed mid-year, assessed at the end."""

    __tablename__ = "performance_objective"
    __audit_entity__ = "performance_objective"
    __table_args__ = (
        CheckConstraint("weight BETWEEN 1 AND 100", name="weight"),
        CheckConstraint(
            "mid_year_progress IS NULL OR mid_year_progress BETWEEN 0 AND 100", name="mid_year"
        ),
        CheckConstraint("achievement IS NULL OR achievement BETWEEN 0 AND 100", name="achievement"),
        CheckConstraint("status IN ('agreed','reviewed','assessed','dropped')", name="status"),
        Index(None, "tenant_id", "staff_id", "cycle_id"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("acad.appraisal_cycle.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    measure: Mapped[str] = mapped_column(String(300), nullable=False)  # KPI: how it is measured
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="agreed")
    agreed_by: Mapped[uuid.UUID | None]
    mid_year_progress: Mapped[int | None] = mapped_column(Integer)  # percent
    mid_year_note: Mapped[str | None] = mapped_column(String(500))
    achievement: Mapped[int | None] = mapped_column(Integer)  # percent of target achieved
    final_note: Mapped[str | None] = mapped_column(String(500))


class AppraisalAppeal(Base, TenantScoped):
    """An appraisee's appeal against a countersigned appraisal, decided by an officer who was
    neither the appraiser nor the countersigning officer."""

    __tablename__ = "appraisal_appeal"
    __audit_entity__ = "appraisal_appeal"
    __table_args__ = (
        UniqueConstraint("tenant_id", "appraisal_id"),
        CheckConstraint("status IN ('lodged','upheld','dismissed')", name="status"),
        {"schema": S},
    )

    appraisal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.appraisal.id"), nullable=False)
    grounds: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="lodged")
    lodged_by: Mapped[uuid.UUID | None]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    revised_ratings: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    revised_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    revised_band: Mapped[str | None] = mapped_column(String(16))


CPD_CATEGORIES = (
    "subject_knowledge",
    "pedagogy",
    "ict",
    "leadership",
    "assessment",
    "inclusive_education",
    "safeguarding",
    "early_years",
    "literacy_numeracy",
    "wellbeing",
    "other",
)
_CATS = ",".join(f"'{c}'" for c in CPD_CATEGORIES)


class CpdProgramme(Base, TenantScoped):
    """A training programme offered to staff: workshop, course, conference, cluster meeting…
    Run by the body that creates it; schools beneath it can nominate staff."""

    __tablename__ = "cpd_programme"
    __audit_entity__ = "cpd_programme"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(f"category IN ({_CATS})", name="category"),
        CheckConstraint("mode IN ('in_person','online','blended','school_based')", name="mode"),
        CheckConstraint(
            "status IN ('planned','open','closed','completed','cancelled')", name="status"
        ),
        CheckConstraint("end_date >= start_date", name="dates"),
        CheckConstraint("credit_points >= 0", name="points"),
        {"schema": S},
    )

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_accredited: Mapped[bool] = mapped_column(nullable=False, default=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str] = mapped_column(String(14), nullable=False)
    audience: Mapped[str | None] = mapped_column(String(200))  # e.g. Primary mathematics teachers
    description: Mapped[str | None] = mapped_column(String(2000))
    start_date: Mapped[date] = mapped_column(nullable=False)
    end_date: Mapped[date] = mapped_column(nullable=False)
    venue: Mapped[str | None] = mapped_column(String(200))
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 1), nullable=False)
    credit_points: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")


class CpdParticipation(Base, TenantScoped):
    """One staff member's CPD activity: a programme they were nominated to, or an activity they
    report themselves. It counts toward their CPD points once verified."""

    __tablename__ = "cpd_participation"
    __audit_entity__ = "cpd_participation"
    __table_args__ = (
        CheckConstraint(
            "status IN ('nominated','enrolled','attended','completed','verified','rejected',"
            "'withdrawn','self_reported')",
            name="status",
        ),
        CheckConstraint(
            "activity_type IN ('programme','workshop','course','conference','mentoring',"
            "'peer_observation','research','qualification','other')",
            name="activity_type",
        ),
        CheckConstraint(f"category IN ({_CATS})", name="category"),
        Index(None, "tenant_id", "staff_id"),
        Index(None, "tenant_id", "programme_id"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    programme_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.cpd_programme.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(18), nullable=False)
    started_on: Mapped[date | None]
    completed_on: Mapped[date | None]
    hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    credit_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(14), nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False)  # nomination | self | appraisal
    appraisal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.appraisal.id"))
    evidence: Mapped[str | None] = mapped_column(String(300))  # certificate number, document id
    reflection: Mapped[str | None] = mapped_column(String(1000))  # what changed in practice
    nominated_by: Mapped[uuid.UUID | None]
    verified_by: Mapped[uuid.UUID | None]
    verified_at: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(String(500))


class DevelopmentAction(Base, TenantScoped):
    """An action in a staff member's individual development plan."""

    __tablename__ = "development_action"
    __audit_entity__ = "development_action"
    __table_args__ = (
        CheckConstraint("status IN ('planned','in_progress','done','dropped')", name="status"),
        CheckConstraint(f"category IN ({_CATS})", name="category"),
        Index(None, "tenant_id", "staff_id"),
        {"schema": S},
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("acad.staff.id"), nullable=False)
    appraisal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.appraisal.id"))
    need: Mapped[str] = mapped_column(String(300), nullable=False)  # what to develop
    action: Mapped[str] = mapped_column(String(300), nullable=False)  # how
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    due_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="planned")
    participation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("acad.cpd_participation.id")
    )
    outcome: Mapped[str | None] = mapped_column(String(500))
