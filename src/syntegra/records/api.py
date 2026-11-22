"""Document and records management services (CEDA §16.3, ADR-027).

* **File plan.** An institution adopts the model plan for its level (primary, secondary,
  tertiary) and may add local series. Each series fixes the numbering scheme, the default
  classification and what happens to the paper original once the file closes.
* **Paper files.** Opened under a series, numbered by its scheme, with a cover (reference, title,
  classification, dates, volume), a home location and a current holder. Every issue, pass and
  return is recorded; overdue files are listed. Files close, continue in a new volume, and their
  paper is dispositioned (four-eyes) only after verified digitisation.
* **Capture.** Scanning is done in-house or by a contractor, in batches. Only the operator
  assigned to a batch captures into it; someone else checks a sample and accepts or rejects it.
* **Documents.** Registered to the institution with a DOC number, SHA-256 and every version kept.
  Indexed (metadata can be proposed by OCR/AI and confirmed by a person) and verified by a second
  person. Digital records are permanent: nothing here deletes one.
* **Access.** Search and retrieval are trimmed by classification permission at the caller's
  scope; Restricted and Special records need a stated purpose; every view and download is logged.
  The State Government and the State Archives hold the oversight roles at the state root.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import document_number
from syntegra.governance import api as governance
from syntegra.iam import api as iam
from syntegra.institutions.models import Institution, InstitutionLevel
from syntegra.platform.blobstore import BlobStore
from syntegra.platform.outbox import publish
from syntegra.platform.sequence import next_value
from syntegra.records import events
from syntegra.records.domain import (
    DOC_TYPES,
    LINK_KINDS,
    NEEDS_PURPOSE,
    RANK,
    SOURCES,
    VIEW_PERMISSION,
    Classification,
    Numbering,
    PaperRule,
    SeriesDef,
    check_content,
    check_series,
    file_number,
    initial,
    model_plan,
    paper_due,
    plan_for_levels,
    search_terms,
)
from syntegra.records.models import (
    DigitisationBatch,
    Document,
    DocumentAccess,
    DocumentLink,
    DocumentVersion,
    FileMovement,
    PaperDisposition,
    PaperFile,
    RecordsSeries,
)

MAX_SEARCH = 200

# --------------------------------------------------------------------------- access


async def _allowed(session: AsyncSession, permission: str) -> bool:
    ctx = current_context()
    if ctx.user_id is None:  # system jobs
        return True
    d = await iam.check(
        session, user_id=ctx.user_id, permission=permission, target_scope_id=ctx.scope_node_id
    )
    return d.allowed


async def _require(session: AsyncSession, permission: str) -> None:
    if not await _allowed(session, permission):
        raise Forbidden(
            f"you do not have {permission} here",
            next_action="Ask your administrator for a records role at this scope.",
        )


async def readable(session: AsyncSession) -> set[Classification]:
    """The classifications the caller may read at the active scope."""
    out = {Classification.PUBLIC}
    for level, perm in VIEW_PERMISSION.items():
        if perm and await _allowed(session, perm):
            out.add(level)
    return out


def _publish(row: Any, aggregate: str, event: str, payload: dict[str, Any]) -> EventEnvelope:
    ctx = current_context()
    return EventEnvelope(
        event_type=event,
        tenant_id=row.tenant_id,
        scope_node_id=row.scope_node_id,
        aggregate_type=aggregate,
        aggregate_id=row.id,
        actor_id=ctx.user_id,
        correlation_id=ctx.correlation_id,
        payload=payload,
    )


def _emit(session: AsyncSession, row: Any, aggregate: str, event: str, **payload: Any) -> None:
    publish(session, _publish(row, aggregate, event, payload))


def _actor() -> uuid.UUID | None:
    return current_context().user_id


async def _institution(session: AsyncSession, institution_id: uuid.UUID) -> Institution:
    ctx = current_context()
    inst = (
        await session.execute(
            select(Institution).where(
                Institution.id == institution_id, Institution.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if inst is None:
        raise NotFound("institution not found")
    return inst


def _clean(text: str | None, limit: int, what: str) -> str | None:
    value = (text or "").strip()
    if len(value) > limit:
        raise ValidationFailed(f"{what} is longer than {limit} characters")
    return value or None


def _keywords(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for v in values or []:
        k = " ".join(v.split()).lower()[:60]
        if k and k not in out:
            out.append(k)
    return out[:30]


# --------------------------------------------------------------------------- file plan


@dataclass(frozen=True, slots=True)
class SeriesView:
    id: uuid.UUID
    institution_id: uuid.UUID
    plan: str
    function_code: str
    function_name: str
    code: str
    title: str
    activity: str | None
    numbering: str
    classification: str
    paper_retain_years: int | None
    paper_rule: str
    vital: bool
    active: bool


def _series_view(s: RecordsSeries) -> SeriesView:
    return SeriesView(
        s.id,
        s.institution_id,
        s.plan,
        s.function_code,
        s.function_name,
        s.code,
        s.title,
        s.activity,
        s.numbering,
        s.classification,
        s.paper_retain_years,
        s.paper_rule,
        s.vital,
        s.active,
    )


def _new_series(inst: Institution, plan: str, d: SeriesDef) -> RecordsSeries:
    return RecordsSeries(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        institution_id=inst.id,
        plan=plan,
        function_code=d.function_code,
        function_name=d.function_name,
        code=d.code,
        title=d.title,
        activity=d.activity or None,
        numbering=d.numbering.value,
        classification=d.classification.value,
        paper_retain_years=d.paper_retain_years,
        paper_rule=d.paper_rule.value,
        vital=d.vital,
        active=True,
    )


async def file_plan(session: AsyncSession, institution_id: uuid.UUID) -> list[SeriesView]:
    await _institution(session, institution_id)
    rows = (
        await session.execute(
            select(RecordsSeries)
            .where(RecordsSeries.institution_id == institution_id)
            .order_by(RecordsSeries.code)
        )
    ).scalars()
    return [_series_view(s) for s in rows]


async def suggested_plan(session: AsyncSession, institution_id: uuid.UUID) -> str:
    await _institution(session, institution_id)
    levels = (
        await session.execute(
            select(InstitutionLevel.level).where(InstitutionLevel.institution_id == institution_id)
        )
    ).scalars()
    return plan_for_levels(set(levels))


async def adopt_plan(
    session: AsyncSession, institution_id: uuid.UUID, plan: str | None = None
) -> list[SeriesView]:
    """Adopt a model file plan (the one for the institution's levels unless named). Series the
    institution already has are left as they are, so adopting again only adds what is missing."""
    inst = await _institution(session, institution_id)
    name = plan or await suggested_plan(session, institution_id)
    try:
        _, series = model_plan(name)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    have = {
        c
        for c in (
            await session.execute(
                select(RecordsSeries.code).where(RecordsSeries.institution_id == institution_id)
            )
        ).scalars()
    }
    for d in series:
        if d.code not in have:
            session.add(_new_series(inst, name, d))
    await session.flush()
    return await file_plan(session, institution_id)


@dataclass(frozen=True, slots=True)
class SeriesIn:
    function_code: str
    function_name: str
    code: str
    title: str
    numbering: str
    classification: str
    paper_rule: str
    paper_retain_years: int | None = None
    activity: str | None = None
    vital: bool = False


async def add_series(
    session: AsyncSession, institution_id: uuid.UUID, data: SeriesIn
) -> SeriesView:
    """A local series the model plan does not have."""
    inst = await _institution(session, institution_id)
    try:
        d = SeriesDef(
            function_code=data.function_code.strip().upper(),
            function_name=data.function_name.strip(),
            code=data.code.strip().upper(),
            title=data.title.strip(),
            activity=(data.activity or "").strip(),
            numbering=Numbering(data.numbering),
            classification=Classification(data.classification),
            paper_retain_years=data.paper_retain_years,
            paper_rule=PaperRule(data.paper_rule),
            vital=data.vital,
        )
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    problems = check_series(d)
    if not d.function_code or not d.function_name:
        problems.append("the function code and name are required")
    if problems:
        raise ValidationFailed("; ".join(problems))
    dup = (
        await session.execute(
            select(RecordsSeries.id).where(
                RecordsSeries.institution_id == institution_id, RecordsSeries.code == d.code
            )
        )
    ).first()
    if dup:
        raise Conflict(f"series {d.code} already exists in this file plan")
    s = _new_series(inst, "local", d)
    session.add(s)
    await session.flush()
    return _series_view(s)


async def _series(
    session: AsyncSession, series_id: uuid.UUID, *, lock: bool = False
) -> RecordsSeries:
    q = select(RecordsSeries).where(RecordsSeries.id == series_id)
    if lock:
        q = q.with_for_update()
    s = (await session.execute(q)).scalar_one_or_none()
    if s is None:
        raise NotFound("series not found")
    return s


async def set_series_active(
    session: AsyncSession, series_id: uuid.UUID, active: bool
) -> SeriesView:
    s = await _series(session, series_id)
    s.active = active
    await session.flush()
    return _series_view(s)


# --------------------------------------------------------------------------- paper files


@dataclass(frozen=True, slots=True)
class FileView:
    id: uuid.UUID
    institution_id: uuid.UUID
    institution_name: str
    series_id: uuid.UUID
    series_code: str
    series_title: str
    file_number: str
    reference: str  # what goes on the cover: institution code + file number + volume
    title: str
    person_id: uuid.UUID | None
    classification: str
    volume: int
    previous_file_id: uuid.UUID | None
    opened_on: date
    closed_on: date | None
    status: str
    home_location: str
    holder_user_id: uuid.UUID | None
    holder_name: str | None
    out: bool
    due_back: date | None
    overdue: bool
    digitised: bool
    legal_hold: bool
    keywords: list[str]
    paper_rule: str
    paper_due: date | None


async def _file_view(session: AsyncSession, f: PaperFile) -> FileView:
    s = await _series(session, f.series_id)
    inst = await session.get(Institution, f.institution_id)
    assert inst is not None
    out = f.holder_user_id is not None or f.holder_name is not None
    vol = f" Vol {f.volume}" if f.volume > 1 else ""
    return FileView(
        f.id,
        f.institution_id,
        inst.name,
        s.id,
        s.code,
        s.title,
        f.file_number,
        f"{inst.institution_code} {f.file_number}{vol}",
        f.title,
        f.person_id,
        f.classification,
        f.volume,
        f.previous_file_id,
        f.opened_on,
        f.closed_on,
        f.status,
        f.home_location,
        f.holder_user_id,
        f.holder_name,
        out,
        f.due_back,
        out and f.due_back is not None and f.due_back < today(),
        f.digitised,
        f.legal_hold,
        list(f.keywords or []),
        s.paper_rule,
        paper_due(f.closed_on, s.paper_retain_years) if f.closed_on else None,
    )


@dataclass(frozen=True, slots=True)
class FileIn:
    title: str
    home_location: str
    person_id: uuid.UUID | None = None  # personal (self) files
    subject_name: str | None = None  # alphabetical files: whose initial files it
    year: int | None = None  # annual files; default this year
    opened_on: date | None = None
    classification: str | None = None  # default: the series'; may be raised, never lowered
    keywords: list[str] = field(default_factory=list)


async def _next_seq(session: AsyncSession, s: RecordsSeries, **where: Any) -> int:
    q = select(func.max(PaperFile.seq)).where(PaperFile.series_id == s.id)
    for k, v in where.items():
        q = q.where(getattr(PaperFile, k) == v)
    return int((await session.execute(q)).scalar_one() or 0) + 1


async def open_file(session: AsyncSession, series_id: uuid.UUID, data: FileIn) -> FileView:
    """Open a new paper file under a series and give it its number."""
    s = await _series(session, series_id, lock=True)  # serialises numbering in the series
    if not s.active:
        raise Conflict(f"series {s.code} is no longer in use")
    title = _clean(data.title, 200, "the title")
    home = _clean(data.home_location, 120, "the home location")
    if not title or not home:
        raise ValidationFailed("the file title and its home location (cabinet, shelf) are required")
    level = Classification(s.classification)
    if data.classification:
        try:
            wanted = Classification(data.classification)
        except ValueError as exc:
            raise ValidationFailed("unknown classification") from exc
        if RANK[wanted] < RANK[level]:
            raise ValidationFailed(
                f"files in {s.code} are at least {level.value}; a file may be raised, not lowered"
            )
        level = wanted
    numbering = Numbering(s.numbering)
    year = letter = None
    if numbering is Numbering.SINGLE:
        if await _next_seq(session, s) > 1:
            raise Conflict(
                f"{s.code} is a single running file and already exists",
                next_action="Close it and continue in a new volume.",
            )
        seq = 1
    elif numbering is Numbering.ANNUAL:
        year = data.year or today().year
        seq = await _next_seq(session, s, year=year)
    elif numbering is Numbering.SELF:
        if data.person_id is None:
            raise ValidationFailed(f"{s.code} holds one file per person: say whose file it is")
        mine = (
            await session.execute(
                select(PaperFile.file_number).where(
                    PaperFile.series_id == s.id,
                    PaperFile.person_id == data.person_id,
                    PaperFile.status == "open",
                )
            )
        ).scalar_one_or_none()
        if mine:
            raise Conflict(f"this person already has an open file in {s.code}: {mine}")
        seq = await _next_seq(session, s)
    else:
        try:
            letter = initial(data.subject_name or title)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
        seq = await _next_seq(session, s, letter=letter)
    f = PaperFile(
        tenant_id=s.tenant_id,
        scope_node_id=s.scope_node_id,
        institution_id=s.institution_id,
        series_id=s.id,
        file_number=file_number(s.code, numbering, seq=seq, year=year, letter=letter),
        seq=seq,
        year=year,
        letter=letter,
        title=title,
        person_id=data.person_id,
        classification=level.value,
        volume=1,
        opened_on=data.opened_on or today(),
        status="open",
        home_location=home,
        keywords=_keywords(data.keywords),
    )
    session.add(f)
    await session.flush()
    _emit(session, f, "paper_file", events.FILE_OPENED, series_code=s.code)
    return await _file_view(session, f)


async def _file(session: AsyncSession, file_id: uuid.UUID) -> PaperFile:
    f = (
        await session.execute(select(PaperFile).where(PaperFile.id == file_id))
    ).scalar_one_or_none()
    if f is None:
        raise NotFound("file not found")
    return f


async def _file_readable(session: AsyncSession, f: PaperFile) -> None:
    if Classification(f.classification) not in await readable(session):
        raise NotFound("file not found")


async def get_file(session: AsyncSession, file_id: uuid.UUID) -> FileView:
    f = await _file(session, file_id)
    await _file_readable(session, f)
    return await _file_view(session, f)


def _is_out(f: PaperFile) -> bool:
    return f.holder_user_id is not None or f.holder_name is not None


async def close_file(
    session: AsyncSession, file_id: uuid.UUID, closed_on: date | None = None
) -> FileView:
    f = await _file(session, file_id)
    if f.status != "open":
        raise Conflict(f"this file is {f.status}")
    if _is_out(f):
        raise Conflict("the file is out; it must be back in the registry before it is closed")
    when = closed_on or today()
    if when < f.opened_on or when > today():
        raise ValidationFailed("the closing date must be between the opening date and today")
    f.status, f.closed_on = "closed", when
    await session.flush()
    _emit(session, f, "paper_file", events.FILE_CLOSED)
    return await _file_view(session, f)


async def new_volume(session: AsyncSession, file_id: uuid.UUID) -> FileView:
    """Close a full file and continue it in the next volume under the same number."""
    f = await _file(session, file_id)
    if f.status == "open":
        await close_file(session, file_id)
    elif f.status != "closed":
        raise Conflict(f"this file is {f.status}")
    later = (
        await session.execute(
            select(PaperFile.id).where(
                PaperFile.institution_id == f.institution_id,
                PaperFile.file_number == f.file_number,
                PaperFile.volume > f.volume,
            )
        )
    ).first()
    if later:
        raise Conflict("a later volume of this file already exists")
    n = PaperFile(
        tenant_id=f.tenant_id,
        scope_node_id=f.scope_node_id,
        institution_id=f.institution_id,
        series_id=f.series_id,
        file_number=f.file_number,
        seq=f.seq,
        year=f.year,
        letter=f.letter,
        title=f.title,
        person_id=f.person_id,
        classification=f.classification,
        volume=f.volume + 1,
        previous_file_id=f.id,
        opened_on=today(),
        status="open",
        home_location=f.home_location,
        keywords=list(f.keywords or []),
    )
    session.add(n)
    await session.flush()
    _emit(session, n, "paper_file", events.FILE_OPENED, volume=n.volume)
    return await _file_view(session, n)


async def set_legal_hold(session: AsyncSession, file_id: uuid.UUID, hold: bool) -> FileView:
    f = await _file(session, file_id)
    f.legal_hold = hold
    await session.flush()
    return await _file_view(session, f)


@dataclass(frozen=True, slots=True)
class MovementView:
    id: uuid.UUID
    kind: str
    to_user_id: uuid.UUID | None
    to_name: str | None
    moved_at: Any
    due_back: date | None
    note: str | None
    by: uuid.UUID | None


MOVES = ("issue", "pass", "external_out", "return")


async def move_file(
    session: AsyncSession,
    file_id: uuid.UUID,
    kind: str,
    *,
    to_user_id: uuid.UUID | None = None,
    to_name: str | None = None,
    due_back: date | None = None,
    note: str | None = None,
) -> FileView:
    """Track a file: ``issue`` from the registry, ``pass`` from one holder to the next,
    ``external_out`` to another office, ``return`` to its home location."""
    f = await _file(session, file_id)
    if kind not in MOVES:
        raise ValidationFailed(f"a movement is one of {', '.join(MOVES)}")
    if f.status in ("disposed", "transferred"):
        raise Conflict(f"this file has been {f.status}")
    name = _clean(to_name, 120, "the holder")
    if kind == "return":
        if not _is_out(f):
            raise Conflict("the file is already in its home location")
        to_user_id, name, due_back = None, None, None
    else:
        if (to_user_id is None) == (name is None):
            raise ValidationFailed("say who receives the file: a user or an office, not both")
        if kind == "issue" and _is_out(f):
            raise Conflict("the file is already out; pass it on or return it first")
        if kind == "pass" and not _is_out(f):
            raise Conflict("the file is in the registry; issue it instead")
        if kind == "external_out" and name is None:
            raise ValidationFailed("say which office the file is going to")
        if due_back is not None and due_back < today():
            raise ValidationFailed("the return date is in the past")
        due_back = due_back or (today() + timedelta(days=14 if kind == "external_out" else 7))
    now = utcnow()
    session.add(
        FileMovement(
            tenant_id=f.tenant_id,
            scope_node_id=f.scope_node_id,
            file_id=f.id,
            kind=kind,
            to_user_id=to_user_id,
            to_name=name,
            moved_at=now,
            due_back=due_back,
            note=_clean(note, 255, "the note"),
        )
    )
    f.holder_user_id, f.holder_name, f.due_back = to_user_id, name, due_back
    f.out_since = None if kind == "return" else (f.out_since if kind == "pass" else now)
    await session.flush()
    _emit(session, f, "paper_file", events.FILE_MOVED, kind=kind)
    return await _file_view(session, f)


async def file_history(session: AsyncSession, file_id: uuid.UUID) -> list[MovementView]:
    await get_file(session, file_id)
    rows = (
        await session.execute(
            select(FileMovement)
            .where(FileMovement.file_id == file_id)
            .order_by(FileMovement.moved_at, FileMovement.id)
        )
    ).scalars()
    return [
        MovementView(
            m.id, m.kind, m.to_user_id, m.to_name, m.moved_at, m.due_back, m.note, m.created_by
        )
        for m in rows
    ]


async def file_list(
    session: AsyncSession,
    institution_id: uuid.UUID,
    *,
    series_id: uuid.UUID | None = None,
    status: str | None = None,
    overdue_only: bool = False,
) -> list[FileView]:
    """The file list (register of files) of an institution, in reference order. Files the
    caller may not read are left out."""
    await _institution(session, institution_id)
    levels = {c.value for c in await readable(session)}
    q = select(PaperFile).where(
        PaperFile.institution_id == institution_id, PaperFile.classification.in_(levels)
    )
    if series_id:
        q = q.where(PaperFile.series_id == series_id)
    if status:
        q = q.where(PaperFile.status == status)
    if overdue_only:
        q = q.where(PaperFile.due_back < today())
    rows = (await session.execute(q.order_by(PaperFile.file_number, PaperFile.volume))).scalars()
    return [await _file_view(session, f) for f in rows]


# --------------------------------------------------------------------------- capture


@dataclass(frozen=True, slots=True)
class BatchView:
    id: uuid.UUID
    batch_number: str
    institution_id: uuid.UUID
    operator_kind: str
    contractor_name: str | None
    contract_reference: str | None
    operator_user_id: uuid.UUID
    description: str
    status: str
    documents: int
    pages: int
    qc_by: uuid.UUID | None
    qc_sampled: int | None
    qc_defects: int | None
    qc_note: str | None


async def _batch_view(session: AsyncSession, b: DigitisationBatch) -> BatchView:
    n, pages = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Document.page_count), 0)).where(
                Document.batch_id == b.id
            )
        )
    ).one()
    return BatchView(
        b.id,
        b.batch_number,
        b.institution_id,
        b.operator_kind,
        b.contractor_name,
        b.contract_reference,
        b.operator_user_id,
        b.description,
        b.status,
        int(n),
        int(pages or 0),
        b.qc_by,
        b.qc_sampled,
        b.qc_defects,
        b.qc_note,
    )


@dataclass(frozen=True, slots=True)
class BatchIn:
    description: str
    operator_kind: str  # in_house | contractor
    operator_user_id: uuid.UUID
    contractor_name: str | None = None
    contract_reference: str | None = None


async def open_batch(session: AsyncSession, institution_id: uuid.UUID, data: BatchIn) -> BatchView:
    inst = await _institution(session, institution_id)
    if data.operator_kind not in ("in_house", "contractor"):
        raise ValidationFailed("the operator is in_house or contractor")
    contractor = _clean(data.contractor_name, 200, "the contractor name")
    if data.operator_kind == "contractor" and not contractor:
        raise ValidationFailed("name the contractor doing the scanning")
    description = _clean(data.description, 255, "the description")
    if not description:
        raise ValidationFailed("describe the batch (e.g. 'Admission files 2015-2020, box 3')")
    ctx = current_context()
    seq = await next_value(session, ctx.tenant_id, f"rec.batch.{today().year}")
    b = DigitisationBatch(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        institution_id=inst.id,
        batch_number=f"BAT-{today().year}-{seq:06d}",
        operator_kind=data.operator_kind,
        contractor_name=contractor if data.operator_kind == "contractor" else None,
        contract_reference=_clean(data.contract_reference, 60, "the contract reference"),
        operator_user_id=data.operator_user_id,
        description=description,
        status="open",
    )
    session.add(b)
    await session.flush()
    return await _batch_view(session, b)


async def _batch(session: AsyncSession, batch_id: uuid.UUID) -> DigitisationBatch:
    b = (
        await session.execute(select(DigitisationBatch).where(DigitisationBatch.id == batch_id))
    ).scalar_one_or_none()
    if b is None:
        raise NotFound("batch not found")
    return b


def _operator(b: DigitisationBatch) -> None:
    ctx = current_context()
    if ctx.user_id is not None and ctx.user_id != b.operator_user_id:
        raise Forbidden(
            "only the operator assigned to this batch captures into it",
            next_action="Ask the records officer to assign the batch to you.",
        )


async def my_batches(session: AsyncSession) -> list[BatchView]:
    """Batches assigned to the caller (what a contractor's operator sees)."""
    rows = (
        await session.execute(
            select(DigitisationBatch)
            .where(DigitisationBatch.operator_user_id == _actor())
            .order_by(DigitisationBatch.batch_number)
        )
    ).scalars()
    return [await _batch_view(session, b) for b in rows]


async def batches(
    session: AsyncSession, institution_id: uuid.UUID, status: str | None = None
) -> list[BatchView]:
    await _institution(session, institution_id)
    q = select(DigitisationBatch).where(DigitisationBatch.institution_id == institution_id)
    if status:
        q = q.where(DigitisationBatch.status == status)
    rows = (await session.execute(q.order_by(DigitisationBatch.batch_number))).scalars()
    return [await _batch_view(session, b) for b in rows]


@dataclass(frozen=True, slots=True)
class ContentIn:
    content: bytes
    mime: str
    original_name: str | None = None
    ocr_text: str | None = None  # from the capture station's OCR
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class CaptureIn:
    title: str
    file: ContentIn
    file_id: uuid.UUID | None = None  # the paper file it came from
    folio: int | None = None
    series_id: uuid.UUID | None = None
    doc_type: str = "other"
    classification: str | None = None
    document_date: date | None = None
    proposed: dict[str, Any] | None = None  # metadata suggested by OCR/AI, confirmed at indexing
    source: str = "scanned"


def _check_file(c: ContentIn, max_bytes: int) -> None:
    if not c.content:
        raise ValidationFailed("the file is empty")
    if len(c.content) > max_bytes:
        raise ValidationFailed(f"the file is larger than {max_bytes // (1024 * 1024)} MB")
    problem = check_content(c.mime, c.content[:8])
    if problem:
        raise ValidationFailed(problem)
    if c.page_count is not None and not 1 <= c.page_count <= 10_000:
        raise ValidationFailed("page count must be 1-10000")


async def _store(
    session: AsyncSession,
    store: BlobStore,
    doc: Document,
    version: int,
    c: ContentIn,
    *,
    max_bytes: int,
    note: str | None = None,
) -> DocumentVersion:
    _check_file(c, max_bytes)
    sha = hashlib.sha256(c.content).hexdigest()
    key = f"{doc.tenant_id}/{doc.id}/v{version}-{sha[:16]}"
    await store.put(key, c.content, content_type=c.mime, sha256=sha)
    v = DocumentVersion(
        tenant_id=doc.tenant_id,
        scope_node_id=doc.scope_node_id,
        document_id=doc.id,
        version=version,
        blob_key=key,
        sha256=sha,
        size_bytes=len(c.content),
        mime=c.mime,
        original_name=_clean(c.original_name, 255, "the file name"),
        ocr_text=c.ocr_text or None,
        note=note,
    )
    session.add(v)
    return v


def _reindex(doc: Document, ocr: str | None) -> None:
    doc.search_text = search_terms(
        doc.document_number, doc.title, doc.doc_type, " ".join(doc.keywords or []), ocr
    )


async def _register(
    session: AsyncSession,
    store: BlobStore,
    inst: Institution,
    data: CaptureIn,
    *,
    batch: DigitisationBatch | None,
    max_bytes: int,
) -> Document:
    ctx = current_context()
    _check_file(data.file, max_bytes)  # before anything is registered
    title = _clean(data.title, 255, "the title")
    if not title:
        raise ValidationFailed("give the document a title")
    if data.doc_type not in DOC_TYPES:
        raise ValidationFailed(f"document type must be one of {', '.join(DOC_TYPES)}")
    if data.source not in SOURCES:
        raise ValidationFailed(f"source must be one of {', '.join(SOURCES)}")
    paper = series = None
    if data.file_id:
        paper = await _file(session, data.file_id)
        if paper.institution_id != inst.id:
            raise ValidationFailed("the paper file belongs to another institution")
        series = await _series(session, paper.series_id)
    elif data.series_id:
        series = await _series(session, data.series_id)
        if series.institution_id != inst.id:
            raise ValidationFailed("the series belongs to another institution")
    floor = Classification(
        paper.classification if paper else series.classification if series else "confidential"
    )
    level = floor
    if data.classification:
        try:
            level = Classification(data.classification)
        except ValueError as exc:
            raise ValidationFailed("unknown classification") from exc
        if RANK[level] < RANK[floor]:
            raise ValidationFailed(
                f"documents filed here are at least {floor.value}; raise, never lower"
            )
    if data.folio is not None and data.folio < 1:
        raise ValidationFailed("the folio number starts at 1")
    tenant = await governance.get_tenant(session, ctx.tenant_id)
    year = today().year
    seq = await next_value(session, ctx.tenant_id, f"rec.doc.{year}")
    doc = Document(
        tenant_id=inst.tenant_id,
        scope_node_id=inst.scope_node_id,
        document_number=document_number(tenant.code, year, seq),
        institution_id=inst.id,
        series_id=series.id if series else None,
        file_id=paper.id if paper else None,
        folio=data.folio,
        batch_id=batch.id if batch else None,
        title=title,
        doc_type=data.doc_type,
        classification=level.value,
        document_date=data.document_date,
        source=data.source,
        status="captured",
        keywords=[],
        proposed=data.proposed,
        current_version=1,
        page_count=data.file.page_count,
        captured_by=ctx.user_id,
    )
    session.add(doc)
    await session.flush()
    await _store(session, store, doc, 1, data.file, max_bytes=max_bytes)
    _reindex(doc, data.file.ocr_text)
    await session.flush()
    _emit(session, doc, "document", events.DOCUMENT_CAPTURED, document_number=doc.document_number)
    return doc


async def capture(
    session: AsyncSession,
    store: BlobStore,
    batch_id: uuid.UUID,
    data: CaptureIn,
    *,
    max_bytes: int = 50 * 1024 * 1024,
) -> DocumentView:
    """Capture one scanned document into a batch (the assigned operator only)."""
    b = await _batch(session, batch_id)
    _operator(b)
    if b.status not in ("open", "rejected"):
        raise Conflict(f"batch {b.batch_number} is {b.status}; capture is finished")
    inst = await _institution(session, b.institution_id)
    if data.source != "scanned":
        raise ValidationFailed("batches hold scanned paper; upload born-digital files directly")
    doc = await _register(session, store, inst, data, batch=b, max_bytes=max_bytes)
    return _doc_view(doc)


async def upload(
    session: AsyncSession,
    store: BlobStore,
    institution_id: uuid.UUID,
    data: CaptureIn,
    *,
    max_bytes: int = 50 * 1024 * 1024,
) -> DocumentView:
    """Register a born-digital document (or a single scan) outside a batch."""
    inst = await _institution(session, institution_id)
    doc = await _register(session, store, inst, data, batch=None, max_bytes=max_bytes)
    return _doc_view(doc)


async def finish_batch(session: AsyncSession, batch_id: uuid.UUID) -> BatchView:
    b = await _batch(session, batch_id)
    _operator(b)
    if b.status not in ("open", "rejected"):
        raise Conflict(f"batch {b.batch_number} is {b.status}")
    if not (await _batch_view(session, b)).documents:
        raise Conflict("nothing has been captured into this batch")
    b.status, b.captured_at = "captured", utcnow()
    await session.flush()
    return await _batch_view(session, b)


async def check_batch(
    session: AsyncSession,
    batch_id: uuid.UUID,
    *,
    sampled: int,
    defects: int,
    accept: bool,
    note: str | None = None,
) -> BatchView:
    """Quality check of a captured batch by someone other than its operator: a sample is
    compared with the paper (legibility, completeness, page order, metadata)."""
    b = await _batch(session, batch_id)
    if b.status != "captured":
        raise Conflict(f"batch {b.batch_number} is {b.status}, not waiting for its check")
    ctx = current_context()
    if ctx.user_id is not None and ctx.user_id == b.operator_user_id:
        raise Conflict("the operator cannot check their own batch (four-eyes)")
    total = (await _batch_view(session, b)).documents
    if not 1 <= sampled <= total or not 0 <= defects <= sampled:
        raise ValidationFailed(f"sample 1-{total} documents; defects cannot exceed the sample")
    if not accept and not (note or "").strip():
        raise ValidationFailed("say what must be rescanned")
    b.status = "accepted" if accept else "rejected"
    b.qc_by, b.qc_at, b.qc_sampled, b.qc_defects = ctx.user_id, utcnow(), sampled, defects
    b.qc_note = _clean(note, 500, "the note")
    await session.flush()
    _emit(session, b, "digitisation_batch", events.BATCH_QC, accepted=accept)
    return await _batch_view(session, b)


# --------------------------------------------------------------------------- documents


@dataclass(frozen=True, slots=True)
class DocumentView:
    id: uuid.UUID
    document_number: str
    institution_id: uuid.UUID
    title: str
    doc_type: str
    classification: str
    status: str
    source: str
    document_date: date | None
    series_id: uuid.UUID | None
    file_id: uuid.UUID | None
    folio: int | None
    batch_id: uuid.UUID | None
    keywords: list[str]
    current_version: int
    page_count: int | None


def _doc_view(d: Document) -> DocumentView:
    return DocumentView(
        d.id,
        d.document_number,
        d.institution_id,
        d.title,
        d.doc_type,
        d.classification,
        d.status,
        d.source,
        d.document_date,
        d.series_id,
        d.file_id,
        d.folio,
        d.batch_id,
        list(d.keywords or []),
        d.current_version,
        d.page_count,
    )


@dataclass(frozen=True, slots=True)
class VersionView:
    version: int
    sha256: str
    size_bytes: int
    mime: str
    original_name: str | None
    note: str | None
    created_at: Any
    created_by: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class DocumentDetail:
    document: DocumentView
    institution_name: str
    file_reference: str | None
    versions: list[VersionView]
    links: list[tuple[str, uuid.UUID]]
    proposed: dict[str, Any] | None
    captured_by: uuid.UUID | None
    indexed_by: uuid.UUID | None
    verified_by: uuid.UUID | None
    verified_at: Any


async def _document(session: AsyncSession, document_id: uuid.UUID) -> Document:
    d = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if d is None:
        raise NotFound("document not found")
    return d


async def _open(
    session: AsyncSession,
    document_id: uuid.UUID,
    purpose: str | None,
    action: str,
    version: int | None,
) -> Document:
    """Fetch a document the caller may read, and log the access (with its purpose)."""
    d = await _document(session, document_id)
    level = Classification(d.classification)
    if level not in await readable(session):
        raise NotFound("document not found")  # do not reveal that it exists
    why = _clean(purpose, 255, "the purpose")
    if level in NEEDS_PURPOSE and not why:
        raise ValidationFailed(
            f"{level.value} records are opened only for a stated purpose",
            next_action="Say why you need it (e.g. 'transfer request from the LGEA').",
        )
    session.add(
        DocumentAccess(
            tenant_id=d.tenant_id,
            scope_node_id=d.scope_node_id,
            document_id=d.id,
            user_id=_actor(),
            action=action,
            version=version,
            purpose=why,
            at=utcnow(),
        )
    )
    await session.flush()
    return d


async def _detail(session: AsyncSession, d: Document) -> DocumentDetail:
    inst = await session.get(Institution, d.institution_id)
    assert inst is not None
    ref = None
    if d.file_id:
        ref = (await _file_view(session, await _file(session, d.file_id))).reference
    versions = (
        await session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == d.id)
            .order_by(DocumentVersion.version)
        )
    ).scalars()
    links = (
        await session.execute(
            select(DocumentLink.kind, DocumentLink.target_id).where(
                DocumentLink.document_id == d.id
            )
        )
    ).all()
    return DocumentDetail(
        _doc_view(d),
        inst.name,
        ref,
        [
            VersionView(
                v.version,
                v.sha256,
                v.size_bytes,
                v.mime,
                v.original_name,
                v.note,
                v.created_at,
                v.created_by,
            )
            for v in versions
        ],
        sorted((k, t) for k, t in links),
        d.proposed,
        d.captured_by,
        d.indexed_by,
        d.verified_by,
        d.verified_at,
    )


