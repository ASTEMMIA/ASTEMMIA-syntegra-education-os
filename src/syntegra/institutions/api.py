"""Institution register services, kindergarten to university: register one institution, and
bulk import with preview then commit (CEDA §4, §10.4; ADR-010, ADR-012)."""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import institution_id
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel
from syntegra.institutions import events
from syntegra.institutions.domain import (
    COLUMN_ALIASES,
    LEVEL_LABEL,
    MAX_ROWS,
    SCHOOL_LEVELS,
    TEMPLATE_COLUMNS,
    TYPE_LABEL,
    AddressRules,
    CleanRow,
    InstitutionType,
    Level,
    Placement,
    RowResult,
    compose_address,
    fold,
    mark_duplicates,
    summarise,
    unit_key,
    validate_row,
)
from syntegra.institutions.models import Institution, InstitutionLevel
from syntegra.platform.models import BulkJob, BulkJobRow
from syntegra.platform.outbox import publish
from syntegra.platform.sequence import next_value

KIND = "institutions"
MAX_BYTES = 5 * 1024 * 1024
LEVELS_SETTING = "register.levels"
AREAS_SETTING = "location.areas"


@dataclass(frozen=True, slots=True)
class InstitutionView:
    id: uuid.UUID
    institution_code: str
    government_code: str | None
    name: str
    institution_type: str
    location: str
    parent_scope_id: uuid.UUID
    scope_node_id: uuid.UUID
    levels: list[str]
    address_line1: str | None
    address_line2: str | None
    town: str | None
    postcode: str | None
    full_address: str
    status: str
    ownership: str = "public"
    proprietor_name: str | None = None


def template_csv() -> str:
    """Header plus two example rows (one school, one tertiary institution), built by column
    name so the examples always line up with the template."""
    examples = [
        {
            "name": "Community Primary School Ndume",
            "lga": "Umuahia North",
            "levels": "Primary",
            "address_line1": "1 School Road",
            "address_line2": "Off Ndume Market",
            "town": "Ndume",
            "postcode": "440001",
        },
        {
            "name": "Example State Polytechnic",
            "type": "Polytechnic",
            "lga": "Aba South",
            "levels": "ND; HND",
            "address_line1": "1 Polytechnic Road",
            "town": "Aba",
            "postcode": "450001",
        },
    ]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(TEMPLATE_COLUMNS)
    for ex in examples:
        w.writerow([ex.get(c, "") for c in TEMPLATE_COLUMNS])
    return out.getvalue()


ADDRESS_SETTING = "address.rules"


async def address_rules(session: AsyncSession) -> AddressRules:
    """Postcode format and required address parts for the caller's jurisdiction (P4)."""
    value = await governance.resolve_config(
        session, current_context().scope_node_id, ADDRESS_SETTING
    )
    if not value:
        return AddressRules()
    return AddressRules(
        postcode_pattern=value.get("postcode_pattern"),
        postcode_label=value.get("postcode_label") or "postcode",
        postcode_example=value.get("postcode_example"),
        required=frozenset(value.get("required") or []),
        state=value.get("state"),
        country=value.get("country"),
    )


async def placement(session: AsyncSession) -> Placement:
    """Where the caller's rows go: the body they work in (its units, or the body itself) and
    the jurisdiction's local government areas (setting ``location.areas``, from the structure
    file). Working at the jurisdiction root is refused: an import belongs to one body."""
    ctx = current_context()
    root = await governance.tenant_root_scope(session, ctx.tenant_id)
    if ctx.scope_node_id == root:
        raise ValidationFailed(
            "choose the body you are registering institutions for",
            next_action="Switch to the body (for example ASUBEB or the Ministry) and upload again.",
        )
    units: dict[str, tuple[str, str]] = {}
    after = None
    while True:
        page = await governance.list_children(session, ctx.scope_node_id, after=after, limit=200)
        for n in page:
            if n.level < GovernanceLevel.INSTITUTION:
                suffix = f" {n.local_label}" if n.local_label else ""
                bare = n.name.removesuffix(suffix).strip() if suffix else n.name
                for key in {*unit_key(n.name), *unit_key(bare)}:
                    units[key] = (str(n.id), bare)
        if len(page) < 200:
            break
        after = page[-1].id
    areas: dict[str, str] = {}
    for name in await governance.resolve_config(session, ctx.scope_node_id, AREAS_SETTING) or []:
        for key in unit_key(name):
            areas[key] = name
    return Placement(str(ctx.scope_node_id), units, areas)


