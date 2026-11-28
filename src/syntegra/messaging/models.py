"""Messaging tables (msg schema, CEDA §26, ADR-030).

Conversations are reached through membership, not through the scope hierarchy: a parent, a
teacher and an ASUBEB officer can share one. So these tables carry ``tenant_id`` and are always
filtered by it explicitly, rather than being scope-filtered like TenantScoped rows. Message
bodies and attachment names are encrypted (AES-256-GCM, envelope keys); files are stored
encrypted in the document store.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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
from syntegra.platform.db import Audited, Base, Evidential

S = "msg"


def Blob() -> LargeBinary:
    return LargeBinary(70_000).with_variant(mysql.MEDIUMBLOB(), "mysql")


class Conversation(Base, Audited):
    __tablename__ = "conversation"
    __audit_entity__ = "conversation"
    __audit_exclude__ = frozenset({"next_seq", "last_message_at"})
    __table_args__ = (
        UniqueConstraint("tenant_id", "direct_key"),
        CheckConstraint(
            "kind IN ('direct','group','class','staff','authority','announcement')", name="kind"
        ),
        CheckConstraint("comm_class IN ('institutional','protected')", name="comm_class"),
        CheckConstraint("posting IN ('all','admins')", name="posting"),
        Index(None, "tenant_id", "last_message_at"),
        {"schema": S},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scope_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)  # whose oversight
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str | None] = mapped_column(String(120))
    comm_class: Mapped[str] = mapped_column(String(14), nullable=False, default="institutional")
    posting: Mapped[str] = mapped_column(String(6), nullable=False, default="all")
    institution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("org.institution.id"))
    class_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("acad.class_group.id"))
    direct_key: Mapped[str | None] = mapped_column(String(73))  # sorted user ids, direct only
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_message_at: Mapped[datetime | None]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class ConversationMember(Base, Audited):
    __tablename__ = "conversation_member"
    __audit_entity__ = "conversation_member"
    __audit_exclude__ = frozenset({"last_read_seq"})
    __table_args__ = (
        UniqueConstraint("tenant_id", "conversation_id", "user_id"),
        CheckConstraint("role IN ('owner','admin','member','read_only')", name="role"),
        Index(None, "tenant_id", "user_id"),
        {"schema": S},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("msg.conversation.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("iam.app_user.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="member")
    derived: Mapped[bool] = mapped_column(nullable=False, default=False)  # from class, posts…
    joined_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    left_at: Mapped[datetime | None]
    last_read_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    muted: Mapped[bool] = mapped_column(nullable=False, default=False)


class Message(Base):
    """One message. Never deleted: "delete for everyone" hides it from members, and the
    encrypted body stays for authorised review (institutional record, CEDA §26.2)."""

    __tablename__ = "message"
    __table_args__ = (
        UniqueConstraint("tenant_id", "conversation_id", "seq"),
        CheckConstraint("kind IN ('text','file','system')", name="kind"),
        {"schema": S},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("msg.conversation.id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_user_id: Mapped[uuid.UUID | None]
    sent_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    kind: Mapped[str] = mapped_column(String(6), nullable=False, default="text")
    body_enc: Mapped[bytes | None] = mapped_column(Blob())
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("msg.message.id"))
    edited_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[uuid.UUID | None]


class MessageEdit(Base, Evidential):
    """The earlier text of an edited message, kept."""

    __tablename__ = "message_edit"
    __table_args__ = (Index(None, "tenant_id", "message_id"), {"schema": S})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("msg.message.id"), nullable=False)
    body_enc: Mapped[bytes] = mapped_column(Blob(), nullable=False)
    edited_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class MessageAttachment(Base):
    __tablename__ = "message_attachment"
    __table_args__ = (
        UniqueConstraint("tenant_id", "blob_key"),
        CheckConstraint("scan_status IN ('not_scanned','clean','infected')", name="scan_status"),
        Index(None, "tenant_id", "message_id"),
        {"schema": S},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("msg.message.id"), nullable=False)
    blob_key: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    name_enc: Mapped[bytes] = mapped_column(LargeBinary(1024), nullable=False)
    scan_status: Mapped[str] = mapped_column(String(12), nullable=False, default="not_scanned")


class MessageReaction(Base):
    __tablename__ = "message_reaction"
    __table_args__ = (
        UniqueConstraint("tenant_id", "message_id", "user_id", "emoji"),
        {"schema": S},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("msg.message.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    emoji: Mapped[str] = mapped_column(String(8), nullable=False)


class MessageReport(Base, Audited):
    """A report of an abusive or worrying message, for the moderation queue."""

    __tablename__ = "message_report"
    __audit_entity__ = "message_report"
    __table_args__ = (
        CheckConstraint("status IN ('open','dismissed','actioned')", name="status"),
        Index(None, "tenant_id", "status"),
        {"schema": S},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scope_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("msg.message.id"), nullable=False)
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    handled_by: Mapped[uuid.UUID | None]
    handled_at: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class ConversationAccess(Base, Evidential):
    """An authorised officer opening a conversation they are not in, and why. Never edited."""

    __tablename__ = "conversation_access"
    __table_args__ = (Index(None, "tenant_id", "conversation_id"), {"schema": S})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("msg.conversation.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(255), nullable=False)
    at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
