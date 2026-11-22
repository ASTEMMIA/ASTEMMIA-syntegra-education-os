"""Syntegra-issued tokens (CEDA §8.3, §10.1). Framework-free.

* **Access tokens** are ES256 JWTs (RFC 9068 ``at+jwt``) that live 5-15 minutes. Claims:
  ``iss aud sub(user) tid(tenant) sid(session) jti iat nbf exp amr``.
* **Refresh tokens** are opaque random strings. Only their SHA-256 is stored. Every use rotates
  them, and reusing a spent one revokes the whole session (theft detection, OAuth 2.1 BCP).

Every sign-in method (dev login now, OIDC + MFA next) ends by calling the same issuance code,
and every request is verified by ``verify_access_token``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from syntegra.core.errors import Unauthenticated
from syntegra.core.ids import uuid7

ALGORITHM = "ES256"
TOKEN_TYPE = "at+jwt"  # noqa: S105 - RFC 9068 media type, not a secret
REFRESH_PREFIX = "srt_"
LEEWAY_SECONDS = 30


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def key_id(public_key: ec.EllipticCurvePublicKey) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return _b64(hashlib.sha256(der).digest())[:16]


@dataclass(frozen=True, slots=True)
class KeySet:
    signing_key: ec.EllipticCurvePrivateKey
    verify_keys: Mapping[str, ec.EllipticCurvePublicKey]

    @property
    def signing_kid(self) -> str:
        return key_id(self.signing_key.public_key())

    @classmethod
    def generate(cls) -> KeySet:
        """Ephemeral key for dev/test only: tokens die with the process."""
        return cls.from_keys(ec.generate_private_key(ec.SECP256R1()), [])

    @classmethod
    def from_pem(cls, private_pem: str, previous_public_pems: Iterable[str] = ()) -> KeySet:
        key = serialization.load_pem_private_key(private_pem.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
            raise ValueError("token signing key must be an EC P-256 private key (ES256)")
        previous = []
        for pem in previous_public_pems:
            pub = serialization.load_pem_public_key(pem.encode())
            if not isinstance(pub, ec.EllipticCurvePublicKey):
                raise ValueError("verification keys must be EC public keys")
            previous.append(pub)
        return cls.from_keys(key, previous)

    @classmethod
    def from_keys(
        cls, signing: ec.EllipticCurvePrivateKey, previous: Iterable[ec.EllipticCurvePublicKey]
    ) -> KeySet:
        keys = {key_id(k): k for k in [signing.public_key(), *previous]}
        return cls(signing, keys)

    def jwks(self) -> dict[str, Any]:
        """Public keys in JWK Set form, for gateways and other verifiers."""
        out = []
        for kid, pub in self.verify_keys.items():
            nums = pub.public_numbers()
            out.append(
                {
                    "kty": "EC",
                    "crv": "P-256",
                    "use": "sig",
                    "alg": ALGORITHM,
                    "kid": kid,
                    "x": _b64(nums.x.to_bytes(32, "big")),
                    "y": _b64(nums.y.to_bytes(32, "big")),
                }
            )
        return {"keys": out}


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    session_id: uuid.UUID
    token_id: uuid.UUID
    expires_at: datetime
    amr: tuple[str, ...]


def issue_access_token(
    keys: KeySet,
    *,
    issuer: str,
    audience: str,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID,
    amr: Iterable[str],
    ttl_seconds: int,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    exp = now + timedelta(seconds=ttl_seconds)
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": str(user_id),
        "tid": str(tenant_id),
        "sid": str(session_id),
        "jti": str(uuid7()),
        "amr": list(amr),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(
        claims,
        keys.signing_key,
        algorithm=ALGORITHM,
        headers={"kid": keys.signing_kid, "typ": TOKEN_TYPE},
    )
    return token, exp


def verify_access_token(keys: KeySet, token: str, *, issuer: str, audience: str) -> AccessClaims:
    """Verify signature, type, issuer, audience and lifetime. Raises Unauthenticated."""
    fail = Unauthenticated("your sign-in is not valid or has expired", next_action="Sign in again.")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise fail from None
    if header.get("alg") != ALGORITHM or header.get("typ") != TOKEN_TYPE:
        raise fail
    key = keys.verify_keys.get(str(header.get("kid")))
    if key is None:
        raise fail
    try:
        c = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"]},
        )
        return AccessClaims(
            user_id=uuid.UUID(c["sub"]),
            tenant_id=uuid.UUID(c["tid"]),
            session_id=uuid.UUID(c["sid"]),
            token_id=uuid.UUID(c["jti"]),
            expires_at=datetime.fromtimestamp(c["exp"], UTC),
            amr=tuple(c.get("amr", [])),
        )
    except jwt.PyJWTError, KeyError, ValueError, TypeError:
        raise fail from None


def new_refresh_token() -> tuple[str, bytes]:
    """Return (token to hand to the client, SHA-256 to store)."""
    raw = REFRESH_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode("utf-8")).digest()


# --------------------------------------------------------------------------- MFA challenge

MFA_TOKEN_TYPE = "mfa+jwt"  # noqa: S105 - a media type, not a secret
MFA_AUDIENCE_SUFFIX = "/mfa"


@dataclass(frozen=True, slots=True)
class MfaClaims:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    flow_id: uuid.UUID
    amr: tuple[str, ...]
    expires_at: datetime


def issue_mfa_token(
    keys: KeySet,
    *,
    issuer: str,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    flow_id: uuid.UUID,
    amr: Iterable[str],
    ttl_seconds: int = 300,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Proof that the first factor passed, good only for finishing MFA (ADR-016).

    Its own type and audience mean no API endpoint accepts it as an access token, and it is
    tied to one sign-in attempt (``fid``), which can be completed only once.
    """
    now = now or datetime.now(UTC)
    exp = now + timedelta(seconds=ttl_seconds)
    claims = {
        "iss": issuer,
        "aud": issuer + MFA_AUDIENCE_SUFFIX,
        "sub": str(user_id),
        "tid": str(tenant_id),
        "fid": str(flow_id),
        "jti": str(uuid7()),
        "amr": list(amr),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(
        claims,
        keys.signing_key,
        algorithm=ALGORITHM,
        headers={"kid": keys.signing_kid, "typ": MFA_TOKEN_TYPE},
    )
    return token, exp


def verify_mfa_token(keys: KeySet, token: str, *, issuer: str) -> MfaClaims:
    fail = Unauthenticated(
        "this sign-in attempt has expired", next_action="Start signing in again."
    )
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != ALGORITHM or header.get("typ") != MFA_TOKEN_TYPE:
            raise fail
        key = keys.verify_keys.get(str(header.get("kid")))
        if key is None:
            raise fail
        c = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=issuer + MFA_AUDIENCE_SUFFIX,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
        )
        return MfaClaims(
            user_id=uuid.UUID(c["sub"]),
            tenant_id=uuid.UUID(c["tid"]),
            flow_id=uuid.UUID(c["fid"]),
            amr=tuple(c.get("amr", [])),
            expires_at=datetime.fromtimestamp(c["exp"], UTC),
        )
    except jwt.PyJWTError, KeyError, ValueError, TypeError:
        raise fail from None