async def get_document(
    session: AsyncSession, document_id: uuid.UUID, *, purpose: str | None = None
) -> DocumentDetail:
    d = await _open(session, document_id, purpose, "view", None)
    return await _detail(session, d)


@dataclass(frozen=True, slots=True)
class Download:
    content: bytes
    mime: str
    filename: str
    sha256: str


async def download(
    session: AsyncSession,
    store: BlobStore,
    document_id: uuid.UUID,
    *,
    version: int | None = None,
    purpose: str | None = None,
) -> Download:
    """The stored file, checked against its SHA-256 on the way out."""
    d = await _document(session, document_id)
    n = version or d.current_version
    d = await _open(session, document_id, purpose, "download", n)
    v = (
        await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == d.id, DocumentVersion.version == n
            )
        )
    ).scalar_one_or_none()
    if v is None:
        raise NotFound(f"version {n} not found")
    data = await store.get(v.blob_key)
    if hashlib.sha256(data).hexdigest() != v.sha256:
        raise Conflict(
            "the stored file does not match its checksum",
            next_action="Restore this version from backup; the records officer has been told.",
        )
    ext = {"application/pdf": "pdf", "image/tiff": "tif", "image/jpeg": "jpg", "image/png": "png"}
    return Download(data, v.mime, f"{d.document_number}-v{n}.{ext[v.mime]}", v.sha256)


