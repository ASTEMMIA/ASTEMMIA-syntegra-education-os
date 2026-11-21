"""Domain event envelope (CEDA §20).

Events are named ``<DOMAIN>.<PAST_TENSE_VERB>``. Payloads carry IDs and minimal
facts, never personal data. Delivery is at-least-once; consumers dedupe on event_id.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from syntegra.core.ids import uuid7

EVENT_NAME = re.compile(r"^[A-Z]{2,6}\.[A-Z][A-Z_]*$")

# Keys that would indicate personal data has leaked into a payload.
_PERSONAL_KEYS = frozenset(
    {
        "name",
        "given_name",
        "family_name",
        "date_of_birth",
        "dob",
        "email",
        "phone",
        "address",
        "nin",
        "national_id",
        "body",
        "note",
        "notes",
    }
)


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_type: str
    tenant_id: uuid.UUID
    scope_node_id: uuid.UUID
    aggregate_type: str
    aggregate_id: uuid.UUID
    actor_id: uuid.UUID | None
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    correlation_id: uuid.UUID | None = None
    event_id: uuid.UUID = field(default_factory=uuid7)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not EVENT_NAME.match(self.event_type):
            raise ValueError(f"event type {self.event_type!r} must be DOMAIN.PAST_TENSE_VERB")
        leaked = _PERSONAL_KEYS.intersection(k.lower() for k in self.payload)
        if leaked:
            raise ValueError(f"event payload must not carry personal data: {sorted(leaked)}")
        if self.schema_version < 1:
            raise ValueError("schema_version starts at 1")

    @property
    def partition_key(self) -> str:
        """Ordering is guaranteed per aggregate (Kafka partition key)."""
        return str(self.aggregate_id)

    @property
    def topic(self) -> str:
        return f"syntegra.{self.event_type.split('.', 1)[0].lower()}"
