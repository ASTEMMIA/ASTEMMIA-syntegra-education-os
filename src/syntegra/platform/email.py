"""Transactional email (ADR-015, ADR-017): queue in the caller's transaction, deliver afterwards.

* ``SesEmailSender`` delivers through Amazon SES (API v2) in the residency region. Credentials come
  from the standard AWS chain (the ECS task role in production), never from settings.
* ``DevEmailSender`` logs and keeps messages in memory (dev/test). Staging and production refuse
  to start with it.

Failures are classified so the dispatcher does the right thing:

* ``PermanentSendError``: this message can never be sent (rejected, bad address). It is not retried.
* ``ThrottledError``: SES is pacing us or the account is paused. The batch stops and every
  message waits for the next run.
* Anything else: retried with exponential backoff, up to ``MAX_ATTEMPTS``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from syntegra.core.clock import utcnow
from syntegra.platform.models import EmailSuppression, OutboundEmail
from syntegra.platform.session import PLATFORM_BYPASS
from syntegra.platform.settings import Settings

if TYPE_CHECKING:
    from mypy_boto3_sesv2 import SESV2Client

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 8
SENT_BODY = "[sent; body removed]"
GAVE_UP = "[not sent; body removed]"
FIRST_RETRY = timedelta(minutes=1)  # then 2, 4, 8 … minutes, capped at a day
MAX_RETRY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    body: str
    sender: str
    purpose: str = ""


class PermanentSendError(Exception):
    """This message can never be delivered as it stands; do not retry it."""


class ThrottledError(Exception):
    """The provider is pacing us (or sending is paused): stop and try the batch later."""


class EmailSender(Protocol):
    async def send(self, message: Message) -> str | None:
        """Deliver one message; return the provider's message id, if it has one."""
        ...


class DevEmailSender:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    async def send(self, message: Message) -> str | None:
        self.sent.append(message)
        log.info("dev email to %s: %s", message.to, message.subject)
        return None


# SES API v2 errors. Anything not listed is treated as temporary and retried with backoff.
_PERMANENT = frozenset(
    {
        "MessageRejected",
        "BadRequestException",
        "MailFromDomainNotVerifiedException",
        "NotFoundException",
    }
)
_THROTTLED = frozenset(
    {
        "TooManyRequestsException",
        "LimitExceededException",
        "SendingPausedException",
        "AccountSuspendedException",
        "Throttling",
        "ThrottlingException",
    }
)


def _tag(value: str) -> str:
    # SES tag values allow ASCII letters, digits, '_', '-', '.', '@' only.
    clean = "".join(c if c.isascii() and (c.isalnum() or c in "_-.@") else "_" for c in value)
    return clean[:256] or "none"


class SesEmailSender:
    """Amazon SES API v2 ``SendEmail``. The client is blocking (boto3), so each call runs in a
    worker thread; boto3 itself retries transient network and 5xx errors."""

    def __init__(self, client: SESV2Client, *, configuration_set: str | None = None) -> None:
        self._client = client
        self._configuration_set = configuration_set

    @classmethod
    def from_settings(cls, settings: Settings) -> SesEmailSender:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "sesv2",
            region_name=settings.ses_region or settings.residency_region,
            config=Config(
                retries={"mode": "standard", "max_attempts": 3},
                connect_timeout=5,
                read_timeout=10,
            ),
        )
        return cls(client, configuration_set=settings.ses_configuration_set)

    def _request(self, m: Message) -> dict[str, Any]:
        req: dict[str, Any] = {
            "FromEmailAddress": m.sender,
            "Destination": {"ToAddresses": [m.to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": m.subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": m.body, "Charset": "UTF-8"}},
                }
            },
            "EmailTags": [{"Name": "purpose", "Value": _tag(m.purpose)}],
        }
        if self._configuration_set:
            req["ConfigurationSetName"] = self._configuration_set
        return req

    async def send(self, message: Message) -> str | None:
        from botocore.exceptions import ClientError

        try:
            out = await asyncio.to_thread(self._client.send_email, **self._request(message))
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in _PERMANENT:
                raise PermanentSendError(f"SES {code}") from exc
            if code in _THROTTLED:
                raise ThrottledError(f"SES {code}") from exc
            raise
        return str(out.get("MessageId")) if out.get("MessageId") else None


