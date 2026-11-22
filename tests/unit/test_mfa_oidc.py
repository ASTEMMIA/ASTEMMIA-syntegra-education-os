import base64
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from syntegra.core.errors import Unauthenticated, ValidationFailed
from syntegra.iam import mfa, oidc
from syntegra.iam.tokens import (
    KeySet,
    issue_access_token,
    issue_mfa_token,
    verify_access_token,
    verify_mfa_token,
)

# RFC 6238 Appendix B: the SHA-1 seed is the ASCII string "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    ("unix", "expected"),
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_totp_matches_the_rfc_6238_vectors(unix, expected):
    assert mfa.totp(RFC_SECRET, unix // 30) == expected


def test_totp_allows_one_step_of_drift_and_never_the_same_step_twice():
    now = datetime(2026, 11, 22, 9, 0, 15, tzinfo=UTC)
    s = mfa.step(now)
    for drift in (-1, 0, 1):
        assert mfa.verify_totp(RFC_SECRET, mfa.totp(RFC_SECRET, s + drift), last_step=None, now=now)
    assert mfa.verify_totp(RFC_SECRET, mfa.totp(RFC_SECRET, s + 2), last_step=None, now=now) is None
    assert mfa.verify_totp(RFC_SECRET, mfa.totp(RFC_SECRET, s), last_step=s, now=now) is None
    assert mfa.verify_totp(RFC_SECRET, "12345", last_step=None, now=now) is None
    assert mfa.verify_totp(RFC_SECRET, "abcdef", last_step=None, now=now) is None


def test_new_secrets_are_160_bit_base32():
    s = mfa.new_totp_secret()
    assert len(base64.b32decode(s + "=" * (-len(s) % 8))) == 20 and s != mfa.new_totp_secret()


def test_otpauth_uri_is_what_authenticator_apps_read():
    uri = mfa.otpauth_uri("JBSWY3DPEHPK3PXP", account="head@gcu.test", issuer="Syntegra Education")
    parts = urlsplit(uri)
    assert parts.scheme == "otpauth" and parts.netloc == "totp"
    assert parts.path == "/Syntegra%20Education:head@gcu.test"
    q = parse_qs(parts.query)
    assert q["secret"] == ["JBSWY3DPEHPK3PXP"] and q["issuer"] == ["Syntegra Education"]


def test_recovery_codes_are_unique_readable_and_forgiving_to_type():
    codes = mfa.new_recovery_codes()
    assert len(set(codes)) == 10 and all(len(c) == 14 and c.count("-") == 2 for c in codes)
    assert not set("01ilo") & set("".join(codes))
    c = codes[0]
    assert mfa.hash_recovery_code(c) == mfa.hash_recovery_code(f" {c.upper().replace('-', ' ')} ")
    assert mfa.hash_recovery_code(c) != mfa.hash_recovery_code(codes[1])


def test_provider_settings_are_validated():
    ok = oidc.parse_providers(
        [
            {
                "key": "Entra",
                "name": "M365",
                "issuer": "https://login.example/x",
                "client_id": "c",
                "scopes": ["email"],
                "email_domains": ["@AbiaState.gov.ng"],
            }
        ]
    )[0]
    assert ok.key == "entra" and ok.scopes == ("openid", "email")
    assert ok.email_domains == ("abiastate.gov.ng",)
    assert ok.discovery == "https://login.example/x/.well-known/openid-configuration"
    assert not ok.trust_idp_mfa and not ok.link_by_email  # both off unless chosen
    assert oidc.parse_providers(None) == []
    for bad in (
        {"key": "a", "name": "A", "issuer": "http://plain.example", "client_id": "c"},
        {"key": "a", "name": "A", "issuer": "https://x.example"},
    ):
        with pytest.raises(ValidationFailed):
            oidc.parse_providers([bad])
    dup = {"key": "a", "name": "A", "issuer": "https://x.example", "client_id": "c"}
    with pytest.raises(ValidationFailed, match="unique"):
        oidc.parse_providers([dup, dup])


@pytest.mark.parametrize(
    ("amr", "strong"),
    [
        (("pwd", "mfa"), True),
        (("hwk",), True),
        (("pwd",), False),
        (("pwd", "sms", "mfa"), False),
        ((), False),
    ],
)
def test_only_strong_provider_mfa_counts(amr, strong):
    claims = oidc.IdTokenClaims("i", "s", None, False, None, amr, {})
    assert claims.provider_mfa is strong


def test_subjects_are_namespaced_by_issuer():
    a = oidc.subject_key("https://login.a.test", "123")
    b = oidc.subject_key("https://login.b.test", "123")
    assert a != b and a.endswith(":123") and len(a) == 20
    with pytest.raises(Unauthenticated):
        oidc.subject_key("https://login.a.test", "")


def test_pkce_challenge_is_s256_of_the_verifier():
    import hashlib

    verifier, challenge = oidc.new_pkce()
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == digest.decode() and 43 <= len(verifier) <= 128


def test_mfa_tokens_and_access_tokens_are_not_interchangeable():
    keys, iss = KeySet.generate(), "https://api.test"
    ids = dict(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    mfa_token, _ = issue_mfa_token(keys, issuer=iss, flow_id=uuid.uuid4(), amr=["fed"], **ids)
    assert verify_mfa_token(keys, mfa_token, issuer=iss).user_id == ids["user_id"]
    with pytest.raises(Unauthenticated):
        verify_access_token(keys, mfa_token, issuer=iss, audience="syntegra-api")
    access, _ = issue_access_token(
        keys,
        issuer=iss,
        audience=iss + "/mfa",
        session_id=uuid.uuid4(),
        amr=["dev"],
        ttl_seconds=600,
        **ids,
    )
    with pytest.raises(Unauthenticated):  # right audience, wrong type
        verify_mfa_token(keys, access, issuer=iss)