async def self_registering_levels(session: AsyncSession) -> frozenset[Level]:
    """Levels whose PRIVATE institutions apply online to the active body rather than being
    listed by it (state directive; setting ``private.registration``, ADR-014, ADR-021)."""
    ctx = current_context()
    node = await governance.get_scope_node(session, ctx.scope_node_id)
    root = await governance.tenant_root_scope(session, ctx.tenant_id)
    for entry in await governance.resolve_config(session, root, "private.registration") or []:
        if entry.get("body") == node.code:
            return frozenset(Level(v) for v in entry.get("levels", []))
    return frozenset()


async def allowed_levels(session: AsyncSession) -> frozenset[Level]:
    """Levels the caller's body registers, from configuration (P4); default: basic education."""
    value = await governance.resolve_config(
        session, current_context().scope_node_id, LEVELS_SETTING
    )
    if value is None:  # not configured: basic education
        return SCHOOL_LEVELS
    return frozenset(Level(v) for v in value)  # an empty list means this body registers none yet


async def _existing(session: AsyncSession) -> tuple[set[str], set[tuple[str, str, str]]]:
    rows = (
        await session.execute(
            select(
                Institution.government_code,
                Institution.parent_scope_id,
                Institution.location_key,
                Institution.name_key,
            )
        )
    ).all()
    return ({c for c, *_ in rows if c}, {(str(p), loc, key) for _, p, loc, key in rows})


async def _create(session: AsyncSession, c: CleanRow, *, managing_body: str | None) -> Institution:
    ctx = current_context()
    seq = await next_value(session, ctx.tenant_id, "institution")
    code = institution_id("NG", seq)
    node = await governance.create_scope_node(
        session,
        parent_id=uuid.UUID(c.parent_scope_id),
        level=GovernanceLevel.INSTITUTION,
        code=code,
        name=c.name,
        local_label=TYPE_LABEL[c.institution_type],
    )
    inst = Institution(
        scope_node_id=node.id,
        institution_code=code,
        government_code=c.government_code,
        name=c.name,
        name_key=c.name_key,
        parent_scope_id=uuid.UUID(c.parent_scope_id),
        location=c.location,
        location_key=fold(c.location),
        institution_type=c.institution_type.value,
        ownership=c.ownership,
        proprietor_name=c.proprietor_name,
        managing_body=managing_body,
        address_line1=c.address_line1,
        address_line2=c.address_line2,
        town=c.town,
        postcode=c.postcode,
        phone=c.phone,
        email=c.email,
        latitude=Decimal(str(c.latitude)) if c.latitude is not None else None,
        longitude=Decimal(str(c.longitude)) if c.longitude is not None else None,
    )
    session.add(inst)
    await session.flush()
    from syntegra.mdm.gazetteer import link_institution_premises  # avoids an import cycle

    await link_institution_premises(session, inst)
    for lv in c.levels:
        session.add(InstitutionLevel(scope_node_id=node.id, institution_id=inst.id, level=lv.value))
    publish(
        session,
        EventEnvelope(
            event_type=events.INSTITUTION_REGISTERED,
            tenant_id=inst.tenant_id,
            scope_node_id=node.id,
            aggregate_type="institution",
            aggregate_id=inst.id,
            actor_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
            payload={
                "institution_code": code,
                "type": c.institution_type.value,
                "ownership": c.ownership,
                "levels": [lv.value for lv in c.levels],
            },
        ),
    )
    await session.flush()
    return inst


