"""Operational CLI: create schema (dev), bootstrap the global tenant, run the relay, verify audit.

uv run python -m syntegra.cli init-db
uv run python -m syntegra.cli bootstrap --admin admin@example.org
uv run python -m syntegra.cli relay --once
uv run python -m syntegra.cli verify-audit --day 2026-11-21
uv run python -m syntegra.cli configure-sso NG-AB providers.json
uv run python -m syntegra.cli reset-mfa --tenant-code INS-NG-000101 --login head@gcu.test \
    --reason 'phone lost'
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

import syntegra.governance.models
import syntegra.hr.models
import syntegra.iam.models
import syntegra.institutions.models
import syntegra.mdm.models
import syntegra.records.models  # noqa: F401
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
        from syntegra.mdm.gazetteer import seed_address_types

        await seed_address_types(session)
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


async def seed_structure(
    structure_ref: str, platform_code: str, admin_login: str | None
) -> dict[str, object]:
    """Build a jurisdiction from a structure file (P4: every state or country differs; the
    differences are data). Safe to run again: nothing is duplicated."""
    from syntegra.governance.structure import load_structure

    structure = load_structure(structure_ref)
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        platform = await governance.tenant_by_code(session, platform_code)
        if platform is None:
            raise SystemExit(f"platform tenant {platform_code} not found: run 'bootstrap' first")
        tenant, bodies, created = await governance.apply_structure(
            session,
            structure,
            platform_scope_id=platform.root_scope_node_id,
            residency_region=platform.residency_region,
        )
        if admin_login and await iam.user_by_login(session, tenant.id, admin_login) is None:
            with use_context(
                RequestContext(
                    tenant.id,
                    tenant.root_scope_node_id,
                    None,
                    system=True,
                    purpose="apply-structure",
                )
            ):
                user = await iam.create_user(
                    session,
                    tenant_id=tenant.id,
                    login=admin_login,
                    display_name=f"{tenant.name} administrator",
                )
                await iam.grant_role(
                    session,
                    user_id=user.id,
                    role_code="STA",
                    scope_node_id=tenant.root_scope_node_id,
                )
            created.append(f"user {admin_login} (STA)")
        if structure.education_bodies:
            from syntegra.institutions.bodies import seed_catalogue
            from syntegra.institutions.body_rules import load_catalogue

            for name in structure.education_bodies:
                seeded = await seed_catalogue(session, load_catalogue(name))
                created += [f"education body {c}" for c in seeded["created"]]
    await engine.dispose()
    return {
        "tenant": tenant.code,
        "tenant_id": str(tenant.id),
        "bodies": {code: str(sid) for code, sid in bodies.items()},
        "created": created,
    }


async def import_schools(
    text: str, source_name: str, tenant_code: str, board_code: str, commit: bool
) -> int:
    """Preview (and with --commit, register) a CSV list of institutions for one body (ADR-012)."""
    from syntegra.institutions import api as institutions

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        state = await governance.tenant_by_code(session, tenant_code)
        board = (
            await governance.scope_node_by_code(session, state.id, board_code) if state else None
        )
        if state is None or board is None:
            raise SystemExit(f"{board_code} not found in {tenant_code}: run seed-structure first")
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
                print(f"registered {summary['created']} institutions, skipped {summary['skipped']}")
            else:
                print("preview only: nothing registered.")
                print("Run again with --commit to register the valid rows.")
    await engine.dispose()
    return 0


async def send_email(once: bool) -> None:
    """Deliver queued email (ADR-015). Dev: printed here and to the log."""
    from syntegra.platform.email import build_sender, dispatch_once

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    sender = build_sender(settings)
    while True:
        n = await dispatch_once(sm, sender, from_address=settings.email_from)
        print(f"sent {n}")
        for m in getattr(sender, "sent", [])[-n:] if n else []:
            print(f"--- to {m.to}: {m.subject}\n{m.body}")
        if once:
            break
        await asyncio.sleep(1 if n else 5)
    await engine.dispose()


async def email_test(to: str) -> None:
    """Send one message straight through the configured sender, to prove the SES set-up
    (verified identity, region, IAM permission, sandbox) before real email depends on it."""
    from syntegra.platform.email import Message, build_sender

    settings = get_settings()
    sender = build_sender(settings)
    message_id = await sender.send(
        Message(
            to,
            "Syntegra email test",
            "This is a test message from Syntegra Education. No action is needed.",
            settings.email_from,
            "email-test",
        )
    )
    print(f"sent through {settings.email_sender} from {settings.email_from}: {message_id or 'ok'}")


async def seed_bodies(catalogues: list[str]) -> dict[str, list[str]]:
    """Add or update external education bodies from catalogue files (ADR-019)."""
    from syntegra.institutions.bodies import seed_catalogue
    from syntegra.institutions.body_rules import load_catalogue

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    out: dict[str, list[str]] = {"created": [], "updated": []}
    async with sm() as session, session.begin():
        for name in catalogues:
            seeded = await seed_catalogue(session, load_catalogue(name))
            out["created"] += seeded["created"]
            out["updated"] += seeded["updated"]
    await engine.dispose()
    return out


async def import_body_links(text: str, tenant_code: str, board_code: str, commit: bool) -> int:
    """Check (and with --commit, record) a CSV of institution links: exam centre numbers, JAMB
    codes, accreditations (ADR-019), for the institutions under one body."""
    from syntegra.institutions.bodies import import_links

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        state = await governance.tenant_by_code(session, tenant_code)
        board = (
            await governance.scope_node_by_code(session, state.id, board_code) if state else None
        )
        if state is None or board is None:
            raise SystemExit(f"{board_code} not found in {tenant_code}: run seed-structure first")
        with use_context(
            RequestContext(state.id, board.id, None, system=True, purpose="body-link-import")
        ):
            summary, problems = await import_links(session, text, commit=commit)
    await engine.dispose()
    print(json.dumps(summary, indent=2))
    for n, message in problems[:100]:
        print(f"row {n}: {message}")
    if not commit:
        print("check only: nothing recorded. Run again with --commit to record the valid rows.")
    return 0


async def sync_roles() -> None:
    """Add built-in roles and permissions introduced since this database was set up (e.g. the
    HR officer role and staff and appraisal permissions, ADR-024, ADR-025)."""
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        created = await iam.seed_builtin_roles(session)
    await engine.dispose()
    print(f"built-in roles up to date ({created} new)")


async def backfill_addresses(tenant_code: str) -> int:
    """Give institutions registered before the gazetteer (ADR-022) their PREMISES address."""
    from sqlalchemy import select

    from syntegra.institutions.models import Institution
    from syntegra.mdm.gazetteer import addresses_of, link_institution_premises

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    done = 0
    async with sm() as session, session.begin():
        tenant = await governance.tenant_by_code(session, tenant_code)
        if tenant is None:
            raise SystemExit(f"{tenant_code} not found")
        with use_context(
            RequestContext(
                tenant.id, tenant.root_scope_node_id, None, system=True, purpose="backfill"
            )
        ):
            for inst in (await session.execute(select(Institution))).scalars().all():
                if not await addresses_of(session, institution_id=inst.id):
                    await link_institution_premises(session, inst)
                    done += 1
    await engine.dispose()
    print(f"linked premises addresses for {done} institutions")
    return 0


async def email_events(once: bool) -> None:
    """Apply SES bounce, complaint and delivery events from the SQS queue (ADR-018)."""
    import boto3

    from syntegra.platform.email_feedback import poll_once

    settings = get_settings()
    if not settings.ses_events_queue_url:
        raise SystemExit("set SYNTEGRA_SES_EVENTS_QUEUE_URL to the SES events queue")
    sqs = boto3.client("sqs", region_name=settings.ses_region or settings.residency_region)
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    while True:
        handled, failed = await poll_once(
            sm, sqs, settings.ses_events_queue_url, wait_seconds=1 if once else 20
        )
        if handled or failed or once:
            print(f"applied {handled}, failed {failed}")
        if once:
            break
    await engine.dispose()


async def email_unsuppress(address: str) -> None:
    from syntegra.platform.email_feedback import unsuppress

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        found = await unsuppress(session, address)
    await engine.dispose()
    print("address allowed again" if found else "address was not suppressed")


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


async def configure_sso(tenant_code: str, providers_json: str) -> list[str]:
    """Set an organisation's identity providers from a JSON list (ADR-016)."""
    from syntegra.iam import sso

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        found = await sso.set_identity_providers(session, tenant_code, json.loads(providers_json))
    await engine.dispose()
    missing = [
        p.client_secret_ref
        for p in found
        if p.client_secret_ref and p.client_secret_ref not in settings.oidc_client_secrets
    ]
    if missing:
        print("note: add these to SYNTEGRA_OIDC_CLIENT_SECRETS: " + ", ".join(missing))
    print(f"register this redirect URI with each provider: {settings.redirect_uri}")
    return [p.key for p in found]


