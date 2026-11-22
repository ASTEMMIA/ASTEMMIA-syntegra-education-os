"""Single sign-on (OIDC) and MFA (ADR-016), end to end through the API with a fake identity
provider that enforces PKCE, client authentication and the redirect URI."""

from dataclasses import replace
from datetime import timedelta

import jwt
import pytest
from sqlalchemy import func, select, update

from syntegra.core.clock import utcnow
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import ValidationFailed
from syntegra.governance import api as governance
from syntegra.iam import mfa, sso
from syntegra.iam.models import AppUser, MfaCredential, OidcFlow, RecoveryCode
from syntegra.iam.oidc import OidcClient
from tests.audit_helpers import verify_all_days
from tests.fake_idp import FakeIdP

SCHOOL = "INS-NG-000101"  # Government College Umuahia, under Abia State
TEACHER = {"sub": "aad-0001", "email": "teacher@gcu.test", "name": "Teacher A"}


async def _configure(sm, world, **overrides):
    """Abia State sets up its Microsoft 365 once, at the state; schools inherit it."""
    idp = FakeIdP()
    value = [idp.provider_config(link_by_email=True, email_domains=["gcu.test"]) | overrides]
    async with sm() as s, s.begin():
        with use_context(RequestContext(world.abia_tenant, world.abia_root, None, system=True)):
            await governance.set_config(
                s, scope_node_id=world.abia_root, key=sso.PROVIDERS_KEY, value=value
            )
    return idp


@pytest.fixture
async def idp(app, sm, world):
    fake = await _configure(sm, world)
    app.state.sso = replace(
        app.state.sso,
        client=OidcClient(transport=fake.transport()),
        client_secrets={"ABIA_ENTRA": "idp-s3cret"},
    )
    return fake


async def _sign_in_at_idp(client, idp, tenant=SCHOOL, **claims):
    r = await client.get(f"/v1/auth/oidc/entra/start?tenant={tenant}")
    assert r.status_code == 302, r.text
    assert r.headers["cache-control"].startswith("no-store")
    state, code = idp.authorize(r.headers["location"], **claims)
    return await client.get("/v1/auth/oidc/callback", params={"state": state, "code": code})


def _amr(tokens):
    return jwt.decode(tokens["access_token"], options={"verify_signature": False})["amr"]


def _bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _enrol(client, challenge):
    e = (await client.post("/v1/auth/mfa/enrolment", json={"mfa_token": challenge})).json()
    assert e["otpauth_uri"].startswith("otpauth://totp/Syntegra%20Education:teacher@gcu.test?")
    r = await client.post(
        "/v1/auth/mfa/enrolment/confirm",
        json={"mfa_token": challenge, "code": mfa.totp(e["secret"], mfa.step())},
    )
    assert r.status_code == 200, r.text
    return e["secret"], r.json()


# --------------------------------------------------------------------------- providers


async def test_a_school_inherits_the_states_identity_provider(client, idp, world):
    r = await client.get(f"/v1/auth/providers?tenant={SCHOOL.lower()}")
    assert r.json()["providers"] == [{"key": "entra", "name": "Abia State Microsoft 365"}]
    assert "client_id" not in r.text and "secret" not in r.text  # nothing sensitive is shown
    assert (await client.get("/v1/auth/oidc/google/start?tenant=" + SCHOOL)).status_code == 404
    assert (await client.get("/v1/auth/providers?tenant=NO-SUCH")).status_code == 401


# --------------------------------------------------------------------------- first sign-in


async def test_first_sign_in_links_the_account_and_enrols_the_authenticator(client, idp, sm, world):
    r = await _sign_in_at_idp(client, idp, **TEACHER)
    assert r.status_code == 200, r.text
    challenge = r.json()
    assert challenge["mfa_required"] and challenge["enrolment_required"]
    assert "access_token" not in challenge and 290 <= challenge["expires_in"] <= 300
    # The challenge is not an access token.
    me = await client.get("/v1/me", headers={"Authorization": f"Bearer {challenge['mfa_token']}"})
    assert me.status_code == 401

    secret, done = await _enrol(client, challenge["mfa_token"])
    assert len(done["recovery_codes"]) == 10 and done["user_id"] == str(world.teacher_a)
    assert _amr(done) == ["fed", "otp", "mfa"]
    assert (await client.get("/v1/me", headers=_bearer(done))).status_code == 200

    async with sm() as s:
        user = await s.get(AppUser, world.teacher_a)
        assert user.idp_subject.endswith(":aad-0001")
        cred = (await s.execute(select(MfaCredential))).scalar_one()
        assert secret.encode() not in cred.secret_enc  # encrypted at rest
        assert (await s.execute(select(func.count()).select_from(RecoveryCode))).scalar() == 10
        assert await verify_all_days(s, world.school_a_tenant)

    # The challenge has been used: it cannot open a second session.
    again = await client.post(
        "/v1/auth/mfa/verify",
        json={"mfa_token": challenge["mfa_token"], "code": mfa.totp(secret, mfa.step() + 1)},
    )
    assert again.status_code == 401


