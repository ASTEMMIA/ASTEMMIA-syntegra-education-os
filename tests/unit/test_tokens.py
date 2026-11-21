import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from syntegra.core.errors import Unauthenticated
from syntegra.iam.tokens import (
    KeySet,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
    verify_access_token,
)

ISS, AUD = "https://api.test", "syntegra-api"


def _issue(keys, **kw):
    args = dict(
        issuer=ISS,
        audience=AUD,
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        amr=["dev"],
        ttl_seconds=600,
    )
    args.update(kw)
    return issue_access_token(keys, **args)


def test_round_trip_and_header():
    keys = KeySet.generate()
    token, exp = _issue(keys)
    header = jwt.get_unverified_header(token)
    assert header == {"alg": "ES256", "kid": keys.signing_kid, "typ": "at+jwt"}
    claims = verify_access_token(keys, token, issuer=ISS, audience=AUD)
    assert claims.amr == ("dev",) and abs((claims.expires_at - exp).total_seconds()) < 1


@pytest.mark.parametrize(
    "mutate", ["audience", "issuer", "expired", "other_key", "garbage", "tamper"]
)
def test_invalid_tokens_are_rejected(mutate):
    keys = KeySet.generate()
    if mutate == "expired":
        token, _ = _issue(keys, now=datetime.now(UTC) - timedelta(hours=1))
    elif mutate == "other_key":
        token, _ = _issue(KeySet.generate())
    elif mutate == "garbage":
        token = "not-a-token"
    else:
        token, _ = _issue(keys)
        if mutate == "tamper":
            head, body, sig = token.split(".")
            token = f"{head}.{body[:-2]}AA.{sig}"
    iss = "https://evil" if mutate == "issuer" else ISS
    aud = "other-api" if mutate == "audience" else AUD
    with pytest.raises(Unauthenticated):
        verify_access_token(keys, token, issuer=iss, audience=aud)


def test_alg_none_and_hmac_confusion_are_rejected():
    keys = KeySet.generate()
    claims = {
        "iss": ISS,
        "aud": AUD,
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "jti": "x",
        "iat": 0,
        "nbf": 0,
        "exp": 2**31,
    }
    none_token = jwt.encode(
        claims, None, algorithm="none", headers={"kid": keys.signing_kid, "typ": "at+jwt"}
    )
    pub_pem = keys.signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    import base64
    import hashlib
    import hmac
    import json

    def b64(d):
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "kid": keys.signing_kid, "typ": "at+jwt"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(pub_pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    hmac_token = f"{head}.{body}.{sig}"
    for token in filter(None, [none_token, hmac_token]):
        with pytest.raises(Unauthenticated):
            verify_access_token(keys, token, issuer=ISS, audience=AUD)


def test_key_rotation_keeps_old_tokens_valid_until_they_expire():
    old = ec.generate_private_key(ec.SECP256R1())
    new = ec.generate_private_key(ec.SECP256R1())
    token, _ = _issue(KeySet.from_keys(old, []))
    rotated = KeySet.from_keys(new, [old.public_key()])
    verify_access_token(rotated, token, issuer=ISS, audience=AUD)
    assert len(rotated.jwks()["keys"]) == 2
    with pytest.raises(Unauthenticated):
        verify_access_token(KeySet.from_keys(new, []), token, issuer=ISS, audience=AUD)


def test_refresh_tokens_are_random_and_stored_hashed():
    a, ha = new_refresh_token()
    b, _ = new_refresh_token()
    assert a != b and a.startswith("srt_") and len(a) > 40
    assert hash_refresh_token(a) == ha and a.encode() not in ha