@dataclass(frozen=True, slots=True)
class IndexIn:
    title: str | None = None
    doc_type: str | None = None
    document_date: date | None = None
    keywords: list[str] | None = None
    links: list[tuple[str, uuid.UUID]] | None = None
    file_id: uuid.UUID | None = None
    folio: int | None = None
    accept_proposed: bool = False  # take the suggested title/type/date/keywords as they are


async def index(session: AsyncSession, document_id: uuid.UUID, data: IndexIn) -> DocumentDetail:
    """Confirm or correct a document's metadata. Proposed (OCR/AI) values become metadata only
    when a person accepts them here."""
    d = await _document(session, document_id)
    if Classification(d.classification) not in await readable(session):
        raise NotFound("document not found")
    p = (d.proposed or {}) if data.accept_proposed else {}
    title = data.title or p.get("title")
    doc_type = data.doc_type or p.get("doc_type")
    keywords = data.keywords if data.keywords is not None else p.get("keywords")
    when = data.document_date or (
        date.fromisoformat(p["document_date"]) if p.get("document_date") else None
    )
    if title is not None:
        clean = _clean(str(title), 255, "the title")
        if not clean:
            raise ValidationFailed("the title cannot be blank")
        d.title = clean
    if doc_type is not None:
        if doc_type not in DOC_TYPES:
            raise ValidationFailed(f"document type must be one of {', '.join(DOC_TYPES)}")
        d.doc_type = str(doc_type)
    if when is not None:
        if when > today():
            raise ValidationFailed("the document date is in the future")
        d.document_date = when
    if keywords is not None:
        d.keywords = _keywords([str(k) for k in keywords])
    if data.file_id is not None:
        f = await _file(session, data.file_id)
        if f.institution_id != d.institution_id:
            raise ValidationFailed("the paper file belongs to another institution")
        if RANK[Classification(f.classification)] > RANK[Classification(d.classification)]:
            d.classification = f.classification
        d.file_id, d.series_id = f.id, f.series_id
    if data.folio is not None:
        if data.folio < 1:
            raise ValidationFailed("the folio number starts at 1")
        d.folio = data.folio
    for kind, target in data.links or []:
        if kind not in LINK_KINDS:
            raise ValidationFailed(f"a link is to one of {', '.join(LINK_KINDS)}")
        exists = (
            await session.execute(
                select(DocumentLink.id).where(
                    DocumentLink.document_id == d.id,
                    DocumentLink.kind == kind,
                    DocumentLink.target_id == target,
                )
            )
        ).first()
        if not exists:
            session.add(
                DocumentLink(
                    tenant_id=d.tenant_id,
                    scope_node_id=d.scope_node_id,
                    document_id=d.id,
                    kind=kind,
                    target_id=target,
                )
            )
    if d.status == "verified":
        d.verified_by = d.verified_at = None  # changed metadata is checked again
    d.status, d.indexed_by, d.indexed_at = "indexed", _actor(), utcnow()
    ocr = (
        await session.execute(
            select(DocumentVersion.ocr_text).where(
                DocumentVersion.document_id == d.id, DocumentVersion.version == d.current_version
            )
        )
    ).scalar_one_or_none()
    _reindex(d, ocr)
    await session.flush()
    return await _detail(session, d)


