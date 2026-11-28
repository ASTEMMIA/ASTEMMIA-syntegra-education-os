"""Bounce, complaint and delivery feedback from Amazon SES (ADR-018).

    SES (configuration set) ─► SNS topic ─► SQS queue (+ dead-letter queue) ─► poll_once

No public endpoint: the worker pulls from a queue that only the SNS topic may write to (queue
policy) and only the worker's role may read. Events arrive at least once and in any order, so:

* each (message, recipient, event) is recorded once (``event_key``);
* the email's ``delivery_status`` only moves to a more decisive state (a late "delayed" never
  overwrites "bounced");
* events for messages this system did not send, or for recipients it did not send to, are ignored.

A permanent bounce or a complaint suppresses the address (stored as a hash): the dispatcher never
emails it again, and forms that would email it say so up front.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from syntegra.core.clock import utcnow
from syntegra.platform.email import address_hash
from syntegra.platform.models import EmailEvent, EmailSuppression, OutboundEmail
from syntegra.platform.session import PLATFORM_BYPASS

if TYPE_CHECKING:
    from mypy_boto3_sqs import SQSClient

log = logging.getLogger(__name__)

# How decisive each delivery state is; a status only ever moves up this list.
RANK = {
    None: -1,
    "sent": 0,
    "delayed": 1,
    "soft_bounced": 2,
    "delivered": 3,
    "rejected": 4,
    "suppressed": 4,
    "bounced": 5,
    "complained": 6,
}
UNDELIVERABLE = frozenset({"bounced", "complained", "rejected", "suppressed"})


class FeedbackError(ValueError):
    """The message is not a usable SES event; it is left for the dead-letter queue."""


@dataclass(frozen=True, slots=True)
class Recipient:
    address: str
    detail: str | None


@dataclass(frozen=True, slots=True)
class SesEvent:
    kind: str  # delivery | bounce | complaint | delay | reject
    status: str  # the delivery_status it implies
    message_id: str
    recipients: tuple[Recipient, ...]
    subtype: str | None
    occurred_at: datetime  # naive UTC
    feedback_id: str | None
    suppress: str | None  # 'bounce' | 'complaint' | None


def _when(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeedbackError(f"bad timestamp {value!r}") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _people(
    items: Any, *, detail_keys: tuple[str, ...] = ("diagnosticCode", "status")
) -> tuple[Recipient, ...]:
    out = []
    for item in items or []:
        if isinstance(item, str):
            out.append(Recipient(item.strip().lower(), None))
        elif isinstance(item, dict) and item.get("emailAddress"):
            detail = next((str(item[k]) for k in detail_keys if item.get(k)), None)
            out.append(Recipient(str(item["emailAddress"]).strip().lower(), detail))
    return tuple(out)


def parse(body: str) -> SesEvent | None:
    """One SQS message → one SES event, or None for events we do not act on (send, open, click,
    SNS subscription notices). Accepts SNS-wrapped, EventBridge and raw SES JSON."""
    try:
        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("Type") in (
            "SubscriptionConfirmation",
            "UnsubscribeConfirmation",
        ):
            return None
        if isinstance(payload, dict) and payload.get("Type") == "Notification":
            payload = json.loads(payload["Message"])
        if isinstance(payload, dict) and "detail-type" in payload:
            payload = payload["detail"]
    except (ValueError, KeyError, TypeError) as exc:
        raise FeedbackError("not JSON in a known envelope") from exc
    if not isinstance(payload, dict):
        raise FeedbackError("not a JSON object")
    # Event publishing uses eventType; identity notifications use notificationType.
    kind = str(payload.get("eventType") or payload.get("notificationType") or "")
    mail = payload.get("mail")
    if not isinstance(mail, dict) or not mail.get("messageId"):
        if kind in ("Bounce", "Complaint", "Delivery", "DeliveryDelay", "Reject"):
            raise FeedbackError(f"{kind} without mail.messageId")
        return None
    mid = str(mail["messageId"])
    try:
        if kind == "Bounce":
            b = payload["bounce"]
            permanent = b.get("bounceType") == "Permanent"
            return SesEvent(
                "bounce",
                "bounced" if permanent else "soft_bounced",
                mid,
                _people(b.get("bouncedRecipients")),
                f"{b.get('bounceType')}/{b.get('bounceSubType')}",
                _when(b["timestamp"]),
                b.get("feedbackId"),
                "bounce" if permanent else None,
            )
        if kind == "Complaint":
            c = payload["complaint"]
            return SesEvent(
                "complaint",
                "complained",
                mid,
                _people(c.get("complainedRecipients")),
                c.get("complaintFeedbackType") or c.get("complaintSubType"),
                _when(c["timestamp"]),
                c.get("feedbackId"),
                "complaint",
            )
        if kind == "Delivery":
            d = payload["delivery"]
            return SesEvent(
                "delivery",
                "delivered",
                mid,
                _people(d.get("recipients")),
                None,
                _when(d["timestamp"]),
                None,
                None,
            )
        if kind == "DeliveryDelay":
            dd = payload["deliveryDelay"]
            return SesEvent(
                "delay",
                "delayed",
                mid,
                _people(dd.get("delayedRecipients")),
                dd.get("delayType"),
                _when(dd["timestamp"]),
                None,
                None,
            )
        if kind == "Reject":
            r = payload.get("reject") or {}
            return SesEvent(
                "reject",
                "rejected",
                mid,
                _people(mail.get("destination")),
                r.get("reason"),
                _when(mail["timestamp"]),
                None,
                None,
            )
    except KeyError as exc:
        raise FeedbackError(f"{kind} is missing {exc}") from exc
    return None  # Send, Open, Click, Rendering Failure, Subscription: nothing to do


def _key(e: SesEvent, recipient: str) -> bytes:
    raw = "|".join([e.message_id, e.kind, recipient, e.feedback_id or e.occurred_at.isoformat()])
    return hashlib.sha256(raw.encode()).digest()


async def record(session: AsyncSession, event: SesEvent) -> str:
    """Apply one event. Returns 'recorded', 'duplicate' or 'unknown_message'."""
    bypass = {PLATFORM_BYPASS: True}
    email = (
        await session.execute(
            select(OutboundEmail)
            .where(OutboundEmail.provider_message_id == event.message_id)
            .with_for_update()
            .execution_options(**bypass)
        )
    ).scalar_one_or_none()
    if email is None:
        return "unknown_message"  # not ours (another sender on the account) or already purged
    to = email.to_address.strip().lower()
    outcome = "duplicate"
    for person in event.recipients:
        if person.address != to:
            continue  # SES may list others; we only ever send to one address
        key = _key(event, person.address)
        seen = (
            await session.execute(
                select(EmailEvent.id).where(EmailEvent.event_key == key).execution_options(**bypass)
            )
        ).first()
        if seen:
            continue
        session.add(
            EmailEvent(
                tenant_id=email.tenant_id,
                outbound_email_id=email.id,
                event_key=key,
                event_type=event.kind,
                subtype=(event.subtype or None) and str(event.subtype)[:64],
                detail=person.detail[:500] if person.detail else None,
                occurred_at=event.occurred_at,
            )
        )
        if RANK.get(event.status, -1) > RANK.get(email.delivery_status, -1):
            email.delivery_status = event.status
        if event.suppress:
            await _suppress(session, person.address, event.suppress)
        outcome = "recorded"
    if outcome == "duplicate" and not any(p.address == to for p in event.recipients):
        outcome = "unknown_message"
    await session.flush()
    return outcome


async def _suppress(session: AsyncSession, address: str, reason: str) -> None:
    h = address_hash(address)
    row = (
        await session.execute(
            select(EmailSuppression)
            .where(EmailSuppression.address_hash == h)
            .with_for_update()
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one_or_none()
    now = utcnow()
    if row is None:
        session.add(EmailSuppression(address_hash=h, reason=reason, first_at=now, last_at=now))
        return
    row.events += 1
    row.last_at = now
    if reason == "complaint":
        row.reason = "complaint"  # the stronger reason wins


async def unsuppress(session: AsyncSession, address: str) -> bool:
    """Allow an address again (for example, the mailbox was fixed). Returns whether it was
    suppressed. Complaints should only be lifted when the person asks for email again."""
    row = (
        await session.execute(
            select(EmailSuppression)
            .where(EmailSuppression.address_hash == address_hash(address))
            .execution_options(**{PLATFORM_BYPASS: True})
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    log.info("email address unsuppressed (was %s)", row.reason)
    return True


async def poll_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    sqs: SQSClient,
    queue_url: str,
    *,
    wait_seconds: int = 20,
) -> tuple[int, int]:
    """Receive up to 10 queued events and apply each in its own transaction.

    A message is deleted once applied (or found to be a duplicate, or not ours). One that cannot
    be parsed or applied is left alone: SQS redelivers it and, after the queue's maxReceiveCount,
    moves it to the dead-letter queue for a person to look at. Returns (handled, failed).
    """
    resp = await asyncio.to_thread(
        sqs.receive_message,
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=wait_seconds,
    )
    handled = failed = 0
    for msg in resp.get("Messages", []):
        try:
            event = parse(msg["Body"])
            if event is not None:
                try:
                    async with sessionmaker() as session, session.begin():
                        await record(session, event)
                except IntegrityError:
                    pass  # the same event, applied concurrently by another worker
        except Exception:
            log.exception("email feedback message %s not applied", msg.get("MessageId"))
            failed += 1
            continue
        await asyncio.to_thread(
            sqs.delete_message, QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"]
        )
        handled += 1
    return handled, failed
