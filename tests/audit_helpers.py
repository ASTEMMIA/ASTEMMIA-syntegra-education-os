"""Audit-chain checks for tests that do not depend on the time of day.

There is one chain per tenant per day (ADR-003). A test that writes events and then verifies
"today" fails if it runs across midnight UTC: its events are in yesterday's chain. Verify
every day the tenant has a chain for instead.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.audit import ChainBreak
from syntegra.platform.audit_store import verify_day
from syntegra.platform.models import AuditChainHead


async def verify_all_days(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[int, list[ChainBreak]]:
    days = (
        (
            await session.execute(
                select(AuditChainHead.chain_date)
                .where(AuditChainHead.tenant_id == tenant_id)
                .order_by(AuditChainHead.chain_date)
            )
        )
        .scalars()
        .all()
    )
    total, breaks = 0, []
    for day in days:
        count, day_breaks = await verify_day(session, tenant_id, day)
        total += count
        breaks += day_breaks
    return total, breaks
