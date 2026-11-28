"""Private provider registration (ADR-014): apply → review → site inspection → approve or reject."""

import re
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.governance.models import Tenant
from syntegra.iam import api as iam
from syntegra.institutions import api as inst
from syntegra.institutions import registration as reg
from syntegra.institutions.models import Institution
from syntegra.platform.models import OutboundEmail
from tests.integration.test_tertiary_register import _abia

FORM = {
    "name": "TEST Bright Future Academy",
    "lga": "Aba North",
    "levels": "Nursery; Primary",
    "address_line1": "14 Faulks Road",
    "town": "Aba",
    "postcode": "450001",
    "proprietor_name": "Mrs Ada Okeke",
    "proprietor_email": "Ada.Okeke@example.ng",
    "proprietor_phone": "0803 555 0101",
    "cac_number": "RC 1234567",
}


@asynccontextmanager
async def public(tenant):
    with use_context(RequestContext(tenant.id, tenant.root_scope_node_id, None, system=True)):
        yield


@asynccontextmanager
async def regulator(tenant, scope, user):
    with use_context(RequestContext(tenant.id, scope, user)):
        yield


async def _officer(sm, tenant, asubeb):
    async with sm() as s, s.begin(), public(tenant):
        u = await iam.create_user(
            s, tenant_id=tenant.id, login="officer@abia.example", display_name="Officer"
        )
        await iam.grant_role(
            s, user_id=u.id, role_code="STA", scope_node_id=tenant.root_scope_node_id
        )
    return u.id


BASE = "https://apply.example"


async def _apply(sm, tenant, form=FORM):
    async with sm() as s, s.begin(), public(tenant):
        return await reg.submit_application(s, dict(form), public_base_url=BASE)


async def emailed_code(sm, reference):
    """The one-time code from the latest verification email for this application."""
    async with sm() as s:
        rows = (
            (
                await s.execute(
                    select(OutboundEmail)
                    .where(OutboundEmail.subject.contains(reference))
                    .order_by(OutboundEmail.id)
                )
            )
            .scalars()
            .all()
        )
    return re.search(r"code=([\w-]+)", rows[-1].body).group(1)


async def _verify(sm, tenant, reference):
    code = await emailed_code(sm, reference)
    async with sm() as s, s.begin(), public(tenant):
        await reg.verify_email(s, reference, code)


async def test_apply_and_track_without_an_account(sm, world):
    tenant, _ = await _abia(sm, world)
    ref = await _apply(sm, tenant)
    assert ref.startswith("PSR-") and ref.endswith("000001")
    async with sm() as s, public(tenant):
        out = await reg.application_status(s, ref, "ada.okeke@EXAMPLE.ng")
        assert out["status"] == "awaiting_email_verification"
        with pytest.raises(NotFound):  # someone without the proprietor's email learns nothing
            await reg.application_status(s, ref, "someone@else.ng")


async def test_application_is_checked_like_any_registration(sm, world):
    tenant, _ = await _abia(sm, world)
    with pytest.raises(ValidationFailed) as exc:
        await _apply(
            sm, tenant, FORM | {"postcode": "", "proprietor_email": "not-an-email", "lga": "Lagos"}
        )
    detail = exc.value.detail
    assert "postcode is required" in detail and "not one of this body's units" in detail
    assert "valid proprietor email" in detail
    with pytest.raises(ValidationFailed, match="not registered through this system"):
        await _apply(
            sm, tenant, FORM | {"type": "University", "levels": "Degree"}
        )  # NUC licenses universities
    await _apply(sm, tenant)
    with pytest.raises(Conflict, match="already has a registration application"):
        await _apply(
            sm, tenant, FORM | {"name": "test bright future academy", "proprietor_email": "x@y.ng"}
        )


