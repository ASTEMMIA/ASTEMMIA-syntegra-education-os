"""org schema: institution and institution_level (CEDA §4 Institution 360, §11.1; ADR-012)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, UniqueConstraint
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
