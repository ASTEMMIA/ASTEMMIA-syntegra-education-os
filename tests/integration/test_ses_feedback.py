"""SES bounce, complaint and delivery feedback (ADR-018)."""

import itertools
import json

import boto3
import pytest
from botocore.stub import Stubber
from sqlalchemy import func, select

from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.institutions import registration as reg
from syntegra.platform import email_feedback as fb
from syntegra.platform.email import GAVE_UP, dispatch_once, queue_email
from syntegra.platform.models import EmailEvent, EmailSuppression, OutboundEmail
from tests.integration.test_private_registration import (
    BASE,
    FORM,
    _apply,
    _officer,
    public,
    regulator,
)
from tests.integration.test_tertiary_register import _abia

TO = "ada.okeke@example.ng"


@pytest.fixture(autouse=True)
def _fresh_ids():
    IdSender._ids = itertools.count(1)


class IdSender:
    """Stands in for SES: returns a message id per message, like SES does."""

    _ids = itertools.count(1)  # SES message ids are unique account-wide

    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)
        return f"ses-{next(self._ids)}"


def ses_event(kind, message_id="ses-1", to=TO, ts="2026-11-22T10:00:00.000Z", **extra):
    mail = {"messageId": message_id, "timestamp": ts, "destination": [to]}
    body = {
        "Bounce": {
            "bounce": {
                "bounceType": "Permanent",
                "bounceSubType": "General",
                "bouncedRecipients": [
                    {"emailAddress": to, "diagnosticCode": "smtp; 550 5.1.1 user unknown"}
                ],
                "timestamp": ts,
                "feedbackId": f"fb-{message_id}",
            }
        },
        "Complaint": {
            "complaint": {
                "complainedRecipients": [{"emailAddress": to}],
                "complaintFeedbackType": "abuse",
                "timestamp": ts,
                "feedbackId": f"fc-{message_id}",
            }
        },
        "Delivery": {"delivery": {"recipients": [to], "timestamp": ts}},
        "DeliveryDelay": {
            "deliveryDelay": {
                "delayType": "MailboxFull",
                "delayedRecipients": [{"emailAddress": to}],
                "timestamp": ts,
            }
        },
        "Reject": {"reject": {"reason": "Bad content"}},
    }.get(kind, {})
    event = {"eventType": kind, "mail": mail} | body
    for k, v in extra.items():
        event[k] |= v
    return event


def sns(event):
    """How SES events arrive in SQS through SNS (raw message delivery off)."""
    return json.dumps({"Type": "Notification", "MessageId": "sns-1", "Message": json.dumps(event)})


async def _sent_email(sm, world, to=TO):
    async with sm() as s, s.begin():
        queue_email(
            s, tenant_id=world.abia_tenant, purpose="test", to=to, subject="Hello", body="Hi"
        )
    sender = IdSender()
    assert await dispatch_once(sm, sender, from_address="no-reply@example") == 1
    return sender


async def _apply_event(sm, event_json):
    async with sm() as s, s.begin():
        return await fb.record(s, fb.parse(event_json))


async def _email(sm):
    async with sm() as s:
        return (await s.execute(select(OutboundEmail))).scalar_one()


# --------------------------------------------------------------------------- parsing


def test_every_envelope_is_understood():
    bounce = fb.parse(sns(ses_event("Bounce")))
    assert (bounce.kind, bounce.status, bounce.suppress) == ("bounce", "bounced", "bounce")
    assert bounce.recipients[0].detail == "smtp; 550 5.1.1 user unknown"
    assert bounce.occurred_at.isoformat() == "2026-11-22T10:00:00"  # naive UTC
    eventbridge = json.dumps(
        {"detail-type": "Email Complained", "source": "aws.ses", "detail": ses_event("Complaint")}
    )
    assert fb.parse(eventbridge).status == "complained"
    raw_notification = ses_event("Delivery")
    raw_notification["notificationType"] = raw_notification.pop("eventType")
    assert fb.parse(json.dumps(raw_notification)).status == "delivered"
    soft = fb.parse(sns(ses_event("Bounce", bounce={"bounceType": "Transient"})))
    assert soft.status == "soft_bounced" and soft.suppress is None
    assert fb.parse(sns(ses_event("DeliveryDelay"))).subtype == "MailboxFull"
    assert fb.parse(sns(ses_event("Reject"))).recipients[0].address == TO


