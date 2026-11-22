"""org schema: institution and institution_level (CEDA §4 Institution 360, §11.1; ADR-012)."""

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

from syntegra.core.ids import uuid7
from syntegra.platform.db import Audited, Base, Hash32, TenantScoped


class Institution(Base, TenantScoped):
    """An institution's master record, kindergarten to university. ``scope_node_id`` IS the
    institution's own scope node; ``parent_scope_id`` is where it sits under its body (an LGEA
    for a school, the Ministry itself for a tertiary institution); ``location`` is its LGA."""

    __tablename__ = "institution"
    __audit_entity__ = "institution"
    __table_args__ = (
        UniqueConstraint("tenant_id", "institution_code"),
        UniqueConstraint("tenant_id", "government_code"),
        UniqueConstraint("tenant_id", "parent_scope_id", "location_key", "name_key"),
        Index(None, "tenant_id", "parent_scope_id"),
        Index(None, "tenant_id", "location_key"),
        Index(None, "tenant_id", "postcode"),
        CheckConstraint("ownership IN ('public','private')", name="ownership"),
        CheckConstraint(
            "institution_type IN ('school','university','polytechnic','college_of_education',"
            "'technical_college','college_of_health','other_tertiary')",
            name="institution_type",
        ),
        CheckConstraint("status IN ('active','closed')", name="status"),
        {"schema": "org"},
    )

    institution_code: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # INS-NG-NNNNNN (§7.1)
    government_code: Mapped[str | None] = mapped_column(
        String(40)
    )  # e.g. ASUBEB registration number
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_key: Mapped[str] = mapped_column(
        String(200), nullable=False
    )  # folded, for duplicate checks
    parent_scope_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    location: Mapped[str] = mapped_column(String(64), nullable=False)  # local government area
    location_key: Mapped[str] = mapped_column(String(64), nullable=False)  # folded, for duplicates
    institution_type: Mapped[str] = mapped_column(String(24), nullable=False, default="school")
    ownership: Mapped[str] = mapped_column(String(8), nullable=False, default="public")
    managing_body: Mapped[str | None] = mapped_column(String(64))  # e.g. ASUBEB
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")
    town: Mapped[str | None] = mapped_column(String(100))
    address_line1: Mapped[str | None] = mapped_column(String(255))  # street or plot
    address_line2: Mapped[str | None] = mapped_column(String(255))
    postcode: Mapped[str | None] = mapped_column(String(16))  # Nigeria: 6-digit NIPOST code
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    head_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mdm.person.id"))
    # Private institutions (§2): their own tenant holds their operational data; this row is the
    # regulator's registration record of them, in the state tenant.
    own_tenant_id: Mapped[uuid.UUID | None]
    proprietor_name: Mapped[str | None] = mapped_column(String(200))


class InstitutionLevel(Base, TenantScoped):
    """Levels offered (§11.2: text[] becomes a child table)."""

    __tablename__ = "institution_level"
    __audit_entity__ = "institution_level"
    __table_args__ = (
        UniqueConstraint("tenant_id", "institution_id", "level"),
        CheckConstraint(
            "level IN ('eccde','primary','jss','sss',"
            "'tvet','nce','nd','hnd','degree','postgraduate')",
            name="level",
        ),
        {"schema": "org"},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)


class RegistrationApplication(Base, TenantScoped):
    """A private provider's application to register an institution (CEDA §22 "Institution
    registration"; ADR-014). Held in the state tenant at the regulator's scope."""

    __tablename__ = "registration_application"
    __audit_entity__ = "registration_application"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference"),
        Index(None, "tenant_id", "status"),
        Index(None, "tenant_id", "location_key", "name_key"),
        CheckConstraint(
            "status IN ('submitted','under_review','site_inspection','approved','rejected')",
            name="status",
        ),
        {"schema": "org"},
    )

    reference: Mapped[str] = mapped_column(String(20), nullable=False)  # PSR-2026-000001
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="submitted")
    regulator_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # The institution as applied for: the validated import-row shape (name, type, lga, levels,
    # address, postcode, contacts), so approval registers exactly what was reviewed.
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    name_key: Mapped[str] = mapped_column(String(200), nullable=False)
    location_key: Mapped[str] = mapped_column(String(64), nullable=False)
    proprietor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    proprietor_email: Mapped[str] = mapped_column(String(254), nullable=False)
    proprietor_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    cac_number: Mapped[str | None] = mapped_column(String(20))  # Corporate Affairs Commission
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # Email verification (ADR-015): the proprietor proves the address before review starts.
    email_verified_at: Mapped[datetime | None]
    email_token_hash: Mapped[bytes | None] = mapped_column(Hash32())  # SHA-256 of the one-time code
    email_token_expires_at: Mapped[datetime | None]
    verification_sends: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_verification_sent_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.institution.id"))


class EducationBody(Base, Audited):
    """An education body outside the state's own structure (ADR-019): an examination board,
    admissions board, regulator, funder… Platform reference data from a catalogue file, shared by
    every tenant; audited on the platform chain."""

    __tablename__ = "education_body"
    __audit_entity__ = "education_body"
    __table_args__ = (
        CheckConstraint("scope IN ('international','regional','national','state')", name="scope"),
        {"schema": "org"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)  # e.g. WAEC
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    countries: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    covers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    examinations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(String(500))
    catalogue: Mapped[str] = mapped_column(String(32), nullable=False)  # where it came from
    active: Mapped[bool] = mapped_column(nullable=False, default=True)

    def audit_tenant_id(self) -> uuid.UUID:
        return uuid.UUID(int=0)  # platform chain, like built-in roles

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return None


class InstitutionBodyLink(Base, TenantScoped):
    """An institution's standing with an external body (ADR-019): a WAEC or NECO centre number,
    its JAMB code, NUC/NBTE/NCCE accreditation… Kept in the institution's tenant and scope, so
    whoever may see the institution may see its links. Ending a link keeps it, as history."""

    __tablename__ = "institution_body_link"
    __audit_entity__ = "institution_body_link"
    __table_args__ = (
        # A body's number identifies one institution, e.g. one WAEC centre number per centre.
        UniqueConstraint("body_id", "link_type", "exam_code", "reference"),
        Index(None, "tenant_id", "institution_id"),
        CheckConstraint(
            "link_type IN ('exam_centre','admissions_code','accreditation','approval',"
            "'beneficiary')",
            name="link_type",
        ),
        CheckConstraint(
            "status IN ('active','provisional','suspended','withdrawn')", name="status"
        ),
        {"schema": "org"},
    )

    institution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.institution.id"), nullable=False
    )
    body_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("org.education_body.id"), nullable=False)
    link_type: Mapped[str] = mapped_column(String(20), nullable=False)
    exam_code: Mapped[str | None] = mapped_column(String(24))
    reference: Mapped[str | None] = mapped_column(String(40))  # the body's number for it
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    valid_from: Mapped[date | None]
    valid_to: Mapped[date | None]
    note: Mapped[str | None] = mapped_column(String(255))
