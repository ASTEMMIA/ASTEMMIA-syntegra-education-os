"""Identifiers (CEDA §6.1, §7.1).

* Internal keys are UUID v7: time-ordered so InnoDB inserts stay sequential.
* Human-facing identifiers carry an ISO 7064 MOD 97-10 check (two digits).
* National identifiers are attributes, never keys (see external_identifier).
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """Return a RFC 9562 UUID version 7.

    48-bit Unix millisecond timestamp, then a 12-bit counter (monotonic within a
    millisecond in this process), then 62 random bits.
    """
    global _last_ms, _counter
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            _counter += 1
            if _counter > 0xFFF:  # counter exhausted: borrow the next millisecond
                _last_ms += 1
                _counter = 0
            ms = _last_ms
        else:
            _last_ms = ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF  # leave headroom
        counter = _counter
    rand = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= counter << 64
    value |= 0b10 << 62
    value |= rand
    return uuid.UUID(int=value)


def uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Extract the Unix millisecond timestamp from a UUID v7."""
    if value.version != 7:
        raise ValueError("not a UUID v7")
    return value.int >> 80


# --------------------------------------------------------------------------- ISO 7064


def _to_digits(payload: str) -> str:
    """Map an alphanumeric payload to digits (0-9 stay, A=10 … Z=35), as in IBAN."""
    out: list[str] = []
    for ch in payload.upper():
        if ch.isdigit():
            out.append(ch)
        elif "A" <= ch <= "Z":
            out.append(str(ord(ch) - 55))
        else:
            raise ValueError(f"invalid character {ch!r} in check payload")
    return "".join(out)


def mod97_check(payload: str) -> str:
    """ISO 7064 MOD 97-10 check digits for ``payload`` (separators removed)."""
    digits = _to_digits(re.sub(r"[-\s]", "", payload))
    return f"{98 - (int(digits + '00') % 97):02d}"


def mod97_valid(payload: str, check: str) -> bool:
    digits = _to_digits(re.sub(r"[-\s]", "", payload)) + check
    return int(digits) % 97 == 1


# --------------------------------------------------------------------------- human IDs

_LRN = re.compile(r"^LRN-(\d{4})-(\d{4})-(\d{2})$")


def global_learner_id(sequence: int) -> str:
    """``LRN-NNNN-NNNN-CC``: assigned at creation, never reused, survives merge as alias."""
    if not 0 < sequence < 10**8:
        raise ValueError("learner sequence out of range")
    body = f"{sequence:08d}"
    a, b = body[:4], body[4:]
    return f"LRN-{a}-{b}-{mod97_check('LRN' + body)}"


def is_valid_global_learner_id(value: str) -> bool:
    m = _LRN.match(value)
    return bool(m) and mod97_valid("LRN" + m.group(1) + m.group(2), m.group(3))  # type: ignore[union-attr]


def staff_number(sequence: int) -> str:
    """``STF-NNNN-NNNN-CC`` (per employing tenant)."""
    if not 0 < sequence < 10**8:
        raise ValueError("staff sequence out of range")
    body = f"{sequence:08d}"
    return f"STF-{body[:4]}-{body[4:]}-{mod97_check('STF' + body)}"


def institution_id(country: str, sequence: int) -> str:
    """``INS-<ISO country>-NNNNNN``."""
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country must be ISO 3166-1 alpha-2")
    if not 0 < sequence < 10**6:
        raise ValueError("institution sequence out of range")
    return f"INS-{country}-{sequence:06d}"


def certificate_number(country: str, authority: str, year: int, sequence: int) -> str:
    """``<country>-<authority>-<year>-<seq><check>``: authority-wide sequence, never reused."""
    seq = f"{sequence:04d}"
    check = mod97_check(f"{country}{authority}{year}{seq}")
    return f"{country}-{authority}-{year}-{seq}{check}"


def safeguarding_case_reference(year: int, sequence: int) -> str:
    """``SG-<year>-<seq>``: carries no learner data."""
    return f"SG-{year}-{sequence:04d}"


def document_number(tenant_code: str, year: int, sequence: int) -> str:
    """``DOC-<tenant code>-<year>-<seq>``."""
    return f"DOC-{tenant_code.upper()}-{year}-{sequence:06d}"


def mask(value: str, visible: int = 4) -> str:
    """Masked display for external identifiers: ``•••••••4417``."""
    if len(value) <= visible:
        return "•" * len(value)
    return "•" * (len(value) - visible) + value[-visible:]
