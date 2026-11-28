"""Never-reused counters for human-facing identifiers (CEDA §7.1).

Same locking pattern as the audit chain head (ADR-003): one statement creates the row if
absent AND takes an exclusive lock, so concurrent issuers serialise without deadlocking.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import Table, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.platform.models import IdSequence


def _upsert(dialect: str, tenant_id: uuid.UUID, name: str) -> Any:
    table = cast(Table, IdSequence.__table__)
    values = {"tenant_id": tenant_id, "name": name, "last_value": 0}
    if dialect == "mysql":
        return (
            mysql_insert(table)
            .values(values)
            .on_duplicate_key_update(last_value=table.c.last_value)
        )
    return sqlite_insert(table).values(values).on_conflict_do_nothing()


async def next_value(session: AsyncSession, tenant_id: uuid.UUID, name: str) -> int:
    await session.execute(_upsert(session.get_bind().dialect.name, tenant_id, name))
    row = (
        await session.execute(
            select(IdSequence)
            .where(IdSequence.tenant_id == tenant_id, IdSequence.name == name)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    row.last_value += 1
    await session.flush()
    return row.last_value