async def reset_mfa(tenant_code: str, login: str, reason: str) -> None:
    """Lost phone: remove the user's authenticator app and recovery codes (audited)."""
    from syntegra.iam import sso

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    async with sm() as session, session.begin():
        tenant = await governance.tenant_by_code(session, tenant_code)
        user = await iam.user_by_login(session, tenant.id, login) if tenant else None
        if user is None:
            raise SystemExit(f"{login} not found in {tenant_code}")
        await sso.reset_mfa(session, user_id=user.id, reason=reason)
    await engine.dispose()
    print(f"MFA reset for {login}: they will set up a new authenticator at their next sign-in")


def main() -> None:
    p = argparse.ArgumentParser(prog="syntegra")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    b = sub.add_parser("bootstrap")
    b.add_argument("--admin", required=True)
    b.add_argument("--code", default="SYNTEGRA")
    b.add_argument("--name", default="Syntegra programme")
    b.add_argument("--region", default=get_settings().residency_region)
    st = sub.add_parser("seed-structure", help="build a state or country from its structure file")
    st.add_argument("structure", help="a structure file, or a bundled name such as ng-abia")
    st.add_argument("--platform-code", default="SYNTEGRA")
    st.add_argument("--admin", help="login for a jurisdiction administrator (state officer role)")
    a = sub.add_parser(
        "seed-abia", aliases=["seed-asubeb"], help="shortcut for: seed-structure ng-abia"
    )
    a.add_argument("--platform-code", default="SYNTEGRA")
    a.add_argument("--admin", help="login for an Abia State administrator (state officer role)")
    imp = sub.add_parser(
        "import-institutions",
        aliases=["import-schools"],
        help="preview or register a CSV list of institutions for one body",
    )
    imp.add_argument("csv_path")
    imp.add_argument("--tenant-code", default="NG-AB")
    imp.add_argument("--board-code", default="ASUBEB")
    imp.add_argument(
        "--commit", action="store_true", help="register the valid rows (default: preview only)"
    )
    em = sub.add_parser("send-email", help="deliver queued email (dev: printed)")
    em.add_argument("--once", action="store_true")
    et = sub.add_parser("email-test", help="send one test message through the configured sender")
    et.add_argument("--to", required=True)
    sb = sub.add_parser("seed-bodies", help="add or update external education bodies")
    sb.add_argument("catalogues", nargs="*", default=["ng", "international"])
    il = sub.add_parser(
        "import-body-links", help="exam centre numbers, JAMB codes, accreditations from a CSV"
    )
    il.add_argument("csv_path")
    il.add_argument("--tenant-code", default="NG-AB")
    il.add_argument("--board-code", default="ASUBEB")
    il.add_argument("--commit", action="store_true")
    sub.add_parser("sync-roles", help="add new built-in roles and permissions")
    bf = sub.add_parser("backfill-addresses", help="premises addresses for existing institutions")
    bf.add_argument("--tenant-code", default="NG-AB")
    ev = sub.add_parser("email-events", help="apply SES bounce/complaint/delivery events")
    ev.add_argument("--once", action="store_true")
    us = sub.add_parser("email-unsuppress", help="allow a suppressed email address again")
    us.add_argument("--address", required=True)
    r = sub.add_parser("relay")
    r.add_argument("--once", action="store_true")
    sso_p = sub.add_parser("configure-sso", help="set an organisation's identity providers")
    sso_p.add_argument("tenant_code", help="e.g. NG-AB (schools beneath it inherit)")
    sso_p.add_argument("providers_json", help="path to a JSON list of identity providers")
    rm = sub.add_parser("reset-mfa", help="lost phone: user sets up a new authenticator")
    rm.add_argument("--tenant-code", required=True)
    rm.add_argument("--login", required=True)
    rm.add_argument("--reason", required=True)
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
    elif args.cmd == "seed-structure":
        out = asyncio.run(seed_structure(args.structure, args.platform_code, args.admin))
        print(json.dumps(out, indent=2))
    elif args.cmd in ("seed-abia", "seed-asubeb"):
        out = asyncio.run(seed_structure("ng-abia", args.platform_code, args.admin))
        print(json.dumps(out, indent=2))
    elif args.cmd in ("import-institutions", "import-schools"):
        from pathlib import Path

        csv_file = Path(args.csv_path)
        text = csv_file.read_text(encoding="utf-8-sig")
        raise SystemExit(
            asyncio.run(
                import_schools(text, csv_file.name, args.tenant_code, args.board_code, args.commit)
            )
        )
    elif args.cmd == "send-email":
        asyncio.run(send_email(args.once))
    elif args.cmd == "email-test":
        asyncio.run(email_test(args.to))
    elif args.cmd == "seed-bodies":
        print(json.dumps(asyncio.run(seed_bodies(args.catalogues)), indent=2))
    elif args.cmd == "import-body-links":
        from pathlib import Path

        links_text = Path(args.csv_path).read_text(encoding="utf-8-sig")
        raise SystemExit(
            asyncio.run(
                import_body_links(links_text, args.tenant_code, args.board_code, args.commit)
            )
        )
    elif args.cmd == "sync-roles":
        asyncio.run(sync_roles())
    elif args.cmd == "backfill-addresses":
        raise SystemExit(asyncio.run(backfill_addresses(args.tenant_code)))
    elif args.cmd == "email-events":
        asyncio.run(email_events(args.once))
    elif args.cmd == "email-unsuppress":
        asyncio.run(email_unsuppress(args.address))
    elif args.cmd == "relay":
        asyncio.run(relay(args.once))
    elif args.cmd == "configure-sso":
        from pathlib import Path

        keys = asyncio.run(
            configure_sso(args.tenant_code, Path(args.providers_json).read_text(encoding="utf-8"))
        )
        print("identity providers: " + ", ".join(keys))
    elif args.cmd == "reset-mfa":
        asyncio.run(reset_mfa(args.tenant_code, args.login, args.reason))
    elif args.cmd == "verify-audit":
        raise SystemExit(asyncio.run(verify_audit(args.day)))


if __name__ == "__main__":
    main()