def test_events_we_do_not_act_on_and_broken_messages():
    assert fb.parse(sns(ses_event("Send"))) is None
    assert fb.parse(sns(ses_event("Open"))) is None
    assert fb.parse(json.dumps({"Type": "SubscriptionConfirmation", "SubscribeURL": "x"})) is None
    for bad in ("not json", "[1, 2]", sns({"eventType": "Bounce", "mail": {}})):
        with pytest.raises(fb.FeedbackError):
            fb.parse(bad)
    no_time = ses_event("Bounce")
    del no_time["bounce"]["timestamp"]
    with pytest.raises(fb.FeedbackError, match="timestamp"):
        fb.parse(sns(no_time))


# --------------------------------------------------------------------------- recording


async def test_delivery_is_recorded_once_and_a_late_delay_does_not_undo_it(sm, world):
    await _sent_email(sm, world)
    assert (await _email(sm)).delivery_status == "sent"
    assert await _apply_event(sm, sns(ses_event("Delivery"))) == "recorded"
    assert await _apply_event(sm, sns(ses_event("Delivery"))) == "duplicate"  # redelivered
    assert await _apply_event(sm, sns(ses_event("DeliveryDelay"))) == "recorded"
    assert (await _email(sm)).delivery_status == "delivered"
    async with sm() as s:
        kinds = (await s.execute(select(EmailEvent.event_type))).scalars().all()
    assert sorted(kinds) == ["delay", "delivery"]


async def test_hard_bounce_suppresses_the_address_by_hash_only(sm, world):
    await _sent_email(sm, world)
    assert await _apply_event(sm, sns(ses_event("Bounce"))) == "recorded"
    assert (await _email(sm)).delivery_status == "bounced"
    async with sm() as s:
        row = (await s.execute(select(EmailSuppression))).scalar_one()
        event = (await s.execute(select(EmailEvent))).scalar_one()
    assert row.reason == "bounce" and TO.encode() not in row.address_hash
    assert event.subtype == "Permanent/General" and "5.1.1" in event.detail

    # The dispatcher will not email it again, and does not even ask SES.
    async with sm() as s, s.begin():
        queue_email(
            s, tenant_id=world.abia_tenant, purpose="t", to=TO.upper(), subject="x", body="code"
        )
    sender = IdSender()
    assert await dispatch_once(sm, sender, from_address="no-reply@example") == 0
    assert sender.sent == []
    async with sm() as s:
        later = (
            await s.execute(select(OutboundEmail).order_by(OutboundEmail.id.desc()).limit(1))
        ).scalar_one()
    assert later.delivery_status == "suppressed" and later.body == GAVE_UP

    async with sm() as s, s.begin():
        assert await fb.unsuppress(s, TO) is True
        assert await fb.unsuppress(s, TO) is False
    async with sm() as s, s.begin():
        queue_email(s, tenant_id=world.abia_tenant, purpose="t", to=TO, subject="x", body="y")
    assert await dispatch_once(sm, sender, from_address="no-reply@example") == 1


async def test_soft_bounce_does_not_suppress_and_complaint_outranks_bounce(sm, world):
    await _sent_email(sm, world)
    soft = {"bounceType": "Transient", "feedbackId": "fb-soft"}  # each notice has its own id
    await _apply_event(sm, sns(ses_event("Bounce", bounce=soft)))
    async with sm() as s:
        assert (await s.execute(select(func.count()).select_from(EmailSuppression))).scalar() == 0
    assert (await _email(sm)).delivery_status == "soft_bounced"
    await _apply_event(sm, sns(ses_event("Bounce")))
    await _apply_event(sm, sns(ses_event("Complaint")))
    assert (await _email(sm)).delivery_status == "complained"
    async with sm() as s:
        row = (await s.execute(select(EmailSuppression))).scalar_one()
    assert row.reason == "complaint" and row.events == 2


