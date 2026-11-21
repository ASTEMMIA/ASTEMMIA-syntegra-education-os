"""UTC clock. DATETIME(6) is stored in UTC by convention (CEDA §11.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime


def utcnow() -> datetime:
    """Naive UTC datetime, microsecond precision, as stored in DATETIME(6)."""
    return datetime.now(UTC).replace(tzinfo=None)


def today() -> date:
    return datetime.now(UTC).date()
