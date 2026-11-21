from datetime import date

import pytest
from sqlalchemy import select, update

from syntegra.core.clock import today
from syntegra.core.context import use_context
from syntegra.core.errors import ValidationFailed
from syntegra.governance import api as governance
from syntegra.platform.audit_store import verify_day
from syntegra.platform.models import AuditEvent, OutboxEvent
from syntegra.platform.outbox import InMemoryPublisher, relay_once


async def test_every_write_is_audited_and_the_chain_verifies(sm, world):
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root, world.head_a)):
            cfg_id = await governance.set_config(
                s, scope_node_id=world.school_a_root, key="k", value=1
            )
    async with sm() as s:
        ev = (
            await s.execute(select(AuditEvent).where(AuditEvent.entity_id == cfg_id))
        ).scalar_one()
        assert ev.action == "create" and ev.actor_id == world.head_a and ev.after_hash
        count, breaks = await verify_day(s, world.school_a_tenant, today())
        assert count >= 4 and breaks == []


async def test_tampering_in_the_database_is_detected(sm, world):
    async with sm() as s, s.begin():
        # Simulate a DBA editing a row directly, bypassing the ORM guard.
        tbl = AuditEvent.__table__
        await s.execute(
            update(tbl)
            .where(tbl.c.tenant_id == world.school_a_tenant, tbl.c.seq == 2)
            .values(action="delete")
        )
    async with sm() as s:
        _, breaks = await verify_day(s, world.school_a_tenant, today())
    assert any("tampered" in b.reason for b in breaks)


async def test_outbox_relay_publishes_in_order_once(sm, world):
    pub = InMemoryPublisher()
    total = 0
    while n := await relay_once(sm, pub, batch=5):
        total += n
    async with sm() as s:
        assert (
            await s.execute(select(OutboxEvent).where(OutboxEvent.published_at.is_(None)))
        ).first() is None
    assert total == len(pub.sent) > 0
    types = [e.event_type for _, _, e in pub.sent]
    assert types[0] == "GOV.TENANT_CREATED"
    assert await relay_once(sm, pub) == 0


async def test_failed_publish_is_retried_without_overtaking(sm, world):
    class Flaky(InMemoryPublisher):
        fail_first = True

        async def send(self, topic, key, value):
            if self.fail_first:
                self.fail_first = False
                raise ConnectionError("broker down")
            await super().send(topic, key, value)

    pub = Flaky()
    await relay_once(sm, pub, batch=500)
    async with sm() as s:
        failed = (
            (await s.execute(select(OutboxEvent).where(OutboxEvent.published_at.is_(None))))
            .scalars()
            .all()
        )
    assert failed and all(r.attempts == 1 and "broker down" in r.last_error for r in failed)
    await relay_once(sm, pub, batch=500)
    async with sm() as s:
        assert (
            await s.execute(select(OutboxEvent).where(OutboxEvent.published_at.is_(None)))
        ).first() is None


RULE = dict(
    rule_key="ATT-INST-02",
    domain="attendance",
    title="Persistent absence",
    expression="learner.attendance_rate_6w < min_rate and learner.age >= 6",
    strictness={"min_rate": "min"},
    test_cases=[
        {
            "name": "absent",
            "facts": {"learner.attendance_rate_6w": 0.5, "learner.age": 8},
            "expect": True,
        },
        {
            "name": "fine",
            "facts": {"learner.attendance_rate_6w": 0.97, "learner.age": 8},
            "expect": False,
        },
    ],
    valid_from=date(2026, 9, 1),
)


async def test_policy_publish_tightening_and_conflicts(sm, world):
    async with sm() as s, s.begin():
        with use_context(world.ctx(world.abia_tenant, world.abia_root, world.state_officer)):
            rid = await governance.create_policy_rule(
                s, owner_scope_id=world.abia_root, parameters={"min_rate": 0.9}, **RULE
            )
            await governance.publish_policy_rule(s, rid)
        with use_context(world.ctx(world.school_a_tenant, world.school_a_root, world.head_a)):
            tighter = await governance.create_policy_rule(
                s, owner_scope_id=world.school_a_root, parameters={"min_rate": 0.95}, **RULE
            )
            await governance.publish_policy_rule(s, tighter)
            looser = await governance.create_policy_rule(
                s, owner_scope_id=world.school_b_root, parameters={"min_rate": 0.8}, **RULE
            )
            with pytest.raises(ValidationFailed, match="relaxes"):
                await governance.publish_policy_rule(s, looser)