async def test_events_for_other_messages_or_recipients_are_ignored(sm, world):
    await _sent_email(sm, world)
    assert (
        await _apply_event(sm, sns(ses_event("Bounce", message_id="not-ours"))) == "unknown_message"
    )
    assert (
        await _apply_event(sm, sns(ses_event("Bounce", to="someone@else.ng"))) == "unknown_message"
    )
    async with sm() as s:
        assert (await s.execute(select(func.count()).select_from(EmailEvent))).scalar() == 0
        assert (await s.execute(select(func.count()).select_from(EmailSuppression))).scalar() == 0


# --------------------------------------------------------------------------- the worker


async def test_worker_deletes_what_it_applied_and_leaves_the_rest_for_the_dlq(sm, world):
    await _sent_email(sm, world)
    sqs = boto3.client(
        "sqs", region_name="af-south-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    url = "https://sqs.af-south-1.amazonaws.com/123456789012/syntegra-ses-events"
    with Stubber(sqs) as stub:
        stub.add_response(
            "receive_message",
            {
                "Messages": [
                    {"MessageId": "1", "ReceiptHandle": "rh-1", "Body": sns(ses_event("Bounce"))},
                    {"MessageId": "2", "ReceiptHandle": "rh-2", "Body": "garbage"},
                    {"MessageId": "3", "ReceiptHandle": "rh-3", "Body": sns(ses_event("Send"))},
                ]
            },
            {"QueueUrl": url, "MaxNumberOfMessages": 10, "WaitTimeSeconds": 0},
        )
        stub.add_response("delete_message", {}, {"QueueUrl": url, "ReceiptHandle": "rh-1"})
        stub.add_response("delete_message", {}, {"QueueUrl": url, "ReceiptHandle": "rh-3"})
        assert await fb.poll_once(sm, sqs, url, wait_seconds=0) == (2, 1)
        stub.assert_no_pending_responses()
    assert (await _email(sm)).delivery_status == "bounced"


# --------------------------------------------------------------------------- registration


async def test_a_bounced_proprietor_email_is_visible_and_does_not_trap_the_applicant(sm, world):
    tenant, bodies = await _abia(sm, world)
    officer = await _officer(sm, tenant, bodies["ASUBEB"])
    ref = await _apply(sm, tenant)
    assert await dispatch_once(sm, IdSender(), from_address="no-reply@example") == 1
    async with sm() as s, s.begin():
        await fb.record(s, fb.parse(sns(ses_event("Bounce"))))

    async with sm() as s, s.begin(), regulator(tenant, bodies["ASUBEB"], officer):
        view = await reg.get_application(s, ref)
        assert view.email_delivery == "bounced" and not view.email_verified
    async with sm() as s, public(tenant):
        out = await reg.application_status(s, ref, TO)
        assert out["email_delivery"] == "failed" and "could not be delivered" in out["next_action"]
    async with sm() as s, s.begin(), public(tenant):
        with pytest.raises(Conflict, match="cannot send email"):
            await reg.resend_verification(s, ref, TO, public_base_url=BASE)
    # The same address is refused up front, without saying why.
    with pytest.raises(ValidationFailed, match="use one that receives mail"):
        await _apply(sm, tenant, FORM | {"name": "TEST Another Academy"})
    # A working address may apply again at once: the dead application no longer blocks.
    ref2 = await _apply(sm, tenant, FORM | {"proprietor_email": "ada@working.ng"})
    assert ref2 != ref


async def test_regulator_api_shows_delivery(client, sm, world):
    tenant, bodies = await _abia(sm, world)
    await _officer(sm, tenant, bodies["ASUBEB"])
    ref = await _apply(sm, tenant)
    await dispatch_once(sm, IdSender(), from_address="no-reply@example")
    async with sm() as s, s.begin():
        await fb.record(s, fb.parse(sns(ses_event("Delivery"))))
    login = await client.post(
        "/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": "officer@abia.example"}
    )
    h = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Scope-Node-Id": str(bodies["ASUBEB"]),
    }
    r = await client.get(f"/v1/private-school-applications/{ref}", headers=h)
    assert r.status_code == 200 and r.json()["email_delivery"] == "delivered"