async def test_wrong_enrolment_code_is_refused(client, idp):
    challenge = (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"]
    await client.post("/v1/auth/mfa/enrolment", json={"mfa_token": challenge})
    r = await client.post(
        "/v1/auth/mfa/enrolment/confirm", json={"mfa_token": challenge, "code": "000000"}
    )
    assert r.status_code == 401 and r.json()["detail"] == "that code is not right"


# --------------------------------------------------------------------------- later sign-ins


async def test_later_sign_ins_ask_for_the_code_and_codes_never_work_twice(client, idp, sm):
    secret, _ = await _enrol(
        client, (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"]
    )
    # Matched by the provider's subject now, even if the email changes at the provider.
    r = await _sign_in_at_idp(client, idp, sub="aad-0001", email="renamed@gcu.test")
    challenge = r.json()
    assert challenge["mfa_required"] and not challenge["enrolment_required"]
    used = mfa.totp(secret, mfa.step())  # spent at enrolment
    bad = await client.post(
        "/v1/auth/mfa/verify", json={"mfa_token": challenge["mfa_token"], "code": used}
    )
    assert bad.status_code == 401
    ok = await client.post(
        "/v1/auth/mfa/verify",
        json={"mfa_token": challenge["mfa_token"], "code": mfa.totp(secret, mfa.step() + 1)},
    )
    assert ok.status_code == 200 and _amr(ok.json()) == ["fed", "otp", "mfa"]


async def test_recovery_codes_work_once_and_can_be_replaced(client, idp):
    _, done = await _enrol(
        client, (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"]
    )
    code = done["recovery_codes"][0]
    for expected in (200, 401):
        challenge = (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"]
        r = await client.post(
            "/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": code.upper()}
        )
        assert r.status_code == expected
    assert _amr(done) == ["fed", "otp", "mfa"]
    fresh = await client.post("/v1/auth/mfa/recovery-codes", headers=_bearer(done))
    assert fresh.status_code == 200 and len(fresh.json()["recovery_codes"]) == 10
    challenge = (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"]
    old = done["recovery_codes"][1]
    r = await client.post("/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": old})
    assert r.status_code == 401  # replaced codes stop working


async def test_five_wrong_codes_lock_the_second_factor(client, idp, sm):
    secret, _ = await _enrol(
        client, (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"]
    )
    challenge = (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"]
    details = []
    for _ in range(5):
        r = await client.post(
            "/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": "123456"}
        )
        details.append(r.json()["detail"])
    assert details[-1] == "too many wrong codes"
    right = mfa.totp(secret, mfa.step() + 1)
    r = await client.post("/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": right})
    assert r.status_code == 401 and r.json()["detail"] == "too many wrong codes"
    async with sm() as s, s.begin():
        await s.execute(update(MfaCredential).values(locked_until=utcnow() - timedelta(seconds=1)))
    r = await client.post("/v1/auth/mfa/verify", json={"mfa_token": challenge, "code": right})
    assert r.status_code == 200


async def test_administrator_reset_for_a_lost_phone(client, idp, sm, world):
    await _enrol(client, (await _sign_in_at_idp(client, idp, **TEACHER)).json()["mfa_token"])
    async with sm() as s, s.begin():
        await sso.reset_mfa(s, user_id=world.teacher_a, reason="phone lost", actor_id=world.head_a)
    async with sm() as s:
        assert (await s.execute(select(func.count()).select_from(RecoveryCode))).scalar() == 0
        assert await verify_all_days(s, world.school_a_tenant)
    r = await _sign_in_at_idp(client, idp, **TEACHER)
    assert r.json()["enrolment_required"] is True


# --------------------------------------------------------------------------- who gets in


async def test_signing_in_never_creates_an_account(client, idp, sm):
    async with sm() as s:
        before = (await s.execute(select(func.count()).select_from(AppUser))).scalar()
    r = await _sign_in_at_idp(client, idp, sub="aad-9999", email="stranger@gcu.test")
    assert r.status_code == 401 and "not set up" in r.json()["next_action"]
    async with sm() as s:
        assert (await s.execute(select(func.count()).select_from(AppUser))).scalar() == before


async def test_email_linking_needs_an_authoritative_or_verified_email(client, app, sm, world):
    idp = await _configure(sm, world, email_domains=[])  # a provider like personal Google
    app.state.sso = replace(
        app.state.sso,
        client=OidcClient(transport=idp.transport()),
        client_secrets={"ABIA_ENTRA": "idp-s3cret"},
    )
    unverified = await _sign_in_at_idp(client, idp, **TEACHER)
    assert unverified.status_code == 401
    verified = await _sign_in_at_idp(client, idp, **TEACHER, email_verified=True)
    assert verified.status_code == 200 and verified.json()["mfa_required"]


async def test_an_account_in_another_organisation_is_not_reachable(client, idp):
    # head@academy.test exists only in the other school: signing in to GCU finds no account.
    r = await _sign_in_at_idp(client, idp, sub="aad-0002", email="head@academy.test")
    assert r.status_code == 401


async def test_trusted_provider_mfa_is_accepted_but_sms_is_not(client, app, sm, world):
    idp = await _configure(sm, world, trust_idp_mfa=True, email_domains=["gcu.test"])
    app.state.sso = replace(
        app.state.sso,
        client=OidcClient(transport=idp.transport()),
        client_secrets={"ABIA_ENTRA": "idp-s3cret"},
    )
    strong = await _sign_in_at_idp(client, idp, **TEACHER, amr=["pwd", "mfa"])
    assert strong.status_code == 200 and _amr(strong.json()) == ["fed", "mfa"]
    sms = await _sign_in_at_idp(client, idp, **TEACHER, amr=["pwd", "sms", "mfa"])
    assert sms.json()["mfa_required"] is True  # §8.3: SMS is not a strong factor
    pwd = await _sign_in_at_idp(client, idp, **TEACHER, amr=["pwd"])
    assert pwd.json()["mfa_required"] is True


async def test_users_exempt_from_mfa_get_tokens_straight_away(client, idp, sm, world):
    async with sm() as s, s.begin():
        await s.execute(
            update(AppUser).where(AppUser.id == world.teacher_a).values(mfa_required=False)
        )
    r = await _sign_in_at_idp(client, idp, **TEACHER)
    assert r.status_code == 200 and _amr(r.json()) == ["fed"]


# --------------------------------------------------------------------------- the exchange


async def test_the_callback_works_once_and_checks_everything(client, app, idp, sm):
    r = await client.get(f"/v1/auth/oidc/entra/start?tenant={SCHOOL}")
    state, code = idp.authorize(r.headers["location"], **TEACHER)
    ok = await client.get("/v1/auth/oidc/callback", params={"state": state, "code": code})
    assert ok.status_code == 200
    replay = await client.get("/v1/auth/oidc/callback", params={"state": state, "code": code})
    assert replay.status_code == 401
    # The provider reported an error (the person pressed Cancel).
    r = await client.get(
        "/v1/auth/oidc/callback", params={"state": "x" * 43, "error": "access_denied"}
    )
    assert r.status_code == 401
    # A state that was never issued.
    r = await client.get("/v1/auth/oidc/callback", params={"state": "y" * 43, "code": code})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "tamper",
    [
        {"nonce": "not-the-nonce"},  # replayed ID token
        {"iss": "https://evil.test/abia"},
        {"aud": "another-app"},
        {"exp": 1_000_000_000},  # long expired
    ],
)
async def test_id_tokens_are_checked_in_full(client, idp, tamper):
    idp.tamper = tamper
    r = await _sign_in_at_idp(client, idp, **TEACHER)
    assert r.status_code == 401 and r.json()["detail"] == "single sign-on failed"


async def test_expired_attempts_and_bad_client_secrets_fail(client, app, idp, sm):
    r = await client.get(f"/v1/auth/oidc/entra/start?tenant={SCHOOL}")
    state, code = idp.authorize(r.headers["location"], **TEACHER)
    async with sm() as s, s.begin():
        await s.execute(update(OidcFlow).values(expires_at=utcnow() - timedelta(seconds=1)))
    r = await client.get("/v1/auth/oidc/callback", params={"state": state, "code": code})
    assert r.status_code == 401

    app.state.sso = replace(app.state.sso, client_secrets={"ABIA_ENTRA": "wrong"})
    assert (await _sign_in_at_idp(client, idp, **TEACHER)).status_code == 401
    app.state.sso = replace(app.state.sso, client_secrets={})
    r = await _sign_in_at_idp(client, idp, **TEACHER)
    assert r.status_code == 401 and "administrator" in r.json()["next_action"]


async def test_pkce_binds_the_code_to_the_attempt_that_started_it(client, idp):
    first = await client.get(f"/v1/auth/oidc/entra/start?tenant={SCHOOL}")
    second = await client.get(f"/v1/auth/oidc/entra/start?tenant={SCHOOL}")
    state1, _ = idp.authorize(first.headers["location"], **TEACHER)
    _, code2 = idp.authorize(second.headers["location"], **TEACHER)
    # An attacker's code injected into the victim's attempt: the verifier does not match.
    r = await client.get("/v1/auth/oidc/callback", params={"state": state1, "code": code2})
    assert r.status_code == 401


async def test_operators_set_providers_through_the_checked_service(sm, world):
    async with sm() as s, s.begin():
        with pytest.raises(ValidationFailed, match="https"):
            await sso.set_identity_providers(
                s, "NG-AB-MOE", [{"key": "x", "name": "X", "issuer": "http://x", "client_id": "c"}]
            )
        found = await sso.set_identity_providers(
            s, "ng-ab-moe", [FakeIdP().provider_config(trust_idp_mfa=True)]
        )
        assert [p.key for p in found] == ["entra"] and found[0].trust_idp_mfa
    async with sm() as s:
        assert [p.key for p in await sso.identity_providers(s, SCHOOL)] == ["entra"]
