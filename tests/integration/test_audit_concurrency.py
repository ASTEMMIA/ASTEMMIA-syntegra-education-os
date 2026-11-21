"""Concurrent writers in one tenant must produce one unbroken chain (§9.1)."""

import asyncio

import pytest

from syntegra.core.clock import today
from syntegra.core.context import use_context
from syntegra.governance import api as governance
from syntegra.platform.audit_store import verify_day
from tests.conftest import MYSQL_URL

pytestmark = pytest.mark.skipif(not MYSQL_URL, reason="row locking needs MySQL")


async def test_concurrent_writers_serialise_on_the_chain_head(sm, world):
    async def write(i: int) -> None:
        for attempt in range(5):
            try:
                async with sm() as s, s.begin():
                    with use_context(
                        world.ctx(world.school_b_tenant, world.school_b_root, world.head_b)
                    ):
                        await governance.set_config(
                            s, scope_node_id=world.school_b_root, key=f"k{i}", value=i
                        )
                return
            except Exception as exc:  # deadlock → retry, as the app's retry policy does
                if "Deadlock" not in str(exc) or attempt == 4:
                    raise

    await asyncio.gather(*(write(i) for i in range(20)))
    async with sm() as s:
        count, breaks = await verify_day(s, world.school_b_tenant, today())
    assert breaks == []
    assert count >= 20


async def test_concurrent_first_writers_of_a_new_chain_day(sm, world):
    from sqlalchemy import delete

    from syntegra.platform.models import AuditChainHead, AuditEvent

    async with sm() as s, s.begin():  # start this tenant's day from nothing
        await s.execute(
            delete(AuditEvent.__table__).where(
                AuditEvent.__table__.c.tenant_id == world.school_b_tenant
            )
        )
        await s.execute(
            delete(AuditChainHead.__table__).where(
                AuditChainHead.__table__.c.tenant_id == world.school_b_tenant
            )
        )
    await test_concurrent_writers_serialise_on_the_chain_head(sm, world)