async def verify(session: AsyncSession, document_id: uuid.UUID) -> DocumentView:
    """A second person confirms the image and metadata match the paper (four-eyes)."""
    d = await _document(session, document_id)
    if Classification(d.classification) not in await readable(session):
        raise NotFound("document not found")
    if d.status != "indexed":
        raise Conflict(f"the document is {d.status}; index it before it is verified")
    me = _actor()
    if me is not None and me in (d.indexed_by, d.captured_by):
        raise Conflict("whoever captured or indexed a document cannot verify it (four-eyes)")
    if d.batch_id:
        b = await _batch(session, d.batch_id)
        if b.status != "accepted":
            raise Conflict(f"batch {b.batch_number} has not passed its quality check")
    d.status, d.verified_by, d.verified_at = "verified", me, utcnow()
    await session.flush()
    _emit(session, d, "document", events.DOCUMENT_VERIFIED, document_number=d.document_number)
    return _doc_view(d)


async def add_version(
    session: AsyncSession,
    store: BlobStore,
    document_id: uuid.UUID,
    file: ContentIn,
    *,
    note: str,
    max_bytes: int = 50 * 1024 * 1024,
) -> DocumentDetail:
    """Store a new version (a rescan, a corrected copy). Earlier versions stay as they were;
    the document is checked again."""
    d = await _document(session, document_id)
    b = await _batch(session, d.batch_id) if d.batch_id else None
    if b is not None and b.status in ("open", "rejected"):
        _operator(b)  # a batch still in capture is rescanned by its own operator only
    elif Classification(d.classification) not in await readable(session):
        raise NotFound("document not found")
    why = _clean(note, 255, "the note")
    if not why:
        raise ValidationFailed("say why there is a new version (e.g. 'rescanned: page 2 skewed')")
    _check_file(file, max_bytes)
    n = d.current_version + 1
    await _store(session, store, d, n, file, max_bytes=max_bytes, note=why)
    d.current_version = n
    if file.page_count:
        d.page_count = file.page_count
    if d.status == "verified":
        d.status, d.verified_by, d.verified_at = "indexed", None, None
    _reindex(d, file.ocr_text)
    await session.flush()
    return await _detail(session, d)


