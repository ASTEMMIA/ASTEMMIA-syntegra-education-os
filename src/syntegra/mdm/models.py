"""mdm schema: person, person_name, person_contact, external_identifier, match_review,
person_merge (CEDA §4, §6.2, §11.1). All tenant-scoped (§6.1 standard columns)."""

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
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.platform.db import Base, EffectiveDated, Hash32, TenantScoped


def VarBinary(n: int) -> Any:
    return LargeBinary(n).with_variant(mysql.VARBINARY(n), "mysql")


class Person(Base, TenantScoped):
    """One human = one person row (P1). Supertype of learner, guardian, staff and user."""

    __tablename__ = "person"
    __audit_entity__ = "person"
    __table_args__ = (
        Index(None, "tenant_id", "family_key", "given_key"),
        Index(None, "tenant_id", "date_of_birth"),
        Index(None, "tenant_id", "status"),
        CheckConstraint("status IN ('active','merged')", name="status"),
        CheckConstraint("sex_at_registration IN ('F','M','X','U')", name="sex"),
        {"schema": "mdm"},
    )

    given_name: Mapped[str] = mapped_column(String(100), nullable=False)
    family_name: Mapped[str] = mapped_column(String(100), nullable=False)
    other_names: Mapped[str | None] = mapped_column(String(200))
    preferred_name: Mapped[str | None] = mapped_column(String(100))
    name_script: Mapped[str] = mapped_column(String(4), nullable=False, default="Latn")
    date_of_birth: Mapped[date | None]
    sex_at_registration: Mapped[str] = mapped_column(String(1), nullable=False, default="U")
    nationality: Mapped[str | None] = mapped_column(String(2))
    primary_language: Mapped[str | None] = mapped_column(String(3))
    # Matching keys (Metaphone of the first token), maintained by the service on every write.
    given_key: Mapped[str | None] = mapped_column(String(16))
    family_key: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="active")
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mdm.person.id"))
    last_confirmed_at: Mapped[datetime | None]
    quality_score: Mapped[int | None] = mapped_column(Integer)
    quality: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class PersonName(Base, TenantScoped, EffectiveDated):
    """Previous names, aliases and transliterations; all searchable for matching."""

    __tablename__ = "person_name"
    __audit_entity__ = "person_name"
    __table_args__ = (
        Index(None, "tenant_id", "person_id"),
        Index(None, "tenant_id", "family_key"),
        CheckConstraint("name_type IN ('previous','alias','transliteration')", name="name_type"),
        {"schema": "mdm"},
    )

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    name_type: Mapped[str] = mapped_column(String(16), nullable=False)
    given_name: Mapped[str] = mapped_column(String(100), nullable=False)
    family_name: Mapped[str] = mapped_column(String(100), nullable=False)
    other_names: Mapped[str | None] = mapped_column(String(200))
    name_script: Mapped[str] = mapped_column(String(4), nullable=False, default="Latn")
    family_key: Mapped[str | None] = mapped_column(String(16))


class PersonContact(Base, TenantScoped, EffectiveDated):
    """Phone (E.164) or email. Confidential tier: encrypted at rest by the database (§8.1)."""

    __tablename__ = "person_contact"
    __audit_entity__ = "person_contact"
    __table_args__ = (
        Index(None, "tenant_id", "value"),
        Index(None, "tenant_id", "person_id"),
        CheckConstraint("kind IN ('phone','email')", name="kind"),
        {"schema": "mdm"},
    )

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[str] = mapped_column(String(254), nullable=False)  # normalised
    is_primary: Mapped[bool] = mapped_column(nullable=False, default=False)
    verified_at: Mapped[datetime | None]


class ExternalIdentifier(Base, TenantScoped):
    """National and exam identifiers: never a key; encrypted, found by blind index (§6.2, §8.1)."""

    __tablename__ = "external_identifier"
    __audit_entity__ = "external_identifier"
    __audit_exclude__ = frozenset({"row_version", "updated_at", "value_enc"})
    __table_args__ = (
        UniqueConstraint("tenant_id", "scheme", "value_hmac"),
        Index(None, "tenant_id", "person_id"),
        {"schema": "mdm"},
    )

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    scheme: Mapped[str] = mapped_column(String(16), nullable=False)
    value_enc: Mapped[bytes] = mapped_column(VarBinary(255), nullable=False)
    value_hmac: Mapped[bytes] = mapped_column(Hash32(), nullable=False)
    display_suffix: Mapped[str] = mapped_column(
        String(4), nullable=False
    )  # masked display: •••4417
    verified_at: Mapped[datetime | None]
    verified_by_adapter: Mapped[str | None] = mapped_column(String(32))


class MatchReview(Base, TenantScoped):
    """A possible duplicate for a data steward to decide (§4, §16.1)."""

    __tablename__ = "match_review"
    __audit_entity__ = "match_review"
    __table_args__ = (
        Index(None, "tenant_id", "status"),
        Index(None, "tenant_id", "person_id"),
        CheckConstraint("status IN ('open','same_person','different_people')", name="status"),
        {"schema": "mdm"},
    )

    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(String(500))


class PersonMerge(Base, TenantScoped):
    """A merge, reversible from its snapshot (§4)."""

    __tablename__ = "person_merge"
    __audit_entity__ = "person_merge"
    __table_args__ = (
        Index(None, "tenant_id", "survivor_id"),
        Index(None, "tenant_id", "merged_id"),
        CheckConstraint("status IN ('applied','reversed')", name="status"),
        {"schema": "mdm"},
    )

    survivor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    merged_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("mdm.person.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="applied")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mdm.match_review.id"))
    reversed_by: Mapped[uuid.UUID | None]
    reversed_at: Mapped[datetime | None]