def build_sender(settings: Settings) -> EmailSender:
    if settings.email_sender == "ses":
        return SesEmailSender.from_settings(settings)
    if settings.env not in ("dev", "test"):
        raise RuntimeError(
            "a real email sender is required outside dev/test (SYNTEGRA_EMAIL_SENDER=ses)"
        )
    return DevEmailSender()


def backoff(attempts: int) -> timedelta:
    """Wait before the next try after ``attempts`` failures: 1, 2, 4 … minutes, at most a day."""
    delay: timedelta = FIRST_RETRY * (2 ** min(max(attempts - 1, 0), 20))
    return delay if delay < MAX_RETRY else MAX_RETRY


def address_hash(address: str) -> bytes:
    """How a suppressed address is stored and looked up (ADR-018): never the address itself."""
    return hashlib.sha256(b"syntegra-email|" + address.strip().lower().encode()).digest()


async def is_suppressed(session: AsyncSession, address: str) -> str | None:
    """'bounce' or 'complaint' if we must not email this address, else None."""
    reason = (
        await session.execute(
            select(EmailSuppression.reason)
            .where(EmailSuppression.address_hash == address_hash(address))
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one_or_none()
    return reason


def queue_email(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    purpose: str,
    to: str,
    subject: str,
    body: str,
    ref: str | None = None,
) -> OutboundEmail:
    row = OutboundEmail(
        tenant_id=tenant_id, purpose=purpose, to_address=to, subject=subject, body=body, ref=ref
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
    """Deliver one batch of due email; returns how many were sent."""
    now = utcnow()
    async with sessionmaker() as session, session.begin():
        stmt = (
            select(OutboundEmail)
            .where(
                OutboundEmail.sent_at.is_(None),
                OutboundEmail.attempts < MAX_ATTEMPTS,
                or_(OutboundEmail.next_attempt_at.is_(None), OutboundEmail.next_attempt_at <= now),
            )
            .order_by(OutboundEmail.id)
            .limit(batch)
            .execution_options(**{PLATFORM_BYPASS: True})
        )
        if session.get_bind().dialect.name == "mysql":
            stmt = stmt.with_for_update(skip_locked=True)
        rows: Sequence[OutboundEmail] = (await session.execute(stmt)).scalars().all()
        sent = 0
        suppressed = (
            {
                h
                for (h,) in (
                    await session.execute(
                        select(EmailSuppression.address_hash)
                        .where(
                            EmailSuppression.address_hash.in_(
                                {address_hash(r.to_address) for r in rows}
                            )
                        )
                        .execution_options(**{PLATFORM_BYPASS: True})
                    )
                ).all()
            }
            if rows
            else set()
        )
        for row in rows:
            if address_hash(row.to_address) in suppressed:
                # A hard bounce or complaint stopped this address: never send (ADR-018).
                row.attempts, row.body = MAX_ATTEMPTS, GAVE_UP
                row.delivery_status, row.last_error = "suppressed", "address is suppressed"
                continue
            message = Message(row.to_address, row.subject, row.body, from_address, row.purpose)
            try:
                message_id = await sender.send(message)
            except ThrottledError as exc:
                # Not this message's fault: leave it and the rest for the next run.
                row.last_error = str(exc)[:500]
                log.warning("email sending paused: %s", exc)
                break
            except PermanentSendError as exc:
                row.attempts = MAX_ATTEMPTS
                row.delivery_status = "rejected"
                row.last_error = str(exc)[:500]
                row.body = GAVE_UP  # it may hold a one-time code
                log.warning("email %s rejected: %s", row.id, exc)
                continue
            except Exception as exc:
                row.attempts += 1
                row.last_error = repr(exc)[:500]
                if row.attempts >= MAX_ATTEMPTS:
                    row.body = GAVE_UP
                else:
                    row.next_attempt_at = utcnow() + backoff(row.attempts)
                continue
            row.attempts += 1
            row.sent_at, row.body = utcnow(), SENT_BODY
            row.provider_message_id = message_id
            row.delivery_status = "sent"
            row.last_error = None
            sent += 1
        return sent
