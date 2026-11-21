"""gov schema tables: tenant, scope_node, scope_closure, policy_rule (§11.1)."""

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
    Select,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.core.clock import utcnow
from syntegra.core.ids import uuid7
from syntegra.platform.db import Audited, Base, Text_
from syntegra.platform.session import register_scope_subtree


class Tenant(Base, Audited):
    """A data, configuration and billing boundary pinned to one residency region (§2)."""

    __tablename__ = "tenant"
    __audit_entity__ = "tenant"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('global','country','regional','network','institutional')", name="kind"
        ),
        {"schema": "gov"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gov.tenant.id"))
    root_scope_node_id: Mapped[uuid.UUID | None]
    country: Mapped[str | None] = mapped_column(String(2))
    residency_region: Mapped[str] = mapped_column(String(32), nullable=False)
    ownership: Mapped[str] = mapped_column(String(16), nullable=False, default="n/a")
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    default_locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)

    def audit_tenant_id(self) -> uuid.UUID:
        return self.id

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return self.root_scope_node_id


class ScopeNode(Base, Audited):
    """Governance placement. Visibility is a subtree question (P5)."""

    __tablename__ = "scope_node"
    __audit_entity__ = "scope_node"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        Index(None, "tenant_id", "path"),
        CheckConstraint("level BETWEEN 1 AND 9", name="level"),
        {"schema": "gov"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gov.tenant.id"), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gov.scope_node.id"))
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    local_label: Mapped[str | None] = mapped_column(
        String(64)
    )  # e.g. "LGEA", "Multi-Academy Trust"
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    is_tenant_root: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return self.id


class ScopeClosure(Base):
    """scope_closure(ancestor_id, descendant_id, depth) for subtree checks (§11.2)."""

    __tablename__ = "scope_closure"
    __table_args__ = (Index(None, "descendant_id", "ancestor_id"), {"schema": "gov"})

    ancestor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gov.scope_node.id"), primary_key=True
    )
    descendant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gov.scope_node.id"), primary_key=True
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False)


class PolicyRule(Base, Audited):
    """Versioned rule owned by a governance level (§29.1)."""

    __tablename__ = "policy_rule"
    __audit_entity__ = "policy_rule"
    __table_args__ = (
        UniqueConstraint("tenant_id", "owner_scope_id", "rule_key", "version"),
        CheckConstraint("status IN ('draft','published','withdrawn','superseded')", name="status"),
        {"schema": "gov"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    owner_scope_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gov.scope_node.id"), nullable=False
    )
    rule_key: Mapped[str] = mapped_column(String(32), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    expression: Mapped[str] = mapped_column(Text_(), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    strictness: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    test_cases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    mandatory_below: Mapped[bool] = mapped_column(nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_to: Mapped[date | None]
    published_at: Mapped[datetime | None]
    published_by: Mapped[uuid.UUID | None]

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return self.owner_scope_id


def subtree_of(ancestor_id: uuid.UUID) -> Select[Any]:
    return select(ScopeClosure.descendant_id).where(ScopeClosure.ancestor_id == ancestor_id)


register_scope_subtree(subtree_of)


from syntegra.platform.db import EffectiveDated, TenantScoped  # noqa: E402


class ConfigSetting(Base, TenantScoped, EffectiveDated):
    """Jurisdiction and institution differences are configuration data (P4).

    Never overwritten: a new value end-dates the previous version (P7).
    Resolution is nearest-scope-wins up the hierarchy.
    """

    __tablename__ = "config_setting"
    __audit_entity__ = "config_setting"
    __table_args__ = (
        Index(None, "tenant_id", "scope_node_id", "setting_key", "valid_from"),
        {"schema": "gov"},
    )

    setting_key: Mapped[str] = mapped_column(String(96), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    locked_below: Mapped[bool] = mapped_column(nullable=False, default=False)
