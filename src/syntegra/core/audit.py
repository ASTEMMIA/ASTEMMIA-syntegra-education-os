"""Audit hash chain (CEDA §9.1, principle P8).

``event_hash = SHA-256(prev_hash ‖ canonical JSON of the event)``.
There is one chain per tenant per day. The first event of a day chains from a
deterministic genesis hash (see ADR-003). Pure functions only; persistence lives
in ``syntegra.platform.audit_store``.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from syntegra.core.canonical import canonical_json

HASH_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "chain_date",
    "seq",
    "occurred_at",
    "actor_id",
    "on_behalf_of",
    "action",
    "entity_type",
    "entity_id",
    "scope_node_id",
    "purpose",
    "before_hash",
    "after_hash",
    "ip",
    "device_id",
)


def genesis_hash(tenant_id: uuid.UUID, chain_date: date) -> bytes:
    return hashlib.sha256(
        f"CEDA-AUDIT-GENESIS|{tenant_id}|{chain_date.isoformat()}".encode()
    ).digest()


def state_hash(state: Mapping[str, Any] | None) -> bytes | None:
    """Content hash of an entity snapshot (before/after)."""
    if state is None:
        return None
    return hashlib.sha256(canonical_json(dict(state))).digest()


def event_hash(prev_hash: bytes, event: Mapping[str, Any]) -> bytes:
    body = {k: event.get(k) for k in HASH_FIELDS}
    return hashlib.sha256(prev_hash + canonical_json(body)).digest()


@dataclass(frozen=True, slots=True)
class ChainBreak:
    seq: int
    reason: str


def verify_chain(
    tenant_id: uuid.UUID, chain_date: date, events: Iterable[Mapping[str, Any]]
) -> list[ChainBreak]:
    """Recompute a tenant-day chain. Events must be ordered by ``seq``.

    Returns every break found (empty list means the chain is intact).
    """
    breaks: list[ChainBreak] = []
    prev = genesis_hash(tenant_id, chain_date)
    expected_seq = 1
    for ev in events:
        seq = int(ev["seq"])
        if seq != expected_seq:
            breaks.append(ChainBreak(seq, f"sequence gap: expected {expected_seq}"))
        if ev["prev_hash"] != prev:
            breaks.append(ChainBreak(seq, "prev_hash does not match previous event_hash"))
        recomputed = event_hash(ev["prev_hash"], ev)
        if recomputed != ev["event_hash"]:
            breaks.append(ChainBreak(seq, "event_hash does not match content (tampered)"))
        prev = ev["event_hash"]
        expected_seq = seq + 1
    return breaks
