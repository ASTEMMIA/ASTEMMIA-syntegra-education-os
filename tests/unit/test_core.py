import uuid
from datetime import date
from decimal import Decimal

import pytest

from syntegra.core import ids
from syntegra.core.audit import event_hash, genesis_hash, verify_chain
from syntegra.core.events import EventEnvelope
from syntegra.core.money import CurrencyMismatch, Money


def test_uuid7_is_version_7_and_time_ordered():
    values = [ids.uuid7() for _ in range(2000)]
    assert all(v.version == 7 and v.variant == uuid.RFC_4122 for v in values)
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_mod97_round_trip_and_detects_single_digit_errors():
    lrn = ids.global_learner_id(41185)
    assert lrn.startswith("LRN-0004-1185-")
    assert ids.is_valid_global_learner_id(lrn)
    corrupted = lrn.replace("1185", "1186")
    assert not ids.is_valid_global_learner_id(corrupted)


def test_certificate_number_format():
    n = ids.certificate_number("NG", "AB", 2026, 44)
    assert n.startswith("NG-AB-2026-0044") and len(n) == len("NG-AB-2026-004417")
    assert ids.mod97_valid("NGAB20260044", n[-2:])


def test_other_identifier_formats():
    assert ids.institution_id("NG", 4120) == "INS-NG-004120"
    assert ids.safeguarding_case_reference(2026, 81) == "SG-2026-0081"
    assert ids.document_number("absmoe", 2026, 118204) == "DOC-ABSMOE-2026-118204"
    assert ids.mask("12345674417") == "•••••••4417"


def test_money_minor_units_and_allocation():
    m = Money.of("100.00", "NGN")
    assert m.minor == 10000
    parts = Money(100, "NGN").allocate([1, 1, 1])
    assert [p.minor for p in parts] == [34, 33, 33] and sum(p.minor for p in parts) == 100
    assert str(Money.of(Decimal("12.5"), "GBP")) == "12.50 GBP"
    with pytest.raises(CurrencyMismatch):
        _ = Money(1, "NGN") + Money(1, "GBP")


def test_event_envelope_rules():
    t, s = ids.uuid7(), ids.uuid7()
    ev = EventEnvelope("LRN.CREATED", t, s, "learner", ids.uuid7(), None, {"learner_id": "x"})
    assert ev.topic == "syntegra.lrn"
    with pytest.raises(ValueError, match=r"DOMAIN\.PAST_TENSE_VERB"):
        EventEnvelope("LearnerCreated", t, s, "learner", ids.uuid7(), None)
    with pytest.raises(ValueError, match="personal data"):
        EventEnvelope("LRN.CREATED", t, s, "learner", ids.uuid7(), None, {"given_name": "Ada"})


def _chain(tenant, day, n):
    prev = genesis_hash(tenant, day)
    out = []
    for i in range(1, n + 1):
        ev = {
            "tenant_id": tenant,
            "chain_date": day,
            "seq": i,
            "action": "update",
            "entity_type": "x",
            "entity_id": None,
            "prev_hash": prev,
        }
        ev["event_hash"] = event_hash(prev, ev)
        prev = ev["event_hash"]
        out.append(ev)
    return out


def test_audit_chain_verifies_and_detects_tampering_and_gaps():
    t, d = ids.uuid7(), date(2026, 11, 21)
    events = _chain(t, d, 5)
    assert verify_chain(t, d, events) == []
    events[2]["action"] = "delete"  # tamper with content
    assert any("tampered" in b.reason for b in verify_chain(t, d, events))
    events = _chain(t, d, 5)
    del events[1]  # remove an event
    reasons = [b.reason for b in verify_chain(t, d, events)]
    assert any("gap" in r for r in reasons) and any("prev_hash" in r for r in reasons)
