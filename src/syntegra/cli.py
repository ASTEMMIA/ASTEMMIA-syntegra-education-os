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
import uuid
from datetime import date

import syntegra.governance.models
import syntegra.iam.models
import syntegra.institutions.models
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


# The 17 local government areas of Abia State; each has a Local Government Education Authority.
ABIA_LGAS = (
    "Aba North",
    "Aba South",
    "Arochukwu",
    "Bende",
    "Ikwuano",
    "Isiala Ngwa North",
    "Isiala Ngwa South",
    "Isuikwuato",
    "Obi Ngwa",
    "Ohafia",
    "Osisioma",
    "Ugwunagbo",
    "Ukwa East",
    "Ukwa West",
    "Umuahia North",
    "Umuahia South",
    "Umu Nneochi",
)
STATE_CODE = "NG-AB"
NIGERIA_CODE = "NG"
ASUBEB_CODE = "ASUBEB"
TERTIARY_CODE = "TERTIARY-BOARD"
# Working name until the official name and abbreviation are confirmed (ADR-010).
TERTIARY_NAME = "Abia State Tertiary Education Board"
BASIC_LEVELS = ["eccde", "primary", "jss", "sss"]


def lgea_code(lga: str) -> str:
    return "LGEA-" + "-".join(lga.upper().split())


async def seed_abia(platform_code: str, admin_login: str | None) -> dict[str, object]:
    """Nigeria -> Abia State (tenant) -> ASUBEB (+17 LGEAs) and the Tertiary Education Board
    (ADR-010). Safe to run again: nothing is duplicated."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    created: list[str] = []
    async with sm() as session, session.begin():
        platform = await governance.tenant_by_code(session, platform_code)
        if platform is None:
            raise SystemExit(f"platform tenant {platform_code} not found: run 'bootstrap' first")
        nigeria = await governance.tenant_by_code(session, NIGERIA_CODE)
        if nigeria is None:
            nigeria = await governance.create_child_tenant(
                session,
                parent_scope_id=platform.root_scope_node_id,
                code=NIGERIA_CODE,
                name="Nigeria",
                level=GovernanceLevel.COUNTRY,
                country="NG",
                residency_region=platform.residency_region,
                time_zone="Africa/Lagos",
                local_label="Federal Ministry of Education",
            )
            created.append("Nigeria")
        state = await governance.tenant_by_code(session, STATE_CODE)
        if state is None:
            state = await governance.create_child_tenant(
                session,
                parent_scope_id=nigeria.root_scope_node_id,
                code=STATE_CODE,
                name="Abia State",
                level=GovernanceLevel.REGION,
                country="NG",
                residency_region=nigeria.residency_region,
                time_zone="Africa/Lagos",
                local_label="State",
            )
            created.append("Abia State")
        with use_context(
            RequestContext(
                state.id, state.root_scope_node_id, None, system=True, purpose="seed-abia"
            )
        ):

            async def node(
                parent: uuid.UUID, level: GovernanceLevel, code: str, name: str, label: str
            ) -> uuid.UUID:
                found = await governance.scope_node_by_code(session, state.id, code)
                if found is not None:
                    return found.id
                made = await governance.create_scope_node(
                    session, parent_id=parent, level=level, code=code, name=name, local_label=label
                )
                created.append(code)
                return made.id

            # Boards rank below the state; LGEAs below a board; institutions below those.
            asubeb = await node(
                state.root_scope_node_id,
                GovernanceLevel.LOCAL_AUTHORITY,
                ASUBEB_CODE,
                "Abia State Universal Basic Education Board",
                "Board",
            )
            tertiary = await node(
                state.root_scope_node_id,
                GovernanceLevel.LOCAL_AUTHORITY,
                TERTIARY_CODE,
                TERTIARY_NAME,
                "Board",
            )
            for lga in ABIA_LGAS:
                await node(asubeb, GovernanceLevel.NETWORK, lgea_code(lga), f"{lga} LGEA", "LGEA")
            if await governance.resolve_config(session, asubeb, "register.levels") is None:
                await governance.set_config(
                    session, scope_node_id=asubeb, key="register.levels", value=BASIC_LEVELS
                )
                created.append("ASUBEB register levels")
            if admin_login and await iam.user_by_login(session, state.id, admin_login) is None:
                user = await iam.create_user(
                    session,
                    tenant_id=state.id,
                    login=admin_login,
                    display_name="Abia State administrator",
                )
                await iam.grant_role(
                    session,
                    user_id=user.id,
                    role_code="STA",
                    scope_node_id=state.root_scope_node_id,
                )
                created.append(f"user {admin_login} (STA)")
    await engine.dispose()
    return {
        "state_tenant_id": str(state.id),
        "asubeb_scope_id": str(asubeb),
        "tertiary_board_scope_id": str(tertiary),
        "created": created,
    }


async def import_schools(text: str, source_name: str, board_code: str, commit: bool) -> int:
    """Preview (and with --commit, register) a CSV list of schools under a board (ADR-010)."""
    from syntegra.institutions import api as institutions

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        state = await governance.tenant_by_code(session, STATE_CODE)
        board = (
            await governance.scope_node_by_code(session, state.id, board_code) if state else None
        )
        if state is None or board is None:
            raise SystemExit(f"board {board_code} not found: run seed-abia first")
        with use_context(
            RequestContext(state.id, board.id, None, system=True, purpose="school-import")
        ):
            job_id = await institutions.preview_import(session, text, source_name=source_name)
            job, rows = await institutions.get_job(session, job_id)
            print(json.dumps(job.summary, indent=2))
            for r in [r for r in rows if r.status == "error"][:50]:
                print(f"row {r.row_no}: " + "; ".join(r.messages))
            if commit:
                summary = await institutions.commit_import(
                    session, job_id, managing_body=board.name[:64]
                )
                print(f"registered {summary['created']} schools, skipped {summary['skipped']}")
            else:
                print("preview only: nothing registered.")
                print("Run again with --commit to register the valid rows.")
    await engine.dispose()
    return 0


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
    a = sub.add_parser(
        "seed-abia",
        aliases=["seed-asubeb"],
        help="create Abia State, ASUBEB (+17 LGEAs) and the Tertiary Education Board",
    )
    a.add_argument("--platform-code", default="SYNTEGRA")
    a.add_argument("--admin", help="login for an ASUBEB administrator (state officer role)")
    imp = sub.add_parser("import-schools", help="preview or register a CSV list of schools")
    imp.add_argument("csv_path")
    imp.add_argument("--board-code", default=ASUBEB_CODE)
    imp.add_argument(
        "--commit", action="store_true", help="register the valid rows (default: preview only)"
    )
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
    elif args.cmd in ("seed-abia", "seed-asubeb"):
        print(json.dumps(asyncio.run(seed_abia(args.platform_code, args.admin)), indent=2))
    elif args.cmd == "import-schools":
        from pathlib import Path

        csv_file = Path(args.csv_path)
        text = csv_file.read_text(encoding="utf-8-sig")
        raise SystemExit(
            asyncio.run(import_schools(text, csv_file.name, args.board_code, args.commit))
        )
    elif args.cmd == "relay":
        asyncio.run(relay(args.once))
    elif args.cmd == "verify-audit":
        raise SystemExit(asyncio.run(verify_audit(args.day)))


if __name__ == "__main__":
    main()