async def reclassify(
    session: AsyncSession, document_id: uuid.UUID, classification: str, *, reason: str
) -> DocumentView:
    """Raise a document's classification, or (State oversight only) lower it."""
    d = await _document(session, document_id)
    try:
        to = Classification(classification)
    except ValueError as exc:
        raise ValidationFailed("unknown classification") from exc
    if not _clean(reason, 255, "the reason"):
        raise ValidationFailed("say why the classification changes")
    here = Classification(d.classification)
    if here not in await readable(session):
        raise NotFound("document not found")
    if to == here:
        return _doc_view(d)
    if RANK[to] < RANK[here]:
        await _require(session, "records:oversight")
    if to not in await readable(session) and not await _allowed(session, "records:oversight"):
        raise Forbidden("you cannot classify a document above what you may read")
    d.classification = to.value
    await session.flush()
    _emit(
        session,
        d,
        "document",
        events.DOCUMENT_RECLASSIFIED,
        classification_from=here.value,
        classification_to=to.value,
    )
    return _doc_view(d)


@dataclass(frozen=True, slots=True)
class SearchIn:
    text: str | None = None
    institution_id: uuid.UUID | None = None
    doc_type: str | None = None
    series_id: uuid.UUID | None = None
    file_id: uuid.UUID | None = None
    status: str | None = None
    link_kind: str | None = None
    link_target: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = 50