async def test_review_inspect_approve_creates_the_school(sm, world):
    tenant, bodies = await _abia(sm, world)
    asubeb = bodies["ASUBEB"]
    officer = await _officer(sm, tenant, asubeb)
    ref = await _apply(sm, tenant)
    await _verify(sm, tenant, ref)
    async with sm() as s, s.begin(), regulator(tenant, asubeb, officer):
        with pytest.raises(ValidationFailed) as exc:  # no approval without review and inspection
            await reg.advance(s, ref, "approved", None)
        assert exc.value.next_action == "Allowed next steps: rejected, under_review."
        await reg.advance(s, ref, "under_review", "Documents complete")
        await reg.advance(s, ref, "site_inspection", "Visit booked for 3 Dec")
        view = await reg.advance(s, ref, "approved", "Premises meet the standard")
        steps = [h.get("status") or h["event"] for h in view.history]
        assert view.status == "approved"
        assert steps == [
            "submitted",
            "email_verified",
            "under_review",
            "site_inspection",
            "approved",
        ]

        school = (
            await s.execute(select(Institution).where(Institution.id == view.institution_id))
        ).scalar_one()
        assert school.ownership == "private" and school.proprietor_name == "Mrs Ada Okeke"
        assert school.postcode == "450001" and school.location == "Aba North"
        own = (
            await s.execute(select(Tenant).where(Tenant.id == school.own_tenant_id))
        ).scalar_one()
        assert own.kind == "institutional" and own.ownership == "private"
        assert own.code == school.institution_code
        node = await governance.get_scope_node(s, school.scope_node_id)
        lgea = await governance.scope_node_by_code(s, tenant.id, "LGEA-ABA-NORTH")
        assert node.parent_id == lgea.id  # regulatory link under its LGEA, inside ASUBEB
        listed = await inst.list_institutions(s)
        assert [v.name for v in listed] == ["TEST Bright Future Academy"]
        proprietor = await iam.user_by_login(s, own.id, "ada.okeke@example.ng")
        grants = await iam.grants_for(s, proprietor.id)
        assert [g.role_code for g in grants] == ["IADM"]
        assert grants[0].scope_node_id == own.root_scope_node_id
    async with sm() as s, public(tenant):
        out = await reg.application_status(s, ref, "ada.okeke@example.ng")
        assert out["status"] == "approved" and "Sign in" in out["next_action"]


async def test_rejection_needs_a_reason_the_proprietor_sees(sm, world):
    tenant, bodies = await _abia(sm, world)
    officer = await _officer(sm, tenant, bodies["ASUBEB"])
    ref = await _apply(sm, tenant)  # left unverified: the regulator may still reject it
    async with sm() as s, s.begin(), regulator(tenant, bodies["ASUBEB"], officer):
        with pytest.raises(ValidationFailed, match="reason"):
            await reg.advance(s, ref, "rejected", "  ")
        await reg.advance(s, ref, "rejected", "Building is not approved for school use")
    async with sm() as s, public(tenant):
        out = await reg.application_status(s, ref, "ada.okeke@example.ng")
        assert (
            out["status"] == "rejected"
            and out["reason"] == "Building is not approved for school use"
        )
    assert await _apply(sm, tenant)  # after a rejection the provider may apply again


async def test_public_and_regulator_api(client, sm, world):
    tenant, bodies = await _abia(sm, world)
    await _officer(sm, tenant, bodies["ASUBEB"])
    body = dict(FORM) | {"captcha_token": "dev-ok"}  # the dev/test CAPTCHA stand-in
    r = await client.post("/v1/public/ng-ab/private-school-applications", json=body)  # no sign-in
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    ref = r.json()["reference"]
    st = await client.get(
        f"/v1/public/NG-AB/private-school-applications/{ref}?email=ada.okeke@example.ng"
    )
    assert st.status_code == 200 and st.json()["status"] == "awaiting_email_verification"
    assert (
        await client.post("/v1/public/XX-NOPE/private-school-applications", json=body)
    ).status_code == 404
    v = await client.post(
        f"/v1/public/NG-AB/private-school-applications/{ref}/verify-email",
        json={"code": await emailed_code(sm, ref)},
    )
    assert v.status_code == 200 and v.json()["email_verified"] is True

    login = await client.post(
        "/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": "officer@abia.example"}
    )
    h = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Scope-Node-Id": str(bodies["ASUBEB"]),
    }
    q = (await client.get("/v1/private-school-applications?status=submitted", headers=h)).json()[
        "items"
    ]
    assert [a["reference"] for a in q] == [ref] and q[0]["email_verified"] is True
    assert q[0]["school"]["full_address"].endswith("450001, Nigeria")
    for step in ("under_review", "site_inspection", "approved"):
        t = await client.post(
            f"/v1/private-school-applications/{ref}/transition", json={"to": step}, headers=h
        )
        assert t.status_code == 200, t.text
    code = (await client.get("/v1/institutions", headers=h)).json()["items"][0]["institution_code"]

    # The proprietor signs in to their own school with the email they applied with.
    own = await client.post(
        "/v1/auth/dev-login", json={"tenant_code": code, "login": "ada.okeke@example.ng"}
    )
    assert own.status_code == 200
    me = await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {own.json()['access_token']}"}
    )
    assert [g["role"] for g in me.json()["grants"]] == ["IADM"]
