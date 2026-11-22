"""SQLAlchemy base, MySQL-faithful types and the CEDA standard columns (§6.1, §11)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import BigInteger, Date, DateTime, Integer, LargeBinary, MetaData, String
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Dialect
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import TypeDecorator

from syntegra.core.clock import utcnow
from syntegra.core.ids import uuid7

# The domain schemas of §11.1 (plus msg, ADR-030). On MySQL each is a database in the
# regional cluster.
SCHEMAS = (
    "gov", "iam", "mdm", "org", "acad", "att", "lms", "sg", "well", "fin", "rec", "msg", "ops",
)  # fmt: skip

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UUIDBinary(TypeDecorator[uuid.UUID]):
    """BINARY(16) holding a UUID v7 (§11.2: uuid → BINARY(16))."""

    impl = LargeBinary(16)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.BINARY(16))
        return dialect.type_descriptor(LargeBinary(16))

    def process_bind_param(self, value: uuid.UUID | str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = uuid.UUID(value)
        return value.bytes

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> uuid.UUID | None:
        return None if value is None else uuid.UUID(bytes=bytes(value))


class UTCDateTime(TypeDecorator[datetime]):
    """DATETIME(6) in UTC; session time zone is pinned to +00:00 on MySQL."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime())

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("store naive UTC datetimes only")
        return value


def Hash32() -> Any:
    return LargeBinary(32).with_variant(mysql.BINARY(32), "mysql")


def Text_() -> Any:
    return String(4000).with_variant(mysql.TEXT(), "mysql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: UUIDBinary(),
        datetime: UTCDateTime(),
        date: Date(),
        int: Integer(),
    }


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)


class Audited:
    """Marker: every insert/update/delete of this entity writes an audit_event (§9.1)."""

    __audit_entity__: ClassVar[str] = ""
    __audit_exclude__: ClassVar[frozenset[str]] = frozenset({"row_version", "updated_at"})

    def audit_tenant_id(self) -> uuid.UUID:
        return self.tenant_id  # type: ignore[attr-defined, no-any-return]

    def audit_scope_node_id(self) -> uuid.UUID | None:
        return getattr(self, "scope_node_id", None)


class Evidential:
    """Marker for P7: rows are immutable once committed; corrections are new rows."""


class Permanent:
    """Marker: rows may change but are never deleted (records kept for good, ADR-027)."""


class StandardColumns(IdMixin, Audited):
    """The standard columns on every tenant-scoped table (§6.1)."""

    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    scope_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utcnow, onupdate=utcnow)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        # Optimistic concurrency: row_version increments on every update (§6.1, If-Match).
        return {"version_id_col": cls.__table__.c.row_version}  # type: ignore[attr-defined]


class TenantScoped(StandardColumns):
    """Marker base: queries are filtered by tenant and active scope (§10.3)."""


class SoftDeletable:
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class EffectiveDated:
    valid_from: Mapped[date] = mapped_column(nullable=False)
    valid_to: Mapped[date | None] = mapped_column(nullable=True)


BigInt = BigInteger().with_variant(mysql.BIGINT(unsigned=False), "mysql")


def schema_translate_map(url: str) -> dict[str | None, str | None]:
    """SQLite has no schemas: map every CEDA schema to the default one for tests/dev."""
    if url.startswith("sqlite"):
        return dict.fromkeys(SCHEMAS)
    return {}


def make_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if url.startswith("mysql"):
        kwargs |= {
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            # UTC session time zone and strict mode on every connection (§11.2)
            "connect_args": {
                "init_command": (
                    "SET time_zone='+00:00', "
                    "sql_mode='STRICT_ALL_TABLES,NO_ZERO_DATE,ONLY_FULL_GROUP_BY'"
                )
            },
        }
    engine = create_async_engine(url, **kwargs)
    translate = schema_translate_map(url)
    if translate:
        engine = engine.execution_options(schema_translate_map=translate)
    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[Any]:
    from syntegra.platform.session import SyntegraSession

    return async_sessionmaker(engine, expire_on_commit=False, sync_session_class=SyntegraSession)
