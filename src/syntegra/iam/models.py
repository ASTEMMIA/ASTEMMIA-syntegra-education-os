"""iam schema: app_user, role, role_permission, access_grant (§11.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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

from syntegra.core.clock import utcnow
from syntegra.core.ids import uuid7
from syntegra.platform.db import Audited, Base, Hash32


def _varbinary(n: int) -> Any:
    return LargeBinary(n).with_variant(mysql.VARBINARY(n), "mysql")


class AppUser(Base, Audited):
    __tablename__ = "app_user"
    __audit_entity__ = "app_user"
    __table_args__ = (
        UniqueConstraint("tenant_id", "login"),
        # "sub" is unique only per identity provider, and one person may hold accounts in
        # several organisations behind the same provider (ADR-016).
        UniqueConstraint("tenant_id", "idp_subject"),
        {"schema": "iam"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    person_id: Mapped[uuid.UUID | None]  # mdm.person, linked in Phase 1
    login: Mapped[str] = mapped_column(String(255), nullable=False)  # utf8mb4_0900_ai_ci on MySQL
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # sha256(issuer)[:16] + ":" + OIDC "sub", set at the first single sign-on (ADR-016)
    idp_subject: Mapped[str | None] = mapped_column(String(255))
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
    method: Mapped[str] = mapped_column(String(16), nullable=False)  # dev | oidc | email_code
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


class MfaCredential(Base, Audited):
    """A user's authenticator app (TOTP, ADR-016). The shared secret is field-encrypted with
    AAD bound to this row; the last accepted time step stops any code being used twice."""

    __tablename__ = "mfa_credential"
    __audit_entity__ = "mfa_credential"
    __audit_exclude__ = frozenset({"secret_enc"})
    __table_args__ = (
        UniqueConstraint("user_id", "kind"),
        CheckConstraint("kind IN ('totp')", name="kind"),
        {"schema": "iam"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.app_user.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="totp")
    secret_enc: Mapped[bytes] = mapped_column(_varbinary(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None]  # NULL until the user proves the app works
    last_step: Mapped[int | None] = mapped_column(BigInteger)
    last_used_at: Mapped[datetime | None]
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None]

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return None


class RecoveryCode(Base, Audited):
    """One single-use recovery code. Only a SHA-256 is stored; the code is shown once."""

    __tablename__ = "recovery_code"
    __audit_entity__ = "recovery_code"
    __audit_exclude__ = frozenset({"code_hash"})
    __table_args__ = (Index(None, "user_id"), {"schema": "iam"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.app_user.id"), nullable=False)
    code_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    used_at: Mapped[datetime | None]

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return None


class OidcFlow(Base):
    """One browser's single sign-on attempt, between leaving for the identity provider and
    coming back (ADR-016). Lives ten minutes and can be completed once. Only the state's hash is
    stored; the PKCE verifier is field-encrypted. Not audited: the sign-in it leads to is."""

    __tablename__ = "oidc_flow"
    __table_args__ = (Index(None, "expires_at"), {"schema": "iam"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    idp_key: Mapped[str] = mapped_column(String(32), nullable=False)
    state_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False, unique=True)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_enc: Mapped[bytes] = mapped_column(_varbinary(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]  # MFA finished: the challenge cannot be reused


class EmailSignInCode(Base):
    """A one-time sign-in code sent by email (ADR-030). Parents only: staff sign in through
    their organisation's single sign-on with MFA. Only a hash of the code is kept."""

    __tablename__ = "email_sign_in_code"
    __table_args__ = (Index(None, "tenant_id", "user_id", "created_at"), {"schema": "iam"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.app_user.id"), nullable=False)
    code_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_at: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(String(45))
