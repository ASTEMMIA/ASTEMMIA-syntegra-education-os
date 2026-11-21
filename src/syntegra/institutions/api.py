"""School register services: register one school, and bulk import with preview then commit."""

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
    LEVEL_LABEL,
    MAX_ROWS,
    TEMPLATE_COLUMNS,
    CleanRow,
    Level,
    RowResult,
    fold,
    mark_duplicates,
    summarise,
    validate_row,
)
from syntegra.institutions.models import Institution, InstitutionLevel
from syntegra.platform.models import BulkJob, BulkJobRow
from syntegra.platform.outbox import publish
from syntegra.platform.sequence import next_value

KIND = "institutions"
MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class InstitutionView:
    id: uuid.UUID
    institution_code: str
    government_code: str | None
    name: str
    lga: str
    lga_scope_id: uuid.UUID
    scope_node_id: uuid.UUID
    levels: list[str]
    town: str | None
    status: str


def template_csv() -> str:
    example = [
        "Community Primary School Ndume",
        "Umuahia North",
        "Primary",
        "",
        "Ndume",
        "",
        "",
        "",
        "",
        "",
    ]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(TEMPLATE_COLUMNS)
    w.writerow(example)
    return out.getvalue()


async def _lgea_index(session: AsyncSession) -> dict[str, tuple[str, str]]:
    """The LGEAs of the board the caller works in: the children of the active scope that sit
    above institution level (ADR-010). Keys are fold(name) and fold(name minus 'LGEA')."""
    ctx = current_context()
    index: dict[str, tuple[str, str]] = {}
    after = None
    while True:
        page = await governance.list_children(session, ctx.scope_node_id, after=after, limit=200)
        for n in page:
            if n.level < GovernanceLevel.INSTITUTION:
                bare = n.name.removesuffix(" LGEA").strip()
                index[fold(n.name)] = (str(n.id), bare)
                index[fold(bare)] = (str(n.id), bare)
        if len(page) < 200:
            return index
        after = page[-1].id


LEVELS_SETTING = "register.levels"
DEFAULT_LEVELS = frozenset({Level.ECCDE, Level.PRIMARY, Level.JSS, Level.SSS})


async def allowed_levels(session: AsyncSession) -> frozenset[Level]:
    """Levels the caller's board registers, from configuration (P4); default: basic education."""
    value = await governance.resolve_config(
        session, current_context().scope_node_id, LEVELS_SETTING
    )
    if not value:
        return DEFAULT_LEVELS
    return frozenset(Level(v) for v in value)


async def _existing(session: AsyncSession) -> tuple[set[str], set[tuple[str, str]]]:
    rows = (
        await session.execute(
            select(Institution.government_code, Institution.lga_scope_id, Institution.name_key)
        )
    ).all()
    return ({c for c, _, _ in rows if c}, {(str(lga), key) for _, lga, key in rows})


async def _create(session: AsyncSession, c: CleanRow, *, managing_body: str | None) -> Institution:
    ctx = current_context()
    seq = await next_value(session, ctx.tenant_id, "institution")
    code = institution_id("NG", seq)
    node = await governance.create_scope_node(
        session,
        parent_id=uuid.UUID(c.lga_scope_id),
        level=GovernanceLevel.INSTITUTION,
        code=code,
        name=c.name,
        local_label="School",
    )
    inst = Institution(
        scope_node_id=node.id,
        institution_code=code,
        government_code=c.government_code,
        name=c.name,
        name_key=c.name_key,
        lga_scope_id=uuid.UUID(c.lga_scope_id),
        ownership="public",
        managing_body=managing_body,
        town=c.town,
        address=c.address,
        phone=c.phone,
        email=c.email,
        latitude=Decimal(str(c.latitude)) if c.latitude is not None else None,
        longitude=Decimal(str(c.longitude)) if c.longitude is not None else None,
    )
    session.add(inst)
    await session.flush()
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
            payload={"institution_code": code, "levels": [lv.value for lv in c.levels]},
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
    """Register one school (same validation as an import row)."""
    result = validate_row(1, raw, await _lgea_index(session), allowed=await allowed_levels(session))
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
    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    missing = [c for c in ("name", "lga", "levels") if c not in headers]
    if missing:
        raise ValidationFailed(
            f"these columns are missing: {', '.join(missing)}",
            next_action="Use the column names from the template (name, lga, levels, …).",
        )
    rows = []
    for raw in reader:
        rows.append({k: (raw.get(headers[k]) or "") for k in TEMPLATE_COLUMNS if k in headers})
        if len(rows) > MAX_ROWS:
            raise ValidationFailed(
                f"the file has more than {MAX_ROWS} rows", next_action="Split it by LGA."
            )
    if not rows:
        raise ValidationFailed("the file has a header but no schools")
    return rows


async def preview_import(
    session: AsyncSession, csv_text: str, *, source_name: str | None
) -> uuid.UUID:
    """Steps 1-2 of §10.4: parse and validate every row. Nothing is registered yet."""
    ctx = current_context()
    rows = _parse_csv(csv_text)
    lgas = await _lgea_index(session)
    if not lgas:
        raise ValidationFailed(
            "there are no LGEAs under the scope you are working in",
            next_action="Switch to your board (for example ASUBEB) and upload again.",
        )
    allowed = await allowed_levels(session)
    # Row 1 is the header, so data rows are numbered from 2 as in the spreadsheet.
    results = [validate_row(i, raw, lgas, allowed=allowed) for i, raw in enumerate(rows, start=2)]
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
    so a school registered since the preview is skipped, not duplicated."""
    ctx = current_context()
    job, rows = await get_job(session, job_id)
    if job.kind != KIND:
        raise ValidationFailed("this is not a school import")
    if job.status != "previewed":
        raise Conflict("this import has already been committed", next_action="Start a new import.")
    lgas = await _lgea_index(session)
    allowed = await allowed_levels(session)
    candidates = [r for r in rows if r.status == "valid"]
    rechecked = [validate_row(r.row_no, _as_raw(r.data), lgas, allowed=allowed) for r in candidates]
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
        raise NotFound("school not found")
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
    names: dict[uuid.UUID, str] = {}
    out = []
    for i in items:
        if i.lga_scope_id not in names:
            names[i.lga_scope_id] = (
                await governance.get_scope_node(session, i.lga_scope_id)
            ).name.removesuffix(" LGEA")
        out.append(
            InstitutionView(
                i.id,
                i.institution_code,
                i.government_code,
                i.name,
                names[i.lga_scope_id],
                i.lga_scope_id,
                i.scope_node_id,
                [LEVEL_LABEL[Level(x)] for x in sorted(levels[i.id], key=order.index)],
                i.town,
                i.status,
            )
        )
    return out


async def list_institutions(
    session: AsyncSession,
    *,
    lga_scope_id: uuid.UUID | None,
    after: uuid.UUID | None,
    limit: int,
) -> list[InstitutionView]:
    stmt = select(Institution).order_by(Institution.id).limit(limit)
    if lga_scope_id is not None:
        stmt = stmt.where(Institution.lga_scope_id == lga_scope_id)
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
    "preview_import",
    "register_institution",
    "template_csv",
]
