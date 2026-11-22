"""CAPTCHA and email verification on private school registration (ADR-015)."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from syntegra.core.clock import utcnow
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.institutions import registration as reg
from syntegra.institutions.models import RegistrationApplication
from syntegra.platform.captcha import DevCaptcha, SiteVerifyCaptcha
from syntegra.platform.email import SENT_BODY, DevEmailSender, dispatch_once
from syntegra.platform.models import OutboundEmail
from tests.integration.test_private_registration import (
    BASE,
    FORM,
    _apply,
    _officer,
    emailed_code,
    public,
    regulator,
)
from tests.integration.test_tertiary_register import _abia


async def _age(sm, ref, **fields):
    async with sm() as s, s.begin():
        await s.execute(
            update(RegistrationApplication.__table__)
            .where(RegistrationApplication.__table__.c.reference == ref)
            .values(**fields)
        )


# --------------------------------------------------------------------------- CAPTCHA


async def test_dev_captcha_accepts_only_its_token():
    assert await DevCaptcha().verify("dev-ok", None)
    assert not await DevCaptcha().verify(None, None) and not await DevCaptcha().verify("x", None)


async def test_siteverify_captcha_asks_the_provider_and_fails_closed():
    seen = {}

    def provider(request: httpx.Request) -> httpx.Response:
        seen["form"] = request.content.decode()
        return httpx.Response(200, json={"success": "good" in seen["form"]})

    ok = SiteVerifyCaptcha(
        "https://captcha.example/siteverify", "s3cret", transport=httpx.MockTransport(provider)
    )
    assert await ok.verify("good-token", "203.0.113.9")
    assert "secret=s3cret" in seen["form"] and "remoteip=203.0.113.9" in seen["form"]
    assert not await ok.verify("bad-token", None)
    assert not await ok.verify("", None)  # no token: no call
    down = SiteVerifyCaptcha(
        "https://captcha.example/siteverify",
        "s3cret",
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    assert not await down.verify(
        "good-token", None
    )  # provider unavailable: refuse, never wave through


async def test_api_refuses_applications_without_a_passed_captcha(client, sm, world):
    tenant, _ = await _abia(sm, world)
    for token in (None, "wrong"):
        r = await client.post(
            "/v1/public/NG-AB/private-school-applications",
            json=dict(FORM) | {"captcha_token": token},
        )
        assert r.status_code == 422 and "human check" in r.json()["detail"]
    async with sm() as s, public(tenant):
        assert (await s.execute(select(RegistrationApplication))).first() is None  # nothing stored
    form = (await client.get("/v1/public/NG-AB/private-school-applications/form")).json()
    assert form["captcha_provider"] == "dev" and form["postcode"]["example"] == "440001"
    assert form["required_address_fields"] == ["address_line1", "postcode", "town"]


# --------------------------------------------------------------------------- email


async def test_verification_email_is_queued_with_the_application_and_wiped_once_sent(sm, world):
    tenant, _ = await _abia(sm, world)
    ref = await _apply(sm, tenant)
    async with sm() as s:
        (mail,) = (await s.execute(select(OutboundEmail))).scalars().all()
    assert mail.to_address == "ada.okeke@example.ng" and ref in mail.subject
    assert f"{BASE}/private-schools/verify?reference={ref}&code=" in mail.body
    sender = DevEmailSender()
    assert await dispatch_once(sm, sender, from_address="no-reply@example") == 1
    assert sender.sent[0].to == "ada.okeke@example.ng" and "code=" in sender.sent[0].body
    async with sm() as s:
        sent = (await s.execute(select(OutboundEmail))).scalar_one()
    assert sent.sent_at is not None and sent.body == SENT_BODY  # the one-time code is not kept
    assert await dispatch_once(sm, sender, from_address="no-reply@example") == 0


async def test_review_waits_for_verification(sm, world):
    tenant, bodies = await _abia(sm, world)
    officer = await _officer(sm, tenant, bodies["ASUBEB"])
    ref = await _apply(sm, tenant)
    async with sm() as s, s.begin(), regulator(tenant, bodies["ASUBEB"], officer):
        assert await reg.list_applications(s, status=None) == []  # hidden from the queue
        assert [
            v.reference
            for v in await reg.list_applications(s, status=None, include_unverified=True)
        ] == [ref]
        with pytest.raises(ValidationFailed, match="not verified their email"):
            await reg.advance(s, ref, "under_review", None)
    code = await emailed_code(sm, ref)
    async with sm() as s, s.begin(), public(tenant):
        with pytest.raises(ValidationFailed, match="not valid or has expired"):
            await reg.verify_email(s, ref, "not-the-code-at-all")
        assert (await reg.verify_email(s, ref, code))["email_verified"] is True
        assert (await reg.verify_email(s, ref, code))[
            "email_verified"
        ] is True  # repeat clicks are fine
    async with sm() as s, s.begin(), regulator(tenant, bodies["ASUBEB"], officer):
        assert [v.reference for v in await reg.list_applications(s, status=None)] == [ref]
        assert (await reg.advance(s, ref, "under_review", None)).status == "under_review"


async def test_codes_expire(sm, world):
    tenant, _ = await _abia(sm, world)
    ref = await _apply(sm, tenant)
    code = await emailed_code(sm, ref)
    await _age(sm, ref, email_token_expires_at=utcnow() - timedelta(minutes=1))
    async with sm() as s, s.begin(), public(tenant):
        with pytest.raises(ValidationFailed, match="expired"):
            await reg.verify_email(s, ref, code)


async def test_resend_rules(sm, world):
    tenant, _ = await _abia(sm, world)
    ref = await _apply(sm, tenant)
    first = await emailed_code(sm, ref)
    async with sm() as s, s.begin(), public(tenant):
        with pytest.raises(NotFound):
            await reg.resend_verification(s, ref, "someone@else.ng", public_base_url=BASE)
        with pytest.raises(Conflict, match="moments ago"):
            await reg.resend_verification(s, ref, "ada.okeke@example.ng", public_base_url=BASE)
    await _age(sm, ref, last_verification_sent_at=utcnow() - timedelta(minutes=5))
    async with sm() as s, s.begin(), public(tenant):
        await reg.resend_verification(s, ref, "ada.okeke@example.ng", public_base_url=BASE)
    second = await emailed_code(sm, ref)
    assert second != first
    async with sm() as s, s.begin(), public(tenant):
        with pytest.raises(ValidationFailed):  # the old code stopped working
            await reg.verify_email(s, ref, first)
        await reg.verify_email(s, ref, second)
        with pytest.raises(Conflict, match="already verified"):
            await reg.resend_verification(s, ref, "ada.okeke@example.ng", public_base_url=BASE)
    ref2 = await _apply(sm, tenant, FORM | {"name": "TEST Another Academy"})
    await _age(
        sm,
        ref2,
        verification_sends=reg.MAX_SENDS,
        last_verification_sent_at=utcnow() - timedelta(minutes=5),
    )
    async with sm() as s, s.begin(), public(tenant):
        with pytest.raises(Conflict, match="too many"):
            await reg.resend_verification(s, ref2, "ada.okeke@example.ng", public_base_url=BASE)


async def test_an_abandoned_unverified_application_stops_blocking_after_a_week(sm, world):
    tenant, _ = await _abia(sm, world)
    ref = await _apply(sm, tenant)
    with pytest.raises(Conflict):
        await _apply(sm, tenant)
    await _age(sm, ref, created_at=utcnow() - timedelta(days=8))
    assert await _apply(sm, tenant) != ref


async def test_resend_through_the_api_needs_the_captcha(client, sm, world):
    tenant, _ = await _abia(sm, world)
    ref = await _apply(sm, tenant)
    url = f"/v1/public/NG-AB/private-school-applications/{ref}/resend-verification"
    r = await client.post(url, json={"email": "ada.okeke@example.ng"})
    assert r.status_code == 422 and "human check" in r.json()["detail"]
    await _age(sm, ref, last_verification_sent_at=utcnow() - timedelta(minutes=5))
    r = await client.post(url, json={"email": "ada.okeke@example.ng", "captcha_token": "dev-ok"})
    assert r.status_code == 202
