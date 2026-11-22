"""Alembic environment. Migrations follow expand/contract for zero downtime (CEDA §10.1).

The URL comes from SYNTEGRA_DATABASE_URL (mysql+asyncmy://…, Aurora writer endpoint).
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

import syntegra.academic.models
import syntegra.governance.models
import syntegra.hr.models
import syntegra.iam.models
import syntegra.institutions.models
import syntegra.learners.models
import syntegra.mdm.models
import syntegra.records.models  # noqa: F401
from syntegra.platform.db import SCHEMAS, Base, make_engine
from syntegra.platform.settings import get_settings

target_metadata = Base.metadata
VERSION_SCHEMA = "ops"


def _include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name in SCHEMAS
    return True


def _configure(connection: Connection | None = None, **kw: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=_include_name,
        version_table_schema=VERSION_SCHEMA,
        compare_type=True,
        **kw,
    )


def run_offline() -> None:
    _configure(
        url=get_settings().database_url, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    for schema in SCHEMAS:
        connection.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{schema}`")
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    settings = get_settings()
    if not settings.is_mysql:
        raise SystemExit(
            "migrations target MySQL; use 'python -m syntegra.cli init-db' for SQLite dev"
        )
    engine = make_engine(settings.database_url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