async def search(session: AsyncSession, q: SearchIn) -> list[DocumentView]:
    """Full-text and metadata search, trimmed to what the caller may read at their scope.
    (Production adds OpenSearch for ranking; the same trimming applies.)"""
    levels = {c.value for c in await readable(session)}
    stmt = select(Document).where(Document.classification.in_(levels))
    for word in search_terms(q.text).split()[:8]:
        stmt = stmt.where(Document.search_text.contains(word, autoescape=True))
    if q.institution_id:
        stmt = stmt.where(Document.institution_id == q.institution_id)
    if q.doc_type:
        stmt = stmt.where(Document.doc_type == q.doc_type)
    if q.series_id:
        stmt = stmt.where(Document.series_id == q.series_id)
    if q.file_id:
        stmt = stmt.where(Document.file_id == q.file_id)
    if q.status:
        stmt = stmt.where(Document.status == q.status)
    if q.date_from:
        stmt = stmt.where(Document.document_date >= q.date_from)
    if q.date_to:
        stmt = stmt.where(Document.document_date <= q.date_to)
    if q.link_kind or q.link_target:
        sub = select(DocumentLink.document_id)
        if q.link_kind:
            sub = sub.where(DocumentLink.kind == q.link_kind)
        if q.link_target:
            sub = sub.where(DocumentLink.target_id == q.link_target)
        stmt = stmt.where(Document.id.in_(sub))
    limit = max(1, min(q.limit, MAX_SEARCH))
    rows = (
        await session.execute(
            stmt.order_by(Document.document_date.desc(), Document.document_number.desc()).limit(
                limit
            )
        )
    ).scalars()
    return [_doc_view(d) for d in rows]


