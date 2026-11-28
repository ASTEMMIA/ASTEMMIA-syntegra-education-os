"""iam schema: app_user, role, role_permission, access_grant (§11.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.core.clock import utcnow
from syntegra.core.ids import uuid7
from syntegra.platform.db import Audited, Base, Hash32


class AppUser(Base, Audited):
    __tablename__ = "app_user"
    __audit_entity__ = "app_user"
    __table_args__ = (UniqueConstraint("tenant_id", "login"), {"schema": "iam"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    person_id: Mapped[uuid.UUID | None]  # mdm.person, linked in Phase 1
    login: Mapped[str] = mapped_column(String(255), nullable=False)  # utf8mb4_0900_ai_ci on MySQL
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    idp_subject: Mapped[str | None] = mapped_column(String(255), unique=True)  # OIDC "sub"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    mfa_required: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class Role(Base, Audited):
    __tablename__ = "role"
    __audit_entity__ = "role"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint("clearance BETWEEN 1 AND 5", name="clearance"),
        {"schema": "iam"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID | None]  # NULL = platform built-in
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    builtin: Mapped[bool] = mapped_column(nullable=False, default=False)
    clearance: Mapped[int] = mapped_column(Integer, nullable=False)
    cloned_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam.role.id"))

    def audit_tenant_id(self) -> uuid.UUID:
        # Built-ins are audited on the nil-UUID platform chain.
        return self.tenant_id or uuid.UUID(int=0)

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return None


class RolePermission(Base):
    __tablename__ = "role_permission"
    __table_args__ = (
        CheckConstraint("cell IN ('Y','P','C','A')", name="cell"),
        {"schema": "iam"},
    )

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.role.id"), primary_key=True)
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)
    cell: Mapped[str] = mapped_column(String(1), nullable=False)


class AccessGrant(Base, Audited):
    """A grant is role x scope node x validity period x clearance cap (§8.2)."""

    __tablename__ = "access_grant"
    __audit_entity__ = "access_grant"
    __table_args__ = (
        Index(None, "tenant_id", "user_id"),
        CheckConstraint("clearance_cap BETWEEN 1 AND 5", name="clearance_cap"),
        {"schema": "iam"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.app_user.id"), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.role.id"), nullable=False)
    scope_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    clearance_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_to: Mapped[date | None]
    granted_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None]


class AuthSession(Base, Audited):
    """A signed-in session: the family that every rotated refresh token belongs to (§8.3)."""

    __tablename__ = "auth_session"
    __audit_entity__ = "auth_session"
    __table_args__ = (Index(None, "tenant_id", "user_id"), {"schema": "iam"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.app_user.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # dev | oidc
    amr: Mapped[str] = mapped_column(String(64), nullable=False)  # space-separated (RFC 8176)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    absolute_expires_at: Mapped[datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[datetime | None]
    revoke_reason: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return None


class RefreshToken(Base, Audited):
    """One link in a session's rotation chain. Only the SHA-256 of the token is stored."""

    __tablename__ = "refresh_token"
    __audit_entity__ = "refresh_token"
    __audit_exclude__ = frozenset({"token_hash"})
    __table_args__ = (Index(None, "tenant_id", "session_id"), {"schema": "iam"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.auth_session.id"), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]
    replaced_by_id: Mapped[uuid.UUID | None]

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return None