async def register_institution(
    session: AsyncSession,
    raw: dict[str, str],
    *,
    managing_body: str | None = None,
) -> InstitutionView:
    """Register one institution (same validation as an import row)."""
    result = validate_row(
        1,
        raw,
        await placement(session),
        allowed=await allowed_levels(session),
        address=await address_rules(session),
        self_registering=await self_registering_levels(session),
    )
    codes, names = await _existing(session)
    mark_duplicates([result], existing_codes=codes, existing_names=names)
    if result.clean is None:
        raise ValidationFailed(
            "; ".join(result.errors), next_action="Correct the details and try again."
        )
    inst = await _create(session, result.clean, managing_body=managing_body)
    return await get_institution(session, inst.id)


def _parse_csv(text: str) -> list[dict[str, str]]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValidationFailed(
            "the file is larger than 5 MB", next_action="Split it by LGA and import each part."
        )
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        raise ValidationFailed(
            "the file is empty", next_action="Download the template and fill it in."
        )
    headers: dict[str, str] = {}
    for h in reader.fieldnames:
        if not h:
            continue
        key = h.strip().lower()
        headers.setdefault(COLUMN_ALIASES.get(key, key), h)
    missing = [c for c in ("name", "lga", "levels") if c not in headers]
    if missing:
        raise ValidationFailed(
            f"these columns are missing: {', '.join(missing)}",
            next_action="Use the column names from the template (name, type, lga, levels, …).",
        )
    rows = []
    for raw in reader:
        rows.append({k: (raw.get(headers[k]) or "") for k in TEMPLATE_COLUMNS if k in headers})
        if len(rows) > MAX_ROWS:
            raise ValidationFailed(
                f"the file has more than {MAX_ROWS} rows", next_action="Split it by LGA."
            )
    if not rows:
        raise ValidationFailed("the file has a header but no institutions")
    return rows


