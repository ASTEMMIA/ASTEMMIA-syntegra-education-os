"""Records tables (rec schema, CEDA §16.3, ADR-027).

Every row belongs to an institution's scope, so a school sees its own records and the bodies
above it (the LGEA, ASUBEB, the Ministry, the State Archives at the state root) see theirs.
Documents and their versions are permanent: versions and the access log are evidential (never
changed), and a document can never be deleted.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.platform.db import Base, Evidential, Permanent, TenantScoped

S = "rec"
CLASSIFICATIONS = "('public','internal','confidential','restricted','special')"


def LongText() -> Any:
    return String(1_000_000).with_variant(mysql.MEDIUMTEXT(), "mysql")


class RecordsSeries(Base, TenantScoped):
    """One series of an institution's file plan: adopted from a model plan or added locally."""

    __tablename__ = "records_series"
    __audit_entity__ = "records_series"
    __table_args__ = (
        UniqueConstraint("tenant_id", "institution_id", "code"),
        CheckConstraint("numbering IN ('single','annual','self','alphabetical')", name="numbering"),
        CheckConstraint(f"classification IN {CLASSIFICATIONS}", name="classification"),
        CheckConstraint(
            "paper_rule IN ('destroy','transfer_archives','retain')", name="paper_rule"
        ),
        CheckConstraint(
            "paper_retain_years IS NULL OR paper_retain_years BETWEEN 0 AND 100", name="years"
        ),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    plan: Mapped[str] = mapped_column(String(16), nullable=False)  # primary|secondary|…|local
    function_code: Mapped[str] = mapped_column(String(8), nullable=False)
    function_name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    activity: Mapped[str | None] = mapped_column(String(200))
    numbering: Mapped[str] = mapped_column(String(12), nullable=False)
    classification: Mapped[str] = mapped_column(String(12), nullable=False)
    paper_retain_years: Mapped[int | None] = mapped_column(Integer)
    paper_rule: Mapped[str] = mapped_column(String(20), nullable=False)
    vital: Mapped[bool] = mapped_column(nullable=False, default=False)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class PaperFile(Base, TenantScoped):
    """A physical file (folder). Its cover carries the reference, title, classification, dates
    and volume; its location and holder are always known."""

    __tablename__ = "paper_file"
    __audit_entity__ = "paper_file"
    __table_args__ = (
        UniqueConstraint("tenant_id", "institution_id", "file_number", "volume"),
        CheckConstraint("status IN ('open','closed','disposed','transferred')", name="status"),
        CheckConstraint(f"classification IN {CLASSIFICATIONS}", name="classification"),
        CheckConstraint("volume >= 1", name="volume"),
        CheckConstraint("closed_on IS NULL OR closed_on >= opened_on", name="dates"),
        Index(None, "tenant_id", "series_id"),
        Index(None, "tenant_id", "person_id"),
        Index(None, "tenant_id", "status"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rec.records_series.id"), nullable=False
    )
    file_number: Mapped[str] = mapped_column(String(40), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    letter: Mapped[str | None] = mapped_column(String(1))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mdm.person.id"))
    classification: Mapped[str] = mapped_column(String(12), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    previous_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rec.paper_file.id"))
    opened_on: Mapped[date] = mapped_column(nullable=False)
    closed_on: Mapped[date | None]
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="open")
    home_location: Mapped[str] = mapped_column(String(120), nullable=False)  # cabinet, shelf
    holder_user_id: Mapped[uuid.UUID | None]  # who has it now; None = in its home location
    holder_name: Mapped[str | None] = mapped_column(String(120))  # or an outside office
    out_since: Mapped[datetime | None]
    due_back: Mapped[date | None]
    digitised: Mapped[bool] = mapped_column(nullable=False, default=False)
    legal_hold: Mapped[bool] = mapped_column(nullable=False, default=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class FileMovement(Base, TenantScoped, Evidential):
    """Every time a paper file leaves or comes back: the tracking history, never edited."""

    __tablename__ = "file_movement"
    __audit_entity__ = "file_movement"
    __table_args__ = (
        CheckConstraint("kind IN ('issue','return','pass','external_out')", name="kind"),
        Index(None, "tenant_id", "file_id"),
        {"schema": S},
    )

    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rec.paper_file.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(14), nullable=False)
    to_user_id: Mapped[uuid.UUID | None]
    to_name: Mapped[str | None] = mapped_column(String(120))
    moved_at: Mapped[datetime] = mapped_column(nullable=False)
    due_back: Mapped[date | None]
    note: Mapped[str | None] = mapped_column(String(255))


class DigitisationBatch(Base, TenantScoped):
    """A box of paper going through capture, in-house or by a contractor. Only the assigned
    operator captures into it, and someone else checks a sample before it is accepted."""

    __tablename__ = "digitisation_batch"
    __audit_entity__ = "digitisation_batch"
    __table_args__ = (
        UniqueConstraint("tenant_id", "batch_number"),
        CheckConstraint("operator_kind IN ('in_house','contractor')", name="operator_kind"),
        CheckConstraint("status IN ('open','captured','accepted','rejected')", name="status"),
        CheckConstraint(
            "operator_kind = 'in_house' OR contractor_name IS NOT NULL", name="contractor"
        ),
        Index(None, "tenant_id", "operator_user_id"),
        {"schema": S},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    batch_number: Mapped[str] = mapped_column(String(20), nullable=False)  # BAT-2026-000001
    operator_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    contractor_name: Mapped[str | None] = mapped_column(String(200))
    contract_reference: Mapped[str | None] = mapped_column(String(60))
    operator_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    captured_at: Mapped[datetime | None]
    qc_by: Mapped[uuid.UUID | None]
    qc_at: Mapped[datetime | None]
    qc_sampled: Mapped[int | None] = mapped_column(Integer)
    qc_defects: Mapped[int | None] = mapped_column(Integer)
    qc_note: Mapped[str | None] = mapped_column(String(500))


class Document(Base, TenantScoped, Permanent):
    """One record in the repository, registered to its institution. Permanent: it can be
    reclassified, re-indexed and given new versions, but never deleted."""

    __tablename__ = "document"
    __audit_entity__ = "document"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_number"),
        CheckConstraint(f"classification IN {CLASSIFICATIONS}", name="classification"),
        CheckConstraint("status IN ('captured','indexed','verified')", name="status"),
        CheckConstraint("source IN ('scanned','born_digital','email','upload')", name="source"),
        Index(None, "tenant_id", "institution_id", "status"),
        Index(None, "tenant_id", "file_id"),
        Index(None, "tenant_id", "batch_id"),
        {"schema": S},
    )

    document_number: Mapped[str] = mapped_column(String(30), nullable=False)  # DOC-NG-AB-2026-…
    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rec.records_series.id"))
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rec.paper_file.id"))
    folio: Mapped[int | None] = mapped_column(Integer)  # position in the paper file
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rec.digitisation_batch.id"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    classification: Mapped[str] = mapped_column(String(12), nullable=False)
    document_date: Mapped[date | None]
    source: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="captured")
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    proposed: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # suggested metadata (AI/OCR)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    page_count: Mapped[int | None] = mapped_column(Integer)
    search_text: Mapped[str] = mapped_column(LongText(), nullable=False, default="")
    captured_by: Mapped[uuid.UUID | None]
    indexed_by: Mapped[uuid.UUID | None]
    indexed_at: Mapped[datetime | None]
    verified_by: Mapped[uuid.UUID | None]
    verified_at: Mapped[datetime | None]


class DocumentVersion(Base, TenantScoped, Evidential):
    """One stored file of a document. Versions are added, never replaced."""

    __tablename__ = "document_version"
    __audit_entity__ = "document_version"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "version"),
        UniqueConstraint("tenant_id", "blob_key"),
        {"schema": S},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rec.document.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_key: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(40), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(255))
    ocr_text: Mapped[str | None] = mapped_column(LongText())
    note: Mapped[str | None] = mapped_column(String(255))


