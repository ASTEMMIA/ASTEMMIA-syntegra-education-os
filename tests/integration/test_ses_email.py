"""Amazon SES delivery (ADR-017). botocore's Stubber checks every request against the real SES v2
API model, so a wrong parameter name or shape fails here, not in production."""

from datetime import timedelta

import boto3
import pytest
from botocore.stub import Stubber
from sqlalchemy import select, update

from syntegra.core.clock import utcnow
from syntegra.platform.email import (
    GAVE_UP,
    MAX_ATTEMPTS,
    SENT_BODY,
    SesEmailSender,
    backoff,
    build_sender,
    dispatch_once,
    queue_email,
)
from syntegra.platform.models import OutboundEmail
from syntegra.platform.settings import Settings

FROM = "no-reply@abia.syntegra.education"


@pytest.fixture
def ses():
    client = boto3.client(
        "sesv2", region_name="af-south-1", aws_access_key_id="x", aws_secret_access_key="x"
    )
    with Stubber(client) as stub:
        yield client, stub
        stub.assert_no_pending_responses()


async def _queue(sm, world, n=1):
    async with sm() as s, s.begin():
        for i in range(n):
            queue_email(
                s,
                tenant_id=world.abia_tenant,
                purpose="private-school-verification",
                to=f"proprietor{i}@example.ng",
                subject=f"Confirm your email PSR-{i}",
                body=f"Your code: secret-{i}",
            )


async def _rows(sm):
    async with sm() as s:
        return (await s.execute(select(OutboundEmail).order_by(OutboundEmail.id))).scalars().all()


def _expected(i=0, **extra):
    return {
        "FromEmailAddress": FROM,
        "Destination": {"ToAddresses": [f"proprietor{i}@example.ng"]},
        "Content": {
            "Simple": {
                "Subject": {"Data": f"Confirm your email PSR-{i}", "Charset": "UTF-8"},
                "Body": {"Text": {"Data": f"Your code: secret-{i}", "Charset": "UTF-8"}},
            }
        },
        "EmailTags": [{"Name": "purpose", "Value": "private-school-verification"}],
    } | extra


async def test_ses_delivers_and_keeps_the_message_id_not_the_body(sm, world, ses):
    client, stub = ses
    stub.add_response(
        "send_email", {"MessageId": "0100018c-abc"}, _expected(ConfigurationSetName="syntegra-tx")
    )
    await _queue(sm, world)
    sender = SesEmailSender(client, configuration_set="syntegra-tx")
    assert await dispatch_once(sm, sender, from_address=FROM) == 1
    (row,) = await _rows(sm)
    assert row.sent_at is not None and row.body == SENT_BODY
    assert row.provider_message_id == "0100018c-abc" and row.attempts == 1


async def test_a_rejected_message_is_not_retried_and_its_code_is_wiped(sm, world, ses):
    client, stub = ses
    stub.add_client_error("send_email", "MessageRejected", "Email address is not verified.", 400)
    await _queue(sm, world)
    sender = SesEmailSender(client)
    assert await dispatch_once(sm, sender, from_address=FROM) == 0
    (row,) = await _rows(sm)
    assert (
        row.attempts == MAX_ATTEMPTS and row.body == GAVE_UP and "MessageRejected" in row.last_error
    )
    assert await dispatch_once(sm, sender, from_address=FROM) == 0  # no second call to SES


async def test_throttling_stops_the_batch_without_spending_attempts(sm, world, ses):
    client, stub = ses
    stub.add_client_error("send_email", "TooManyRequestsException", "Slow down", 429)
    await _queue(sm, world, n=2)
    sender = SesEmailSender(client)
    assert await dispatch_once(sm, sender, from_address=FROM) == 0  # second message not tried
    assert [r.attempts for r in await _rows(sm)] == [0, 0]
    stub.add_response("send_email", {"MessageId": "m0"}, _expected(0))
    stub.add_response("send_email", {"MessageId": "m1"}, _expected(1))
    assert await dispatch_once(sm, sender, from_address=FROM) == 2


async def test_temporary_failures_back_off(sm, world, ses):
    client, stub = ses
    stub.add_client_error("send_email", "InternalFailure", "try later", 500)
    await _queue(sm, world)
    sender = SesEmailSender(client)
    assert await dispatch_once(sm, sender, from_address=FROM) == 0
    (row,) = await _rows(sm)
    assert row.attempts == 1 and row.body == "Your code: secret-0"
    wait = row.next_attempt_at - utcnow()
    assert timedelta(seconds=50) < wait <= timedelta(minutes=1)
    assert await dispatch_once(sm, sender, from_address=FROM) == 0  # not due yet: SES not called
    async with sm() as s, s.begin():
        await s.execute(update(OutboundEmail).values(next_attempt_at=utcnow()))
    stub.add_response("send_email", {"MessageId": "m0"}, _expected(0))
    assert await dispatch_once(sm, sender, from_address=FROM) == 1
    (row,) = await _rows(sm)
    assert row.attempts == 2 and row.last_error is None


def test_backoff_doubles_up_to_a_day():
    assert [backoff(n).total_seconds() / 60 for n in (1, 2, 3, 4)] == [1, 2, 4, 8]
    assert backoff(30) == timedelta(days=1)


def test_tags_are_made_safe_for_ses():
    sender = SesEmailSender(object())  # type: ignore[arg-type]
    from syntegra.platform.email import Message

    req = sender._request(Message("a@b.ng", "s", "b", FROM, "réinscription / école"))
    assert req["EmailTags"] == [{"Name": "purpose", "Value": "r_inscription____cole"}]
    assert "ConfigurationSetName" not in req


def test_sender_selection_and_production_guard():
    ses = build_sender(Settings(env="test", email_sender="ses"))
    assert isinstance(ses, SesEmailSender)
    assert ses._client.meta.region_name == "af-south-1"  # the residency region by default
    other = build_sender(Settings(env="test", email_sender="ses", ses_region="eu-west-2"))
    assert other._client.meta.region_name == "eu-west-2"
    with pytest.raises(RuntimeError, match="real email sender"):
        build_sender(Settings(env="prod", email_sender="dev"))
    assert isinstance(build_sender(Settings(env="prod", email_sender="ses")), SesEmailSender)
