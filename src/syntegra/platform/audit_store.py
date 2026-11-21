"""Audit chain verification (§9.1: nightly verifier recomputes chains; any break is critical)."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.audit import ChainBreak, verify_chain
from syntegra.platform.models import AuditChainHead, AuditEvent


async def verify_day(
    session: AsyncSession, tenant_id: uuid.UUID, day: date
) -> tuple[int, list[ChainBreak]]:
    rows = (
        (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.tenant_id == tenant_id, AuditEvent.chain_date == day)
                .order_by(AuditEvent.seq)
            )
        )
        .scalars()
        .all()
    )
    breaks = verify_chain(tenant_id, day, [r.hash_view() for r in rows])
    head = await session.get(AuditChainHead, (tenant_id, day))
    if (
        head is not None
        and rows
        and (head.last_seq != rows[-1].seq or head.last_hash != rows[-1].event_hash)
    ):
        breaks.append(
            ChainBreak(rows[-1].seq, "chain head does not match the last event (truncation)")
        )
    return len(rows), breaks


async def chains_for_day(session: AsyncSession, day: date) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(AuditChainHead.tenant_id).where(AuditChainHead.chain_date == day)
            )
        ).scalars()
    )
