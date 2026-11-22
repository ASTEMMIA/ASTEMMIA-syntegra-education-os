"""ops schema: audit_event, audit_chain_head, outbox_event, idempotency_record (§6.2, §11.1)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from syntegra.core.clock import utcnow
from syntegra.core.ids import uuid7
from syntegra.platform.db import Base, Evidential, Hash32, Text_


class AuditEvent(Base, Evidential):
    """Append-only, hash-chained per tenant per day. Partitioned daily on MySQL (§11.3)."""

    __tablename__ = "audit_event"
    __table_args__ = (
        UniqueConstraint("tenant_id", "chain_date", "seq"),
        Index(None, "tenant_id", "entity_type", "entity_id"),
        Index(None, "tenant_id", "actor_id", "occurred_at"),
        {"schema": "ops"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Partition column: part of the PK because every unique key must include it (§11.3).
    chain_date: Mapped[date] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    actor_id: Mapped[uuid.UUID | None]
    on_behalf_of: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None]
    scope_node_id: Mapped[uuid.UUID | None]
    purpose: Mapped[str | None] = mapped_column(String(64))
    before_hash: Mapped[bytes | None] = mapped_column(Hash32())
    after_hash: Mapped[bytes | None] = mapped_column(Hash32())
    prev_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False)
    event_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    device_id: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[uuid.UUID | None]
    changed_fields: Mapped[list[str] | None] = mapped_column(JSON)

    def hash_view(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "chain_date": self.chain_date,
            "seq": self.seq,
            "occurred_at": self.occurred_at,
            "actor_id": self.actor_id,
            "on_behalf_of": self.on_behalf_of,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "scope_node_id": self.scope_node_id,
            "purpose": self.purpose,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "ip": self.ip,
            "device_id": self.device_id,
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
        }


class AuditChainHead(Base):
    """Serialisation point for a tenant-day chain (row-locked FOR UPDATE on append)."""

    __tablename__ = "audit_chain_head"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012 - SQLAlchemy declarative attribute

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    chain_date: Mapped[date] = mapped_column(primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False)
    closed_at: Mapped[datetime | None]
    anchored_s3_key: Mapped[str | None] = mapped_column(String(512))


class OutboxEvent(Base):
    """Written in the same transaction as the change (P9, §10.1)."""

    __tablename__ = "outbox_event"
    __table_args__ = (
        Index(None, "published_at", "id"),
        Index(None, "aggregate_type", "aggregate_id"),
        {"schema": "ops"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # = event_id
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scope_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[uuid.UUID | None]
    correlation_id: Mapped[uuid.UUID | None]
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    published_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text_())


class IdempotencyRecord(Base):
    """Idempotency-Key replay store for POST (§10.4)."""

    __tablename__ = "idempotency_record"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "idem_key"), {"schema": "ops"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class IdSequence(Base):
    """Gap-tolerant, never-reused counters for human-facing IDs (INS-, LRN-, STF-; §7.1)."""

    __tablename__ = "id_sequence"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012 - SQLAlchemy declarative attribute

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class BulkJob(Base):
    """A bulk operation: preview → validate → confirm → execute → report (§10.4, §14.3)."""

    __tablename__ = "bulk_job"
    __table_args__ = (
        Index(None, "tenant_id", "created_at"),
        {"schema": "ops"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scope_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # previewed | committed
    source_name: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    committed_by: Mapped[uuid.UUID | None]
    committed_at: Mapped[datetime | None]


class BulkJobRow(Base):
    __tablename__ = "bulk_job_row"
    __table_args__ = {"schema": "ops"}  # noqa: RUF012 - SQLAlchemy declarative attribute

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ops.bulk_job.id"), primary_key=True)
    row_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # valid | error | created | skipped
    messages: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    result_id: Mapped[uuid.UUID | None]


class OutboundEmail(Base):
    """Email written in the same transaction as the change that needs it (like the outbox), then
    delivered by the email dispatcher. The body is wiped once sent: it may hold a one-time code."""

    __tablename__ = "outbound_email"
    __table_args__ = (
        Index(None, "sent_at", "id"),
        {"schema": "ops"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    purpose: Mapped[str] = mapped_column(String(48), nullable=False)
    to_address: Mapped[str] = mapped_column(String(254), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text_(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    sent_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    next_attempt_at: Mapped[datetime | None]  # backoff after a temporary failure (ADR-017)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), unique=True)  # SES id
    # What the email is about, e.g. an application reference, so its delivery can be shown there.
    ref: Mapped[str | None] = mapped_column(String(64), index=True)
    # sent → delivered | delayed | soft_bounced | bounced | complained | rejected | suppressed
    delivery_status: Mapped[str | None] = mapped_column(String(16))


class EmailEvent(Base):
    """What SES reported about one message to one recipient (ADR-018): delivery, bounce,
    complaint, delay or reject. Kept as evidence of delivery; written once per event."""

    __tablename__ = "email_event"
    __table_args__ = (Index(None, "outbound_email_id"), {"schema": "ops"})

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    outbound_email_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ops.outbound_email.id"), nullable=False
    )
    event_key: Mapped[bytes] = mapped_column(Hash32(), nullable=False, unique=True)  # dedupe
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(64))  # e.g. Permanent/General, abuse
    detail: Mapped[str | None] = mapped_column(String(500))  # diagnostic, truncated
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    received_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)


class EmailSuppression(Base):
    """Addresses we must stop emailing after a hard bounce or a complaint (ADR-018). Only a
    hash of the normalised address is kept, enough to check it, not to read it back."""

    __tablename__ = "email_suppression"
    __table_args__ = (
        CheckConstraint("reason IN ('bounce','complaint')", name="reason"),
        {"schema": "ops"},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    address_hash: Mapped[bytes] = mapped_column(Hash32(), nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    events: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    last_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
