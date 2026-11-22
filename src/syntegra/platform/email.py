"""Transactional email (ADR-015): queue in the caller's transaction, deliver afterwards.

``DevEmailSender`` logs and keeps messages in memory (dev/test). Production needs Amazon SES,
whose adapter is an open item, so production refuses to start with the dev sender.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from syntegra.core.clock import utcnow
from syntegra.platform.models import OutboundEmail
from syntegra.platform.session import PLATFORM_BYPASS
from syntegra.platform.settings import Settings

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 8
SENT_BODY = "[sent; body removed]"


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    body: str
    sender: str


class EmailSender(Protocol):
    async def send(self, message: Message) -> None: ...


class DevEmailSender:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    async def send(self, message: Message) -> None:
        self.sent.append(message)
        log.info("dev email to %s: %s", message.to, message.subject)


def build_sender(settings: Settings) -> EmailSender:
    if settings.email_sender == "ses":
        raise RuntimeError("the Amazon SES sender is not built yet (ADR-015 open item)")
    if settings.env not in ("dev", "test"):
        raise RuntimeError(
            "a real email sender is required outside dev/test (SYNTEGRA_EMAIL_SENDER)"
        )
    return DevEmailSender()


def queue_email(
    session: AsyncSession, *, tenant_id: uuid.UUID, purpose: str, to: str, subject: str, body: str
) -> OutboundEmail:
    row = OutboundEmail(
        tenant_id=tenant_id, purpose=purpose, to_address=to, subject=subject, body=body
    )
    session.add(row)
    return row


async def dispatch_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    sender: EmailSender,
    *,
    from_address: str,
    batch: int = 50,
) -> int:
    """Deliver one batch of queued email; returns how many were sent."""
    async with sessionmaker() as session, session.begin():
        stmt = (
            select(OutboundEmail)
            .where(OutboundEmail.sent_at.is_(None), OutboundEmail.attempts < MAX_ATTEMPTS)
            .order_by(OutboundEmail.id)
            .limit(batch)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
        if session.get_bind().dialect.name == "mysql":
            stmt = stmt.with_for_update(skip_locked=True)
        rows: Sequence[OutboundEmail] = (await session.execute(stmt)).scalars().all()
        sent = 0
        for row in rows:
            row.attempts += 1
            try:
                await sender.send(Message(row.to_address, row.subject, row.body, from_address))
            except Exception as exc:
                row.last_error = repr(exc)[:500]
                continue
            row.sent_at, row.body = utcnow(), SENT_BODY
            sent += 1
        return sent