@dataclass(frozen=True, slots=True)
class AccessView:
    user_id: uuid.UUID | None
    action: str
    version: int | None
    purpose: str | None
    at: Any


async def access_log(session: AsyncSession, document_id: uuid.UUID) -> list[AccessView]:
    await _document(session, document_id)
    rows = (
        await session.execute(
            select(DocumentAccess)
            .where(DocumentAccess.document_id == document_id)
            .order_by(DocumentAccess.at, DocumentAccess.id)
        )
    ).scalars()
    return [AccessView(a.user_id, a.action, a.version, a.purpose, a.at) for a in rows]


async def mark_digitised(session: AsyncSession, file_id: uuid.UUID) -> FileView:
    """Record that a whole paper file has been scanned and every document in it verified."""
    f = await _file(session, file_id)
    counts = dict(
        (
            await session.execute(
                select(Document.status, func.count())
                .where(Document.file_id == f.id)
                .group_by(Document.status)
            )
        ).all()
    )
    if not counts:
        raise Conflict("no documents have been captured from this file")
    pending = sum(n for s, n in counts.items() if s != "verified")
    if pending:
        raise Conflict(f"{pending} document(s) from this file are not verified yet")
    f.digitised = True
    await session.flush()
    return await _file_view(session, f)


# --------------------------------------------------------------------------- paper disposition


@dataclass(frozen=True, slots=True)
class DispositionView:
    id: uuid.UUID
    file_id: uuid.UUID
    file_reference: str
    action: str
    reason: str
    status: str
    requested_by: uuid.UUID | None
    decided_by: uuid.UUID | None
    decision_note: str | None
    certificate_reference: str | None


async def _disp_view(session: AsyncSession, p: PaperDisposition) -> DispositionView:
    ref = (await _file_view(session, await _file(session, p.file_id))).reference
    return DispositionView(
        p.id,
        p.file_id,
        ref,
        p.action,
        p.reason,
        p.status,
        p.requested_by,
        p.decided_by,
        p.decision_note,
        p.certificate_reference,
    )


async def due_for_disposition(
    session: AsyncSession, institution_id: uuid.UUID | None = None
) -> list[FileView]:
    """Closed files whose paper retention has run out and which have no decision yet."""
    q = select(PaperFile).where(PaperFile.status == "closed", PaperFile.legal_hold.is_(False))
    if institution_id:
        q = q.where(PaperFile.institution_id == institution_id)
    decided = select(PaperDisposition.file_id).where(
        PaperDisposition.status.in_(("pending", "approved"))
    )
    out = []
    for f in (await session.execute(q.where(PaperFile.id.not_in(decided)))).scalars():
        v = await _file_view(session, f)
        if v.paper_rule != "retain" and v.paper_due is not None and v.paper_due <= today():
            out.append(v)
    return out


