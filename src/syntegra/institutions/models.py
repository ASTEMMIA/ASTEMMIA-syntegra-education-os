"""org schema: institution and institution_level (CEDA §4 Institution 360, §11.1; ADR-012)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.platform.db import Base, TenantScoped


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
    decision_note: Mapped[str | None] = mapped_column(String(1000))
    institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.institution.id"))
