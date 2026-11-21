"""Transactional outbox and relay (P9, §10.1, §20).

``publish`` writes the event row in the caller's transaction. The relay reads unpublished
rows (``FOR UPDATE SKIP LOCKED`` on MySQL so several relays can run), hands them to a
``Publisher`` (Amazon MSK in production) and marks them published. Delivery is
at-least-once; consumers dedupe on event_id.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from syntegra.core.clock import utcnow
from syntegra.core.events import EventEnvelope
from syntegra.platform.models import OutboxEvent

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 10  # after this the row is parked for the dead-letter replay tool


def publish(session: Session | AsyncSession, envelope: EventEnvelope) -> OutboxEvent:
    row = OutboxEvent(
        id=envelope.event_id,
        tenant_id=envelope.tenant_id,
        scope_node_id=envelope.scope_node_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        event_type=envelope.event_type,
        topic=envelope.topic,
        payload={str(k): v for k, v in envelope.payload.items()},
        schema_version=envelope.schema_version,
        actor_id=envelope.actor_id,
        correlation_id=envelope.correlation_id,
        occurred_at=envelope.occurred_at.replace(tzinfo=None),
        attempts=0,
    )
    session.add(row)
    return row


class Publisher(Protocol):
    async def send(self, topic: str, key: str, value: OutboxEvent) -> None: ...


class InMemoryPublisher:
    """Dev/test publisher. Production uses an MSK (Kafka) publisher in integration/."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, OutboxEvent]] = []

    async def send(self, topic: str, key: str, value: OutboxEvent) -> None:
        self.sent.append((topic, key, value))


async def relay_once(
    sessionmaker: async_sessionmaker[AsyncSession], publisher: Publisher, *, batch: int = 100
) -> int:
    """Publish one batch; returns how many rows were marked published."""
    from syntegra.platform.session import PLATFORM_BYPASS

    async with sessionmaker() as session, session.begin():
        stmt = (
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.attempts < MAX_ATTEMPTS)
            .order_by(OutboxEvent.id)  # UUIDv7: time order, preserves per-aggregate order
            .limit(batch)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
        if session.get_bind().dialect.name == "mysql":
            stmt = stmt.with_for_update(skip_locked=True)
        rows: Sequence[OutboxEvent] = (await session.execute(stmt)).scalars().all()
        published = 0
        blocked_aggregates: set[object] = set()
        for row in rows:
            if row.aggregate_id in blocked_aggregates:
                continue  # never overtake an earlier failed event of the same aggregate
            try:
                await publisher.send(row.topic, str(row.aggregate_id), row)
            except Exception as exc:
                row.attempts += 1
                row.last_error = repr(exc)[:4000]
                blocked_aggregates.add(row.aggregate_id)
                log.warning("outbox publish failed", extra={"event_id": str(row.id)})
                continue
            row.published_at = utcnow()
            row.attempts += 1
            published += 1
        return published
