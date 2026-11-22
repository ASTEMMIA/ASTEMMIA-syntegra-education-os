"""Education bodies and institutions' links to them (ADR-019).

* **Internal bodies** are the jurisdiction's own: in Abia, ASUBEB and the Ministry of Tertiary
  Education. They are the state's structure (ADR-010, ADR-011), listed here from it.
* **External bodies** come from catalogue files: WAEC, NECO, NABTEB, JAMB, NUC, NBTE, NCCE…
  An institution's standing with one (an exam centre number, a JAMB code, an accreditation) is an
  ``InstitutionBodyLink``, checked against what the body does and covers (``body_rules``).
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.governance import api as governance
from syntegra.institutions import events
from syntegra.institutions.body_rules import (
    LINK_LABEL,
    BodySpec,
    Catalogue,
    LinkType,
    check_link,
)
from syntegra.institutions.models import (
    EducationBody,
    Institution,
    InstitutionBodyLink,
    InstitutionLevel,
)
from syntegra.platform.outbox import publish
from syntegra.platform.session import PLATFORM_BYPASS

CURRENT = ("active", "provisional")
LINK_COLUMNS = (
    "institution",
    "body",
    "link_type",
    "exam",
    "reference",
    "status",
    "valid_from",
    "valid_to",
    "note",
)
MAX_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class BodyView:
    code: str
    name: str
    scope: str
    countries: list[str]
    roles: list[str]
    covers: dict[str, Any]
    examinations: list[dict[str, Any]]
    note: str | None
    link_types: list[str]


@dataclass(frozen=True, slots=True)
class LinkView:
    id: uuid.UUID
    institution_id: uuid.UUID
    institution_code: str
    institution_name: str
    body_code: str
    body_name: str
    link_type: str
    exam_code: str | None
    exam_name: str | None
    reference: str | None
    status: str
    valid_from: date | None
    valid_to: date | None
    note: str | None

    @property
    def current(self) -> bool:
        return self.status in CURRENT and (self.valid_to is None or self.valid_to >= today())


def _spec(b: EducationBody) -> BodySpec:
    return BodySpec.model_validate(
        {
            "code": b.code,
            "name": b.name,
            "scope": b.scope,
            "countries": b.countries,
            "roles": b.roles,
            "covers": b.covers,
            "examinations": b.examinations,
            "note": b.note,
        }
    )


def _body_view(b: EducationBody) -> BodyView:
    spec = _spec(b)
    return BodyView(
        b.code,
        b.name,
        b.scope,
        list(b.countries),
        list(b.roles),
        dict(b.covers),
        list(b.examinations),
        b.note,
        sorted(t.value for t in spec.link_types),
    )


# --------------------------------------------------------------------------- the register


async def seed_catalogue(session: AsyncSession, catalogue: Catalogue) -> dict[str, list[str]]:
    """Add or update every body in a catalogue. Idempotent; bodies are never deleted here."""
    out: dict[str, list[str]] = {"created": [], "updated": []}
    existing = {
        b.code: b
        for b in (
            await session.execute(
                select(EducationBody).where(
                    EducationBody.code.in_([s.code for s in catalogue.bodies])
                )
            )
        ).scalars()
    }
    for spec in catalogue.bodies:
        values = {
            "name": spec.name,
            "scope": spec.scope,
            "countries": list(spec.countries),
            "roles": [r.value for r in spec.roles],
            "covers": spec.covers.model_dump(mode="json"),
            "examinations": [e.model_dump(mode="json") for e in spec.examinations],
            "note": spec.note,
            "catalogue": catalogue.name,
            "active": True,
        }
        row = existing.get(spec.code)
        if row is None:
            session.add(EducationBody(code=spec.code, **values))
            out["created"].append(spec.code)
        elif any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            out["updated"].append(spec.code)
    await session.flush()
    return out


async def list_bodies(
    session: AsyncSession, *, role: str | None = None, country: str | None = None
) -> list[BodyView]:
    rows = (
        (
            await session.execute(
                select(EducationBody)
                .where(EducationBody.active.is_(True))
                .order_by(EducationBody.code)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for b in rows:
        if role and role not in b.roles:
            continue
        if country and b.countries and country.upper() not in b.countries:
            continue
        out.append(_body_view(b))
    return out


async def _body(session: AsyncSession, code: str) -> EducationBody:
    b = (
        await session.execute(
            select(EducationBody).where(
                EducationBody.code == code.strip().upper(), EducationBody.active.is_(True)
            )
        )
    ).scalar_one_or_none()
    if b is None:
        raise NotFound(
            f"'{code}' is not in the register of education bodies",
            next_action="List the bodies with GET /v1/education-bodies.",
        )
    return b


async def get_body(session: AsyncSession, code: str) -> BodyView:
    return _body_view(await _body(session, code))


async def jurisdiction_bodies(session: AsyncSession) -> list[dict[str, str]]:
    """The caller's own jurisdiction's bodies (in Abia: ASUBEB, the Ministry), from its
    structure: the scope nodes directly beneath the tenant root."""
    ctx = current_context()
    root = await governance.tenant_root_scope(session, ctx.tenant_id)
    nodes = await governance.list_children(session, root, after=None, limit=200)
    return [
        {"code": n.code, "name": n.name, "label": n.local_label or "", "scope_node_id": str(n.id)}
        for n in nodes
    ]


# --------------------------------------------------------------------------- links


async def _institution(session: AsyncSession, key: str | uuid.UUID) -> Institution:
    """By id, institution code (INS-…) or the government's own code, within the caller's scope."""
    if isinstance(key, uuid.UUID):
        cond = Institution.id == key
    else:
        k = key.strip()
        cond = (Institution.institution_code == k.upper()) | (Institution.government_code == k)
    found = (await session.execute(select(Institution).where(cond))).scalars().all()
    if len(found) != 1:
        raise NotFound(
            "institution not found in your scope"
            if not found
            else f"'{key}' matches more than one institution: use its INS code"
        )
    return found[0]


async def _levels(session: AsyncSession, institution_id: uuid.UUID) -> set[str]:
    return set(
        (
            await session.execute(
                select(InstitutionLevel.level).where(
                    InstitutionLevel.institution_id == institution_id
                )
            )
        ).scalars()
    )


@dataclass(frozen=True, slots=True)
class _Prepared:
    inst: Institution
    body: EducationBody
    link_type: str
    exam_code: str | None
    reference: str | None
    status: str
    valid_from: date | None
    valid_to: date | None
    note: str | None

    @property
    def key(self) -> tuple[str, str, str | None, str | None]:
        return (self.body.code, self.link_type, self.exam_code, self.reference)


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


async def _prepare(
    session: AsyncSession,
    institution: str | uuid.UUID,
    *,
    body_code: str,
    link_type: str,
    exam_code: str | None,
    reference: str | None,
    status: str,
    valid_from: date | None,
    valid_to: date | None,
    note: str | None,
) -> _Prepared:
    inst = await _institution(session, institution)
    body = await _body(session, body_code)
    exam = _clean(exam_code)
    p = _Prepared(
        inst,
        body,
        link_type.strip().lower(),
        exam.upper() if exam else None,
        _clean(reference),
        status.strip().lower(),
        valid_from,
        valid_to,
        _clean(note),
    )
    errors = check_link(
        _spec(body),
        link_type=p.link_type,
        institution_type=inst.institution_type,
        institution_levels=await _levels(session, inst.id),
        institution_ownership=inst.ownership,
        exam_code=p.exam_code,
        reference=p.reference,
        status=p.status,
        valid_from=valid_from,
        valid_to=valid_to,
    )
    if errors:
        raise ValidationFailed("; ".join(errors))
    label = LINK_LABEL[LinkType(p.link_type)]
    if p.reference:
        # The body's number identifies one institution anywhere on the platform.
        taken = (
            await session.execute(
                select(InstitutionBodyLink.institution_id)
                .where(
                    InstitutionBodyLink.body_id == body.id,
                    InstitutionBodyLink.link_type == p.link_type,
                    InstitutionBodyLink.reference == p.reference,
                    (InstitutionBodyLink.exam_code == p.exam_code)
                    if p.exam_code
                    else InstitutionBodyLink.exam_code.is_(None),
                )
                .execution_options(**{PLATFORM_BYPASS: True})
            )
        ).scalar_one_or_none()
        if taken is not None:
            whose = "this institution" if taken == inst.id else "another institution"
            raise Conflict(
                f"{body.code} {label} {p.reference} is already recorded for {whose}",
                next_action="Check the number with the body: it belongs to one institution.",
            )
    if p.status in CURRENT:
        clash = (
            await session.execute(
                select(InstitutionBodyLink.id).where(
                    InstitutionBodyLink.institution_id == inst.id,
                    InstitutionBodyLink.body_id == body.id,
                    InstitutionBodyLink.link_type == p.link_type,
                    (InstitutionBodyLink.exam_code == p.exam_code)
                    if p.exam_code
                    else InstitutionBodyLink.exam_code.is_(None),
                    InstitutionBodyLink.status.in_(CURRENT),
                )
            )
        ).first()
        if clash is not None:
            what = f"{body.code} {p.exam_code} {label}" if p.exam_code else f"{body.code} {label}"
            raise Conflict(
                f"{inst.name} already has a current {what}",
                next_action="End the current one first (for example, when it is renewed).",
            )
    return p


async def _write(session: AsyncSession, p: _Prepared) -> InstitutionBodyLink:
    ctx = current_context()
    row = InstitutionBodyLink(
        tenant_id=p.inst.tenant_id,
        scope_node_id=p.inst.scope_node_id,
        institution_id=p.inst.id,
        body_id=p.body.id,
        link_type=p.link_type,
        exam_code=p.exam_code,
        reference=p.reference,
        status=p.status,
        valid_from=p.valid_from,
        valid_to=p.valid_to,
        note=p.note,
    )
    session.add(row)
    await session.flush()
    publish(
        session,
        EventEnvelope(
            event_type=events.BODY_LINKED,
            tenant_id=p.inst.tenant_id,
            scope_node_id=p.inst.scope_node_id,
            aggregate_type="institution",
            aggregate_id=p.inst.id,
            actor_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
            payload={"body_code": p.body.code, "link_type": p.link_type, "link_id": str(row.id)},
        ),
    )
    await session.flush()
    return row


def _link_view(row: InstitutionBodyLink, inst: Institution, body: EducationBody) -> LinkView:
    exam_name = next((e["name"] for e in body.examinations if e.get("code") == row.exam_code), None)
    return LinkView(
        row.id,
        inst.id,
        inst.institution_code,
        inst.name,
        body.code,
        body.name,
        row.link_type,
        row.exam_code,
        exam_name,
        row.reference,
        row.status,
        row.valid_from,
        row.valid_to,
        row.note,
    )


async def link_institution(
    session: AsyncSession,
    institution: str | uuid.UUID,
    *,
    body_code: str,
    link_type: str,
    exam_code: str | None = None,
    reference: str | None = None,
    status: str = "active",
    valid_from: date | None = None,
    valid_to: date | None = None,
    note: str | None = None,
) -> LinkView:
    p = await _prepare(
        session,
        institution,
        body_code=body_code,
        link_type=link_type,
        exam_code=exam_code,
        reference=reference,
        status=status,
        valid_from=valid_from,
        valid_to=valid_to,
        note=note,
    )
    return _link_view(await _write(session, p), p.inst, p.body)


async def end_link(
    session: AsyncSession,
    link_id: uuid.UUID,
    *,
    status: str = "withdrawn",
    note: str | None = None,
    on: date | None = None,
) -> LinkView:
    """Suspend or withdraw a link (kept as history). Withdrawn is final."""
    if status not in ("suspended", "withdrawn"):
        raise ValidationFailed("a link is ended as 'suspended' or 'withdrawn'")
    row = (
        await session.execute(select(InstitutionBodyLink).where(InstitutionBodyLink.id == link_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("link not found in your scope")
    if row.status == "withdrawn":
        raise Conflict("this link is already withdrawn")
    row.status = status
    if status == "withdrawn":
        row.valid_to = on or today()
    if note:
        row.note = note.strip()[:255]
    inst = await _institution(session, row.institution_id)
    body = (
        await session.execute(select(EducationBody).where(EducationBody.id == row.body_id))
    ).scalar_one()
    ctx = current_context()
    publish(
        session,
        EventEnvelope(
            event_type=events.BODY_LINK_ENDED,
            tenant_id=row.tenant_id,
            scope_node_id=row.scope_node_id,
            aggregate_type="institution",
            aggregate_id=row.institution_id,
            actor_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
            payload={"body_code": body.code, "link_id": str(row.id), "status": status},
        ),
    )
    await session.flush()
    return _link_view(row, inst, body)


async def links(
    session: AsyncSession,
    *,
    institution_id: uuid.UUID | None = None,
    body_code: str | None = None,
    link_type: str | None = None,
    current_only: bool = False,
) -> list[LinkView]:
    """Links in the caller's scope: one institution's, or every institution's with one body
    (e.g. all WAEC centres under ASUBEB)."""
    stmt = (
        select(InstitutionBodyLink, Institution, EducationBody)
        .join(Institution, Institution.id == InstitutionBodyLink.institution_id)
        .join(EducationBody, EducationBody.id == InstitutionBodyLink.body_id)
        .order_by(Institution.name, EducationBody.code, InstitutionBodyLink.id)
    )
    if institution_id is not None:
        stmt = stmt.where(InstitutionBodyLink.institution_id == institution_id)
    if body_code:
        stmt = stmt.where(EducationBody.code == body_code.strip().upper())
    if link_type:
        stmt = stmt.where(InstitutionBodyLink.link_type == link_type)
    out = [_link_view(r, i, b) for r, i, b in (await session.execute(stmt)).all()]
    return [v for v in out if v.current] if current_only else out


# --------------------------------------------------------------------------- bulk


def _date(value: str, column: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationFailed(f"{column} must be a date like 2027-08-31") from None


async def import_links(
    session: AsyncSession, text: str, *, commit: bool
) -> tuple[dict[str, Any], list[tuple[int, str]]]:
    """Check (and with ``commit``, record) a CSV of links, e.g. a body's list of centre numbers.

    Columns: institution (INS code or government code), body, link_type, exam, reference,
    status, valid_from, valid_to, note. Returns a summary and each problem row's reason.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    headers = {(h or "").strip().lower(): h for h in reader.fieldnames or [] if h}
    missing = [c for c in ("institution", "body", "link_type") if c not in headers]
    if missing:
        raise ValidationFailed(
            f"these columns are missing: {', '.join(missing)}",
            next_action="Columns: " + ", ".join(LINK_COLUMNS) + ".",
        )
    problems: list[tuple[int, str]] = []
    ready: list[_Prepared] = []
    seen: dict[tuple[str, str, str | None, str | None], int] = {}
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            raise ValidationFailed(f"the file has more than {MAX_ROWS} rows")
        row = {c: (raw.get(headers[c]) or "") if c in headers else "" for c in LINK_COLUMNS}
        if not any(v.strip() for v in row.values()):
            continue
        try:
            p = await _prepare(
                session,
                row["institution"],
                body_code=row["body"],
                link_type=row["link_type"],
                exam_code=row["exam"],
                reference=row["reference"],
                status=row["status"] or "active",
                valid_from=_date(row["valid_from"], "valid_from"),
                valid_to=_date(row["valid_to"], "valid_to"),
                note=row["note"],
            )
        except (ValidationFailed, Conflict, NotFound) as exc:
            problems.append((n, exc.detail))
            continue
        if p.reference and p.key in seen:
            problems.append((n, f"the same {p.body.code} number also appears on row {seen[p.key]}"))
            continue
        seen[p.key] = n
        ready.append(p)
    summary: dict[str, Any] = {
        "rows": len(ready) + len(problems),
        "valid": len(ready),
        "errors": len(problems),
        "by_body": {},
        "recorded": 0,
    }
    for p in ready:
        summary["by_body"][p.body.code] = summary["by_body"].get(p.body.code, 0) + 1
    if commit:
        for p in ready:
            await _write(session, p)
        summary["recorded"] = len(ready)
    return summary, problems
