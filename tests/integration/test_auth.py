import pytest
from sqlalchemy import select, update

from syntegra.iam.models import AppUser, AuthSession
from syntegra.platform.settings import Settings
from syntegra.web.app import create_app
from tests.audit_helpers import verify_all_days

LOGIN = {"tenant_code": "INS-NG-000101", "login": "Teacher@GCU.test"}


async def _login(client, body=LOGIN):
    r = await client.post("/v1/auth/dev-login", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_dev_login_issues_short_lived_tokens(client, world):
    t = await _login(client)
    assert t["token_type"] == "Bearer" and 590 <= t["expires_in"] <= 600
    assert t["user_id"] == str(world.teacher_a) and t["refresh_token"].startswith("srt_")
    me = await client.get("/v1/me", headers=bearer(t))  # one grant: no scope header needed
    assert me.status_code == 200 and me.json()["active_scope_id"] == str(world.school_a_campus_1)


@pytest.mark.parametrize(
    "body",
    [
        {"tenant_code": "INS-NG-000101", "login": "nobody@gcu.test"},
        {"tenant_code": "NO-SUCH-ORG", "login": "teacher@gcu.test"},
        {"tenant_code": "INS-NG-000102", "login": "teacher@gcu.test"},  # right user, wrong org
    ],
)
async def test_failed_sign_in_reveals_nothing(client, world, body):
    r = await client.post("/v1/auth/dev-login", json=body)
    assert r.status_code == 401 and r.json()["detail"] == "sign-in failed"


async def test_requests_without_a_valid_token_are_rejected(client, world):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        r = await client.get("/v1/me", headers=headers)
        assert r.status_code == 401, headers


async def test_refresh_rotates_and_reuse_kills_the_session(client, world):
    t1 = await _login(client)
    r = await client.post("/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 200
    t2 = r.json()
    assert t2["refresh_token"] != t1["refresh_token"] and t2["session_id"] == t1["session_id"]
    assert (await client.get("/v1/me", headers=bearer(t2))).status_code == 200
    # Replaying the spent token looks like theft: the whole session is revoked.
    r = await client.post("/v1/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 401
    assert (await client.get("/v1/me", headers=bearer(t2))).status_code == 401
    r = await client.post("/v1/auth/refresh", json={"refresh_token": t2["refresh_token"]})
    assert r.status_code == 401


async def test_logout_takes_effect_immediately(client, world, sm):
    t = await _login(client)
    assert (await client.post("/v1/auth/logout", headers=bearer(t))).status_code == 204
    assert (await client.get("/v1/me", headers=bearer(t))).status_code == 401
    r = await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh_token"]})
    assert r.status_code == 401
    async with sm() as s:
        auth = (await s.execute(select(AuthSession))).scalars().one()
        assert auth.revoke_reason == "signed_out"


async def test_deactivated_user_is_cut_off_mid_session(client, world, sm):
    t = await _login(client)
    async with sm() as s, s.begin():
        await s.execute(
            update(AppUser.__table__)
            .where(AppUser.__table__.c.id == world.teacher_a)
            .values(status="suspended")
        )
    assert (await client.get("/v1/me", headers=bearer(t))).status_code == 401
    assert (await client.post("/v1/auth/dev-login", json=LOGIN)).status_code == 401


async def test_jwks_publishes_the_signing_key(client, world):
    import jwt as pyjwt

    t = await _login(client)
    keys = (await client.get("/.well-known/jwks.json")).json()["keys"]
    assert pyjwt.get_unverified_header(t["access_token"])["kid"] in {k["kid"] for k in keys}
    assert all("d" not in k for k in keys)  # never the private part


async def test_sign_in_is_audited_and_chain_stays_intact(client, world, sm):
    t = await _login(client)
    await client.post("/v1/auth/refresh", json={"refresh_token": t["refresh_token"]})
    async with sm() as s:
        count, breaks = await verify_all_days(s, world.school_a_tenant)
    assert breaks == [] and count > 0


def test_production_refuses_dev_login_and_missing_keys():
    with pytest.raises(RuntimeError, match="dev login"):
        create_app(Settings(env="prod", dev_auth=True))
    with pytest.raises(RuntimeError, match="SIGNING_KEY"):
        create_app(Settings(env="prod", dev_auth=False))


def test_dev_login_route_absent_when_disabled(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    import base64
    import os

    keys = {
        "field_kek_b64": base64.b64encode(os.urandom(32)).decode(),
        "blind_index_key_b64": base64.b64encode(os.urandom(32)).decode(),
    }
    with pytest.raises(RuntimeError, match="FIELD_KEK"):  # field encryption keys are mandatory too
        create_app(Settings(env="prod", dev_auth=False, token_signing_key_pem=pem))
    with pytest.raises(RuntimeError, match="CAPTCHA"):  # public forms need a real CAPTCHA (ADR-015)
        create_app(Settings(env="prod", dev_auth=False, token_signing_key_pem=pem, **keys))
    captcha = {
        "captcha_provider": "siteverify",
        "captcha_verify_url": "https://captcha.example/siteverify",
        "captcha_secret": "s3cret",
    }
    with pytest.raises(RuntimeError, match="email sender"):  # and a real email sender
        create_app(
            Settings(env="prod", dev_auth=False, token_signing_key_pem=pem, **keys, **captcha)
        )
    # The SES sender is an open item (ADR-015): stand in for it to look at the routes.
    from syntegra.platform.email import DevEmailSender

    monkeypatch.setattr("syntegra.web.app.build_sender", lambda settings: DevEmailSender())
    with pytest.raises(RuntimeError, match="local document store"):  # and S3 for documents
        create_app(
            Settings(env="prod", dev_auth=False, token_signing_key_pem=pem, **keys, **captcha)
        )
    with pytest.raises(RuntimeError, match="DOCUMENT_BUCKET"):  # with its bucket and KMS key
        create_app(
            Settings(
                env="prod",
                dev_auth=False,
                token_signing_key_pem=pem,
                document_store="s3",
                **keys,
                **captcha,
            )
        )
    s3 = {
        "document_store": "s3",
        "document_bucket": "syntegra-records-af-south-1",
        "document_kms_key_id": "alias/syntegra-records",
    }
    app = create_app(
        Settings(env="prod", dev_auth=False, token_signing_key_pem=pem, **keys, **captcha, **s3)
    )
    paths = set(app.openapi()["paths"])
    assert "/v1/auth/dev-login" not in paths and "/v1/auth/refresh" in paths