async def request_disposition(
    session: AsyncSession, file_id: uuid.UUID, action: str, *, reason: str
) -> DispositionView:
    """Propose what happens to a closed paper original. The digital record is not affected:
    it stays in the repository permanently."""
    f = await _file(session, file_id)
    v = await _file_view(session, f)
    if action not in ("destroy", "transfer_archives", "retain"):
        raise ValidationFailed("the action is destroy, transfer_archives or retain")
    why = _clean(reason, 255, "the reason")
    if not why:
        raise ValidationFailed("give the reason (e.g. the file plan rule)")
    if f.status != "closed":
        raise Conflict("only a closed file in the registry can be dispositioned")
    if f.legal_hold:
        raise Conflict("this file is under a legal hold")
    if _is_out(f):
        raise Conflict("the file is out; it must be back in the registry")
    if action != "retain":
        if v.paper_rule == "retain":
            raise Conflict("the file plan keeps this paper permanently")
        if v.paper_due is None or v.paper_due > today():
            raise Conflict(f"the paper is kept until {v.paper_due}")
    if action == "destroy":
        if v.paper_rule != "destroy":
            raise Conflict("the file plan sends this paper to the State Archives, not destruction")
        if not f.digitised:
            raise Conflict(
                "paper is destroyed only after it has been digitised and verified",
                next_action="Scan the file, verify every document, then mark it digitised.",
            )
    open_req = (
        await session.execute(
            select(PaperDisposition.id).where(
                PaperDisposition.file_id == f.id, PaperDisposition.status == "pending"
            )
        )
    ).first()
    if open_req:
        raise Conflict("a decision on this file is already pending")
    p = PaperDisposition(
        tenant_id=f.tenant_id,
        scope_node_id=f.scope_node_id,
        file_id=f.id,
        action=action,
        reason=why,
        status="pending",
        requested_by=_actor(),
    )
    session.add(p)
    await session.flush()
    return await _disp_view(session, p)


async def decide_disposition(
    session: AsyncSession,
    disposition_id: uuid.UUID,
    *,
    approve: bool,
    note: str | None = None,
    certificate_reference: str | None = None,
) -> DispositionView:
    p = (
        await session.execute(select(PaperDisposition).where(PaperDisposition.id == disposition_id))
    ).scalar_one_or_none()
    if p is None:
        raise NotFound("disposition not found")
    if p.status != "pending":
        raise Conflict(f"this request is already {p.status}")
    me = _actor()
    if me is not None and me == p.requested_by:
        raise Conflict("the person who asked cannot also decide (four-eyes)")
    f = await _file(session, p.file_id)
    if approve and f.legal_hold:
        raise Conflict("this file is under a legal hold")
    cert = _clean(certificate_reference, 60, "the certificate reference")
    if approve and p.action != "retain" and not cert:
        raise ValidationFailed(
            "give the destruction certificate or State Archives transfer reference"
        )
    if not approve and not (note or "").strip():
        raise ValidationFailed("say why the request is refused")
    p.status = "approved" if approve else "rejected"
    p.decided_by, p.decided_at = me, utcnow()
    p.decision_note, p.certificate_reference = _clean(note, 255, "the note"), cert
    if approve and p.action == "destroy":
        f.status = "disposed"
    elif approve and p.action == "transfer_archives":
        f.status = "transferred"
        f.holder_name = None
    await session.flush()
    _emit(
        session,
        p,
        "paper_disposition",
        events.DISPOSITION_DECIDED,
        approved=approve,
        action=p.action,
    )
    return await _disp_view(session, p)


async def dispositions(
    session: AsyncSession, status: str | None = "pending"
) -> list[DispositionView]:
    q = select(PaperDisposition)
    if status:
        q = q.where(PaperDisposition.status == status)
    rows = (await session.execute(q.order_by(PaperDisposition.created_at))).scalars()
    return [await _disp_view(session, p) for p in rows]


# --------------------------------------------------------------------------- oversight


@dataclass(frozen=True, slots=True)
class RepositoryRow:
    institution_id: uuid.UUID
    institution_name: str
    series: int
    files_open: int
    files_closed: int
    files_out: int
    files_overdue: int
    files_digitised: int
    documents: int
    documents_verified: int
    pages: int
    by_classification: dict[str, int]
    batches_waiting_check: int


async def repository_summary(session: AsyncSession) -> list[RepositoryRow]:
    """Counts only (no content) for every institution under the caller's scope: what the State
    Government and the State Archives use to oversee the repository."""
    insts = (await session.execute(select(Institution).order_by(Institution.name))).scalars().all()
    out = []
    for i in insts:
        files = (
            await session.execute(
                select(
                    PaperFile.status,
                    PaperFile.holder_user_id,
                    PaperFile.holder_name,
                    PaperFile.due_back,
                    PaperFile.digitised,
                ).where(PaperFile.institution_id == i.id)
            )
        ).all()
        docs = (
            await session.execute(
                select(Document.status, Document.classification, Document.page_count).where(
                    Document.institution_id == i.id
                )
            )
        ).all()
        n_series = (
            await session.execute(
                select(func.count())
                .select_from(RecordsSeries)
                .where(RecordsSeries.institution_id == i.id)
            )
        ).scalar_one()
        waiting = (
            await session.execute(
                select(func.count())
                .select_from(DigitisationBatch)
                .where(
                    DigitisationBatch.institution_id == i.id,
                    DigitisationBatch.status == "captured",
                )
            )
        ).scalar_one()
        if not (files or docs or n_series):
            continue
        out_now = [f for f in files if f.holder_user_id is not None or f.holder_name is not None]
        out.append(
            RepositoryRow(
                i.id,
                i.name,
                int(n_series),
                sum(1 for f in files if f.status == "open"),
                sum(1 for f in files if f.status == "closed"),
                len(out_now),
                sum(1 for f in out_now if f.due_back is not None and f.due_back < today()),
                sum(1 for f in files if f.digitised),
                len(docs),
                sum(1 for d in docs if d.status == "verified"),
                sum(d.page_count or 0 for d in docs),
                dict(Counter(d.classification for d in docs)),
                int(waiting),
            )
        )
    return out


async def documents_about(
    session: AsyncSession, kind: str, target_id: uuid.UUID
) -> list[DocumentView]:
    """A person's or institution's documents (e.g. on Learner 360 or Staff 360), trimmed."""
    if kind not in LINK_KINDS:
        raise ValidationFailed(f"a link is to one of {', '.join(LINK_KINDS)}")
    return await search(session, SearchIn(link_kind=kind, link_target=target_id, limit=MAX_SEARCH))
