"""Canonical JSON for hashing (RFC 8785-style: sorted keys, no whitespace, UTF-8)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def _default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"not canonicalisable: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, default=_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