async def preview_import(
    session: AsyncSession, csv_text: str, *, source_name: str | None
) -> uuid.UUID:
    """Steps 1-2 of §10.4: parse and validate every row. Nothing is registered yet."""
    ctx = current_context()
    rows = _parse_csv(csv_text)
    where = await placement(session)
    allowed = await allowed_levels(session)
    # Row 1 is the header, so data rows are numbered from 2 as in the spreadsheet.
    rules = await address_rules(session)
    private_online = await self_registering_levels(session)
    results = [
        validate_row(i, raw, where, allowed=allowed, address=rules, self_registering=private_online)
        for i, raw in enumerate(rows, start=2)
    ]
    codes, names = await _existing(session)
    mark_duplicates(results, existing_codes=codes, existing_names=names)
    job = BulkJob(
        tenant_id=ctx.tenant_id,
        scope_node_id=ctx.scope_node_id,
        kind=KIND,
        status="previewed",
        source_name=source_name,
        summary=summarise(results),
        created_by=ctx.user_id,
    )
    session.add(job)
    await session.flush()
    for r in results:
        session.add(
            BulkJobRow(
                job_id=job.id,
                row_no=r.row_no,
                data=r.clean.as_json() if r.clean else r.raw,
                status="valid" if r.clean else "error",
                messages=r.errors,
            )
        )
    await session.flush()
    return job.id


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> tuple[BulkJob, list[BulkJobRow]]:
    ctx = current_context()
    job = (
        await session.execute(
            select(BulkJob).where(BulkJob.id == job_id, BulkJob.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFound("import not found")
    rows = list(
        (
            await session.execute(
                select(BulkJobRow).where(BulkJobRow.job_id == job.id).order_by(BulkJobRow.row_no)
            )
        ).scalars()
    )
    return job, rows


async def commit_import(
    session: AsyncSession, job_id: uuid.UUID, *, managing_body: str | None
) -> dict[str, Any]:
    """Steps 4-5 of §10.4: register every valid row and report. Rows are re-checked first,
    so an institution registered since the preview is skipped, not duplicated."""
    ctx = current_context()
    job, rows = await get_job(session, job_id)
    if job.kind != KIND:
        raise ValidationFailed("this is not an institution import")
    if job.status != "previewed":
        raise Conflict("this import has already been committed", next_action="Start a new import.")
    if job.scope_node_id != ctx.scope_node_id:
        raise ValidationFailed(
            "this import was checked for another body",
            next_action="Switch to the body it was uploaded for, then register it.",
        )
    where = await placement(session)
    allowed = await allowed_levels(session)
    candidates = [r for r in rows if r.status == "valid"]
    rechecked = [
        validate_row(
            r.row_no,
            _as_raw(r.data),
            where,
            allowed=allowed,
            address=await address_rules(session),
            self_registering=await self_registering_levels(session),
        )
        for r in candidates
    ]
    codes, names = await _existing(session)
    mark_duplicates(rechecked, existing_codes=codes, existing_names=names)
    created = skipped = 0
    for row, result in zip(candidates, rechecked, strict=True):
        if result.clean is None:
            row.status, row.messages = "skipped", result.errors
            skipped += 1
            continue
        inst = await _create(session, result.clean, managing_body=managing_body)
        row.status, row.result_id = "created", inst.id
        created += 1
    job.status, job.committed_by, job.committed_at = "committed", ctx.user_id, utcnow()
    job.summary = {**job.summary, "created": created, "skipped": skipped}
    await session.flush()
    return job.summary


def _as_raw(data: dict[str, Any]) -> dict[str, str]:
    raw = {k: "" if data.get(k) is None else str(data.get(k)) for k in TEMPLATE_COLUMNS}
    if isinstance(data.get("levels"), list):
        raw["levels"] = ";".join(data["levels"])
    return raw


async def get_institution(session: AsyncSession, inst_id: uuid.UUID) -> InstitutionView:
    inst = (
        await session.execute(select(Institution).where(Institution.id == inst_id))
    ).scalar_one_or_none()
    if inst is None:
        raise NotFound("institution not found")
    return (await _views(session, [inst]))[0]


async def _views(session: AsyncSession, items: list[Institution]) -> list[InstitutionView]:
    ids = [i.id for i in items]
    levels: dict[uuid.UUID, list[str]] = {i: [] for i in ids}
    if ids:
        for lv in (
            await session.execute(
                select(InstitutionLevel).where(InstitutionLevel.institution_id.in_(ids))
            )
        ).scalars():
            levels[lv.institution_id].append(lv.level)
    order = [lv.value for lv in Level]
    rules = await address_rules(session) if items else AddressRules()
    return [
        InstitutionView(
            i.id,
            i.institution_code,
            i.government_code,
            i.name,
            TYPE_LABEL[InstitutionType(i.institution_type)],
            i.location,
            i.parent_scope_id,
            i.scope_node_id,
            [LEVEL_LABEL[Level(x)] for x in sorted(levels[i.id], key=order.index)],
            i.address_line1,
            i.address_line2,
            i.town,
            i.postcode,
            compose_address(
                line1=i.address_line1,
                line2=i.address_line2,
                town=i.town,
                location=i.location,
                postcode=i.postcode,
                state=rules.state,
                country=rules.country,
            ),
            i.status,
            i.ownership,
            i.proprietor_name,
        )
        for i in items
    ]


async def list_institutions(
    session: AsyncSession,
    *,
    parent_scope_id: uuid.UUID | None = None,
    institution_type: str | None = None,
    ownership: str | None = None,
    after: uuid.UUID | None = None,
    limit: int = 50,
) -> list[InstitutionView]:
    stmt = select(Institution).order_by(Institution.id).limit(limit)
    if ownership is not None:
        stmt = stmt.where(Institution.ownership == ownership)
    if parent_scope_id is not None:
        stmt = stmt.where(Institution.parent_scope_id == parent_scope_id)
    if institution_type is not None:
        stmt = stmt.where(Institution.institution_type == institution_type)
    if after is not None:
        stmt = stmt.where(Institution.id > after)
    return await _views(session, list((await session.execute(stmt)).scalars()))


__all__ = [
    "InstitutionView",
    "RowResult",
    "commit_import",
    "get_institution",
    "get_job",
    "list_institutions",
    "placement",
    "preview_import",
    "register_institution",
    "template_csv",
]