class DocumentLink(Base, TenantScoped):
    """What a document is about: a learner, member of staff, person, institution, application."""

    __tablename__ = "document_link"
    __audit_entity__ = "document_link"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", "kind", "target_id"),
        CheckConstraint(
            "kind IN ('learner','staff','person','institution','application')", name="kind"
        ),
        Index(None, "tenant_id", "kind", "target_id"),
        {"schema": S},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rec.document.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)


class DocumentAccess(Base, TenantScoped, Evidential):
    """Who opened, downloaded or shared a document, when and why. Never edited."""

    __tablename__ = "document_access"
    __audit_entity__ = "document_access"
    __table_args__ = (
        CheckConstraint("action IN ('view','download','print','share')", name="action"),
        Index(None, "tenant_id", "document_id"),
        Index(None, "tenant_id", "user_id"),
        {"schema": S},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rec.document.id"), nullable=False)
    user_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    version: Mapped[int | None] = mapped_column(Integer)
    purpose: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime] = mapped_column(nullable=False)


class PaperDisposition(Base, TenantScoped):
    """What happens to a closed paper original: destroy, transfer to the State Archives, or
    retain. Requested by one person, decided by another (four-eyes)."""

    __tablename__ = "paper_disposition"
    __audit_entity__ = "paper_disposition"
    __table_args__ = (
        CheckConstraint("action IN ('destroy','transfer_archives','retain')", name="action"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="status"),
        Index(None, "tenant_id", "file_id"),
        Index(None, "tenant_id", "status"),
        {"schema": S},
    )

    file_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rec.paper_file.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID | None]
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(String(255))
    certificate_reference: Mapped[str | None] = mapped_column(String(60))  # destruction / transfer
