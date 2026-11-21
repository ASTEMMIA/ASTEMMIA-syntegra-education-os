"""Operational CLI: create schema (dev), bootstrap the global tenant, run the relay, verify audit.

uv run python -m syntegra.cli init-db
uv run python -m syntegra.cli bootstrap --admin admin@example.org
uv run python -m syntegra.cli relay --once
uv run python -m syntegra.cli verify-audit --day 2026-11-21
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

import syntegra.governance.models
import syntegra.iam.models
import syntegra.mdm.models  # noqa: F401
from syntegra.core.audit import ChainBreak
from syntegra.core.context import RequestContext, use_context
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel
from syntegra.iam import api as iam
from syntegra.platform.audit_store import chains_for_day, verify_day
from syntegra.platform.db import Base, make_engine, make_sessionmaker
from syntegra.platform.outbox import InMemoryPublisher, relay_once
from syntegra.platform.settings import get_settings


async def init_db() -> None:
    settings = get_settings()
    if settings.is_mysql:
        raise SystemExit("use 'alembic upgrade head' against MySQL")
    engine = make_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("schema created")


async def bootstrap(admin_login: str, code: str, name: str, region: str) -> dict[str, str]:
    """Create the global programme tenant, seed built-in roles and the first administrator."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        await iam.seed_builtin_roles(session)
        tenant = await governance.create_root_tenant(
            session,
            code=code,
            name=name,
            level=GovernanceLevel.GLOBAL,
            residency_region=region,
        )
        with use_context(
            RequestContext(
                tenant.id, tenant.root_scope_node_id, None, system=True, purpose="bootstrap"
            )
        ):
            user = await iam.create_user(
                session,
                tenant_id=tenant.id,
                login=admin_login,
                display_name="Platform administrator",
            )
            await iam.grant_role(
                session, user_id=user.id, role_code="PADM", scope_node_id=tenant.root_scope_node_id
            )
    await engine.dispose()
    return {
        "tenant_id": str(tenant.id),
        "root_scope_node_id": str(tenant.root_scope_node_id),
        "admin_user_id": str(user.id),
    }


async def relay(once: bool) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    publisher = InMemoryPublisher()  # MSK publisher is wired in integration/ (Phase 0 open item)
    while True:
        n = await relay_once(sm, publisher)
        print(f"published {n}")
        if once:
            break
        await asyncio.sleep(0.5 if n else 2)
    await engine.dispose()


async def verify_audit(day: date) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    failures: dict[str, list[ChainBreak]] = {}
    async with sm() as session:
        for tenant_id in await chains_for_day(session, day):
            count, breaks = await verify_day(session, tenant_id, day)
            print(f"{tenant_id} {day}: {count} events, {'INTACT' if not breaks else 'BROKEN'}")
            if breaks:
                failures[str(tenant_id)] = breaks
    await engine.dispose()
    return 1 if failures else 0


def main() -> None:
    p = argparse.ArgumentParser(prog="syntegra")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    b = sub.add_parser("bootstrap")
    b.add_argument("--admin", required=True)
    b.add_argument("--code", default="SYNTEGRA")
    b.add_argument("--name", default="Syntegra programme")
    b.add_argument("--region", default=get_settings().residency_region)
    r = sub.add_parser("relay")
    r.add_argument("--once", action="store_true")
    v = sub.add_parser("verify-audit")
    v.add_argument("--day", type=date.fromisoformat, default=date.today())
    args = p.parse_args()
    if args.cmd == "init-db":
        asyncio.run(init_db())
    elif args.cmd == "bootstrap":
        print(
            json.dumps(
                asyncio.run(bootstrap(args.admin, args.code, args.name, args.region)), indent=2
            )
        )
    elif args.cmd == "relay":
        asyncio.run(relay(args.once))
    elif args.cmd == "verify-audit":
        raise SystemExit(asyncio.run(verify_audit(args.day)))


if __name__ == "__main__":
    main()
