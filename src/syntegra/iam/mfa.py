"""Second factors (CEDA §8.3, ADR-016). Framework-free.

* **TOTP** (RFC 6238): HMAC-SHA1, 30-second steps, 6 digits, one step of clock drift either
  way. This is what Google Authenticator, Microsoft Authenticator and Authy implement. The last
  accepted step is stored, so a code that has been used once can never be used again.
* **Recovery codes**: ten single-use codes shown once at enrolment. Only their SHA-256 is stored.

SMS is deliberately absent: §8.3 does not accept it as a strong factor (SIM-swap).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
SECRET_BYTES = 20  # 160 bits, as RFC 4226 recommends
RECOVERY_CODES = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def new_totp_secret() -> str:
    """A fresh shared secret, base32 without padding (the form authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def step(now: datetime | None = None) -> int:
    return int((now or datetime.now(UTC)).timestamp()) // STEP_SECONDS


def totp(secret: str, counter: int) -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def verify_totp(
    secret: str, code: str, *, last_step: int | None, now: datetime | None = None
) -> int | None:
    """Return the matched time step, or None. Steps at or before ``last_step`` never match."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = step(now)
    for counter in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if last_step is not None and counter <= last_step:
            continue
        if hmac.compare_digest(totp(secret, counter), code):
            return counter
    return None


def otpauth_uri(secret: str, *, account: str, issuer: str) -> str:
    """The ``otpauth://`` link that authenticator apps read from a QR code."""
    label = quote(f"{issuer}:{account}", safe="@:")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def looks_like_totp(code: str) -> bool:
    c = code.strip().replace(" ", "")
    return len(c) == DIGITS and c.isdigit()


def new_recovery_codes(n: int = RECOVERY_CODES) -> list[str]:
    """Codes like ``k7pq-3mzt-w9xa``: 60 bits each, easy to read aloud and type."""
    return [
        "-".join("".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4)) for _ in range(3))
        for _ in range(n)
    ]


def hash_recovery_code(code: str) -> bytes:
    normalized = code.strip().lower().replace(" ", "").replace("-", "")
    return hashlib.sha256(b"syntegra-recovery|" + normalized.encode()).digest()
