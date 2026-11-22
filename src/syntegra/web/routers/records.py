"""Document and records management (CEDA §16.3, ADR-027).

Files are sent as multipart/form-data: a ``file`` part and a ``metadata`` part holding JSON.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from datetime import date
from typing import Any, Literal

import orjson
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import Field, ValidationError

from syntegra.core.errors import Forbidden
from syntegra.core.errors import ValidationFailed as DomainValidation
from syntegra.platform.blobstore import BlobStore
from syntegra.records import api as rec
from syntegra.records.domain import DOC_TYPES, PLAN_NAMES, model_plan
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["records"])

Classification = Literal["public", "internal", "confidential", "restricted", "special"]
DocType = Literal[DOC_TYPES]  # type: ignore[valid-type]
Plan = Literal[PLAN_NAMES]  # type: ignore[valid-type]


def _store(request: Request) -> BlobStore:
    store: BlobStore = request.app.state.document_store
    return store


def _max_bytes(request: Request) -> int:
    return int(request.app.state.settings.document_max_mb) * 1024 * 1024


async def _any(uow: UowDep, *permissions: str) -> None:
    """Allowed if the caller holds any one of the permissions at the active scope."""
    for p in permissions:
        try:
            await uow.require(p)
            return
        except Forbidden:
            continue
    raise Forbidden(
        f"you need one of {', '.join(permissions)} here",
        next_action="Ask your administrator for a records role at this scope.",
    )


def _dump(obj: Any) -> Any:
    """Dataclass views to JSON-ready dicts (uuids, dates as strings)."""
    return orjson.loads(orjson.dumps(obj, default=_default))


def _default(o: Any) -> Any:
    if hasattr(o, "__dataclass_fields__"):
        return {k: getattr(o, k) for k in o.__dataclass_fields__}
    if isinstance(o, tuple):
        return list(o)
    raise TypeError


async def _guarded(
    uow: UowDep, request: Request, payload: dict[str, Any]
) -> tuple[str, bytes, JSONResponse | None]:
    return await replay(uow, request, payload)


# --------------------------------------------------------------------------- file plan


@router.get("/model-file-plans/{plan}", summary="A model file plan (primary, secondary, tertiary)")
async def model_file_plan(plan: Plan, uow: UowDep) -> JSONResponse:
    title, series = model_plan(plan)
    return JSONResponse(
        {
            "plan": plan,
            "title": title,
            "series": [
                {
                    "function_code": s.function_code,
                    "function_name": s.function_name,
                    "code": s.code,
                    "title": s.title,
                    "activity": s.activity,
                    "numbering": s.numbering.value,
                    "classification": s.classification.value,
                    "paper_retain_years": s.paper_retain_years,
                    "paper_rule": s.paper_rule.value,
                    "vital": s.vital,
                }
                for s in series
            ],
        }
    )


@router.get("/institutions/{institution_id}/file-plan", summary="An institution's file plan")
async def get_file_plan(institution_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await _any(uow, "file:manage", "records:admin", "records:oversight")
    items = await rec.file_plan(uow.session, institution_id)
    suggested = await rec.suggested_plan(uow.session, institution_id)
    return JSONResponse({"suggested_plan": suggested, "items": _dump(items)})


class AdoptIn(Model):
    plan: Plan | None = None  # default: the plan for the institution's levels


@router.post(
    "/institutions/{institution_id}/file-plan/adopt",
    summary="Adopt a model file plan (adds the series the institution does not yet have)",
)
async def adopt(
    institution_id: uuid.UUID, body: AdoptIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"i": str(institution_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("records:admin")
    items = await rec.adopt_plan(uow.session, institution_id, body.plan)
    return remember(uow, key, digest, 200, {"items": _dump(items)})


class SeriesBody(Model):
    function_code: str = Field(pattern=r"^[A-Za-z]{2,5}$")
    function_name: str = Field(min_length=2, max_length=100)
    code: str = Field(min_length=4, max_length=20, examples=["ADM/9"])
    title: str = Field(min_length=3, max_length=200)
    numbering: Literal["single", "annual", "self", "alphabetical"]
    classification: Classification
    paper_rule: Literal["destroy", "transfer_archives", "retain"]
    paper_retain_years: int | None = Field(default=None, ge=0, le=100)
    activity: str | None = Field(default=None, max_length=200)
    vital: bool = False


@router.post(
    "/institutions/{institution_id}/file-plan/series",
    status_code=201,
    summary="Add a local series to the file plan",
)
async def add_series(
    institution_id: uuid.UUID, body: SeriesBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"i": str(institution_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("records:admin")
    s = await rec.add_series(uow.session, institution_id, rec.SeriesIn(**body.model_dump()))
    return remember(uow, key, digest, 201, _dump(s))


# --------------------------------------------------------------------------- paper files


class FileBody(Model):
    title: str = Field(min_length=2, max_length=200, examples=["Okeke, Ada Ngozi"])
    home_location: str = Field(min_length=2, max_length=120, examples=["Registry, cabinet 3"])
    person_id: uuid.UUID | None = None
    subject_name: str | None = Field(default=None, max_length=120)
    year: int | None = Field(default=None, ge=1900, le=2100)
    opened_on: date | None = None
    classification: Classification | None = None
    keywords: list[str] = Field(default_factory=list, max_length=30)


@router.post(
    "/records-series/{series_id}/files", status_code=201, summary="Open a paper file in a series"
)
async def open_file(
    series_id: uuid.UUID, body: FileBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"s": str(series_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("file:manage")
    f = await rec.open_file(uow.session, series_id, rec.FileIn(**body.model_dump()))
    return remember(uow, key, digest, 201, _dump(f))


@router.get("/institutions/{institution_id}/files", summary="The file list (register of files)")
async def files(
    institution_id: uuid.UUID,
    uow: UowDep,
    series_id: uuid.UUID | None = None,
    status: Literal["open", "closed", "disposed", "transferred"] | None = None,
    overdue: bool = False,
) -> JSONResponse:
    await _any(uow, "file:manage", "records:admin", "records:oversight")
    items = await rec.file_list(
        uow.session, institution_id, series_id=series_id, status=status, overdue_only=overdue
    )
    return JSONResponse({"items": _dump(items)})


FILE_LIST_COLUMNS = (
    "reference",
    "title",
    "series_code",
    "series_title",
    "classification",
    "volume",
    "opened_on",
    "closed_on",
    "status",
    "home_location",
    "holder_name",
    "due_back",
    "digitised",
    "paper_rule",
    "paper_due",
)


@router.get(
    "/institutions/{institution_id}/files.csv",
    summary="The file list as CSV, for printing or the State Archives",
    response_class=Response,
)
async def files_csv(institution_id: uuid.UUID, uow: UowDep) -> Response:
    await _any(uow, "file:manage", "records:admin", "records:oversight")
    items = await rec.file_list(uow.session, institution_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(FILE_LIST_COLUMNS)
    for f in items:
        row = _dump(f)
        w.writerow(["" if row[c] is None else row[c] for c in FILE_LIST_COLUMNS])
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="file-list.csv"'},
    )


@router.get("/files/{file_id}", summary="A paper file and where it is")
async def get_file(file_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await _any(uow, "file:manage", "records:admin", "records:oversight")
    return JSONResponse(_dump(await rec.get_file(uow.session, file_id)))


class MoveBody(Model):
    kind: Literal["issue", "pass", "external_out", "return"]
    to_user_id: uuid.UUID | None = None
    to_name: str | None = Field(default=None, max_length=120)
    due_back: date | None = None
    note: str | None = Field(default=None, max_length=255)


@router.post("/files/{file_id}/movements", summary="Issue, pass on, send out or return a file")
async def move(file_id: uuid.UUID, body: MoveBody, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"f": str(file_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("file:manage")
    data = body.model_dump()
    f = await rec.move_file(uow.session, file_id, data.pop("kind"), **data)
    return remember(uow, key, digest, 200, _dump(f))


@router.get("/files/{file_id}/movements", summary="Where a file has been")
async def movements(file_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await _any(uow, "file:manage", "records:admin", "records:oversight")
    return JSONResponse({"items": _dump(await rec.file_history(uow.session, file_id))})


async def _file_action(
    uow: UowDep, request: Request, file_id: uuid.UUID, action: str, payload: dict[str, Any]
) -> JSONResponse:
    key, digest, replayed = await _guarded(uow, request, {"f": str(file_id), "a": action} | payload)
    if replayed:
        return replayed
    if action in ("close", "volume"):
        await uow.require("file:manage")
        f = (
            await rec.close_file(uow.session, file_id)
            if action == "close"
            else await rec.new_volume(uow.session, file_id)
        )
    elif action == "hold":
        await uow.require("records:admin")
        f = await rec.set_legal_hold(uow.session, file_id, bool(payload["hold"]))
    else:
        await uow.require("document:index")
        f = await rec.mark_digitised(uow.session, file_id)
    return remember(uow, key, digest, 200, _dump(f))


@router.post("/files/{file_id}/close", summary="Close a file")
async def close(file_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    return await _file_action(uow, request, file_id, "close", {})


@router.post("/files/{file_id}/new-volume", summary="Close a full file and open its next volume")
async def volume(file_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    return await _file_action(uow, request, file_id, "volume", {})


class HoldBody(Model):
    hold: bool


@router.post("/files/{file_id}/legal-hold", summary="Place or lift a legal hold")
async def hold(file_id: uuid.UUID, body: HoldBody, request: Request, uow: UowDep) -> JSONResponse:
    return await _file_action(uow, request, file_id, "hold", body.model_dump())


@router.post(
    "/files/{file_id}/digitised",
    summary="Record that the whole file is scanned and every document verified",
)
async def digitised(file_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    return await _file_action(uow, request, file_id, "digitised", {})


# --------------------------------------------------------------------------- capture


class BatchBody(Model):
    description: str = Field(min_length=3, max_length=255)
    operator_kind: Literal["in_house", "contractor"]
    operator_user_id: uuid.UUID
    contractor_name: str | None = Field(default=None, max_length=200)
    contract_reference: str | None = Field(default=None, max_length=60)


@router.post(
    "/institutions/{institution_id}/batches",
    status_code=201,
    summary="Open a digitisation batch and assign its operator",
)
async def open_batch(
    institution_id: uuid.UUID, body: BatchBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"i": str(institution_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("records:admin")
    b = await rec.open_batch(uow.session, institution_id, rec.BatchIn(**body.model_dump()))
    return remember(uow, key, digest, 201, _dump(b))


@router.get("/institutions/{institution_id}/batches", summary="Digitisation batches")
async def list_batches(
    institution_id: uuid.UUID,
    uow: UowDep,
    status: Literal["open", "captured", "accepted", "rejected"] | None = None,
) -> JSONResponse:
    await _any(uow, "records:admin", "document:index", "records:oversight")
    return JSONResponse({"items": _dump(await rec.batches(uow.session, institution_id, status))})


@router.get("/my-batches", summary="Batches assigned to me (capture operators)")
async def my_batches(uow: UowDep) -> JSONResponse:
    await uow.require("document:capture")
    return JSONResponse({"items": _dump(await rec.my_batches(uow.session))})


class CaptureMeta(Model):
    title: str = Field(min_length=2, max_length=255)
    file_id: uuid.UUID | None = None
    folio: int | None = Field(default=None, ge=1)
    series_id: uuid.UUID | None = None
    doc_type: DocType = "other"
    classification: Classification | None = None
    document_date: date | None = None
    ocr_text: str | None = Field(default=None, max_length=1_000_000)
    page_count: int | None = Field(default=None, ge=1, le=10_000)
    proposed: dict[str, Any] | None = None
    source: Literal["scanned", "born_digital", "email", "upload"] = "scanned"


class VersionMeta(Model):
    note: str = Field(min_length=3, max_length=255)
    ocr_text: str | None = Field(default=None, max_length=1_000_000)
    page_count: int | None = Field(default=None, ge=1, le=10_000)


def _meta[T: Model](kind: type[T], raw: str) -> T:
    try:
        return kind.model_validate_json(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise DomainValidation(f"metadata {where}: {first['msg']}") from exc


async def _read(request: Request, upload: UploadFile) -> tuple[bytes, str]:
    data = await upload.read(_max_bytes(request) + 1)
    return data, (upload.content_type or "application/octet-stream").split(";")[0].strip()


async def _capture(
    request: Request,
    uow: UowDep,
    target: tuple[str, uuid.UUID],
    upload: UploadFile,
    metadata: str,
) -> JSONResponse:
    meta = _meta(CaptureMeta, metadata)
    content, mime = await _read(request, upload)
    key, digest, replayed = await _guarded(
        uow,
        request,
        {"t": target[0], "id": str(target[1]), "sha": hashlib.sha256(content).hexdigest()}
        | meta.model_dump(mode="json"),
    )
    if replayed:
        return replayed
    await uow.require("document:capture")
    m = meta.model_dump()
    file = rec.ContentIn(
        content=content,
        mime=mime,
        original_name=upload.filename,
        ocr_text=m.pop("ocr_text"),
        page_count=m.pop("page_count"),
    )
    data = rec.CaptureIn(file=file, **m)
    if target[0] == "batch":
        d = await rec.capture(
            uow.session, _store(request), target[1], data, max_bytes=_max_bytes(request)
        )
    else:
        d = await rec.upload(
            uow.session, _store(request), target[1], data, max_bytes=_max_bytes(request)
        )
    return remember(uow, key, digest, 201, _dump(d))


@router.post(
    "/batches/{batch_id}/documents",
    status_code=201,
    summary="Capture a scanned document into a batch (its operator only)",
)
async def capture(
    batch_id: uuid.UUID,
    request: Request,
    uow: UowDep,
    file: UploadFile = File(...),  # noqa: B008
    metadata: str = Form(...),
) -> JSONResponse:
    return await _capture(request, uow, ("batch", batch_id), file, metadata)


@router.post(
    "/institutions/{institution_id}/documents",
    status_code=201,
    summary="Register a born-digital document or a single scan",
)
async def upload(
    institution_id: uuid.UUID,
    request: Request,
    uow: UowDep,
    file: UploadFile = File(...),  # noqa: B008
    metadata: str = Form(...),
) -> JSONResponse:
    return await _capture(request, uow, ("institution", institution_id), file, metadata)


@router.post("/batches/{batch_id}/finish", summary="Mark capture of a batch finished")
async def finish(batch_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guarded(uow, request, {"b": str(batch_id), "a": "finish"})
    if replayed:
        return replayed
    await uow.require("document:capture")
    return remember(uow, key, digest, 200, _dump(await rec.finish_batch(uow.session, batch_id)))


class CheckBody(Model):
    sampled: int = Field(ge=1)
    defects: int = Field(ge=0)
    accept: bool
    note: str | None = Field(default=None, max_length=500)


@router.post("/batches/{batch_id}/check", summary="Quality-check a batch (not its operator)")
async def check(
    batch_id: uuid.UUID, body: CheckBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(uow, request, {"b": str(batch_id)} | body.model_dump())
    if replayed:
        return replayed
    await uow.require("document:index")
    b = await rec.check_batch(uow.session, batch_id, **body.model_dump())
    return remember(uow, key, digest, 200, _dump(b))


# --------------------------------------------------------------------------- documents


@router.get("/documents", summary="Search documents (only what you may read is returned)")
async def search(
    uow: UowDep,
    q: str | None = None,
    institution_id: uuid.UUID | None = None,
    doc_type: DocType | None = None,
    series_id: uuid.UUID | None = None,
    file_id: uuid.UUID | None = None,
    status: Literal["captured", "indexed", "verified"] | None = None,
    link_kind: Literal["learner", "staff", "person", "institution", "application"] | None = None,
    link_target: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
) -> JSONResponse:
    items = await rec.search(
        uow.session,
        rec.SearchIn(
            text=q,
            institution_id=institution_id,
            doc_type=doc_type,
            series_id=series_id,
            file_id=file_id,
            status=status,
            link_kind=link_kind,
            link_target=link_target,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        ),
    )
    return JSONResponse({"items": _dump(items)})


def _detail(d: rec.DocumentDetail) -> dict[str, Any]:
    out: dict[str, Any] = _dump(d)
    out["links"] = [{"kind": k, "target_id": str(t)} for k, t in d.links]
    return out


@router.get("/documents/{document_id}", summary="A document's record (the view is logged)")
async def get_document(
    document_id: uuid.UUID, uow: UowDep, purpose: str | None = None
) -> JSONResponse:
    return JSONResponse(_detail(await rec.get_document(uow.session, document_id, purpose=purpose)))


@router.get(
    "/documents/{document_id}/content",
    summary="Download a document's file (the download is logged)",
    response_class=Response,
)
async def content(
    document_id: uuid.UUID,
    request: Request,
    uow: UowDep,
    version: int | None = None,
    purpose: str | None = None,
) -> Response:
    d = await rec.download(
        uow.session, _store(request), document_id, version=version, purpose=purpose
    )
    return Response(
        d.content,
        media_type=d.mime,
        headers={
            "Content-Disposition": f'attachment; filename="{d.filename}"',
            "Cache-Control": "no-store",
            "X-Content-SHA256": d.sha256,
        },
    )


class IndexBody(Model):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    doc_type: DocType | None = None
    document_date: date | None = None
    keywords: list[str] | None = Field(default=None, max_length=30)
    links: list[dict[str, uuid.UUID | str]] | None = None
    file_id: uuid.UUID | None = None
    folio: int | None = Field(default=None, ge=1)
    accept_proposed: bool = False


@router.post("/documents/{document_id}/index", summary="Confirm or correct a document's metadata")
async def index(
    document_id: uuid.UUID, body: IndexBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"d": str(document_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("document:index")
    data = body.model_dump()
    links = []
    for link in data.pop("links") or []:
        try:
            links.append((str(link["kind"]), uuid.UUID(str(link["target_id"]))))
        except (KeyError, ValueError) as exc:
            raise DomainValidation("each link is {kind, target_id}") from exc
    d = await rec.index(uow.session, document_id, rec.IndexIn(links=links, **data))
    return remember(uow, key, digest, 200, _detail(d))


@router.post("/documents/{document_id}/verify", summary="Verify a document (second person)")
async def verify(document_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guarded(uow, request, {"d": str(document_id), "a": "verify"})
    if replayed:
        return replayed
    await uow.require("document:index")
    return remember(uow, key, digest, 200, _dump(await rec.verify(uow.session, document_id)))


@router.post(
    "/documents/{document_id}/versions",
    status_code=201,
    summary="Add a new version (earlier versions are kept)",
)
async def add_version(
    document_id: uuid.UUID,
    request: Request,
    uow: UowDep,
    file: UploadFile = File(...),  # noqa: B008
    metadata: str = Form(...),
) -> JSONResponse:
    meta = _meta(VersionMeta, metadata)
    data, mime = await _read(request, file)
    key, digest, replayed = await _guarded(
        uow,
        request,
        {"d": str(document_id), "sha": hashlib.sha256(data).hexdigest()}
        | meta.model_dump(mode="json"),
    )
    if replayed:
        return replayed
    await uow.require("document:capture")
    d = await rec.add_version(
        uow.session,
        _store(request),
        document_id,
        rec.ContentIn(data, mime, file.filename, meta.ocr_text, meta.page_count),
        note=meta.note,
        max_bytes=_max_bytes(request),
    )
    return remember(uow, key, digest, 201, _detail(d))


class ClassifyBody(Model):
    classification: Classification
    reason: str = Field(min_length=3, max_length=255)


@router.post(
    "/documents/{document_id}/classification",
    summary="Raise a classification (records admin) or lower it (State oversight)",
)
async def reclassify(
    document_id: uuid.UUID, body: ClassifyBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"d": str(document_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await _any(uow, "records:admin", "records:oversight")
    d = await rec.reclassify(uow.session, document_id, body.classification, reason=body.reason)
    return remember(uow, key, digest, 200, _dump(d))


@router.get("/documents/{document_id}/access-log", summary="Who opened a document, and why")
async def access_log(document_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await _any(uow, "records:oversight", "audit:view")
    return JSONResponse({"items": _dump(await rec.access_log(uow.session, document_id))})


# --------------------------------------------------------------------------- disposition


@router.get(
    "/institutions/{institution_id}/disposition-due",
    summary="Closed paper files whose retention has run out",
)
async def disposition_due(institution_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await _any(uow, "records:admin", "records:oversight", "disposal:approve")
    items = await rec.due_for_disposition(uow.session, institution_id)
    return JSONResponse({"items": _dump(items)})


class DispositionBody(Model):
    action: Literal["destroy", "transfer_archives", "retain"]
    reason: str = Field(min_length=3, max_length=255)


@router.post(
    "/files/{file_id}/disposition",
    status_code=201,
    summary="Propose what happens to a closed paper original (the digital record is kept)",
)
async def request_disposition(
    file_id: uuid.UUID, body: DispositionBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(uow, request, {"f": str(file_id)} | body.model_dump())
    if replayed:
        return replayed
    await uow.require("records:admin")
    p = await rec.request_disposition(uow.session, file_id, body.action, reason=body.reason)
    return remember(uow, key, digest, 201, _dump(p))


class DecisionBody(Model):
    approve: bool
    note: str | None = Field(default=None, max_length=255)
    certificate_reference: str | None = Field(default=None, max_length=60)


@router.post(
    "/dispositions/{disposition_id}/decision", summary="Approve or refuse (a second person)"
)
async def decide(
    disposition_id: uuid.UUID, body: DecisionBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guarded(
        uow, request, {"p": str(disposition_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("disposal:approve")
    p = await rec.decide_disposition(uow.session, disposition_id, **body.model_dump())
    return remember(uow, key, digest, 200, _dump(p))


@router.get("/dispositions", summary="Paper disposition requests")
async def list_dispositions(
    uow: UowDep, status: Literal["pending", "approved", "rejected"] | None = "pending"
) -> JSONResponse:
    await _any(uow, "disposal:approve", "records:admin", "records:oversight")
    return JSONResponse({"items": _dump(await rec.dispositions(uow.session, status))})


# --------------------------------------------------------------------------- oversight


@router.get(
    "/records/repository",
    summary="Repository overview for every institution in scope (State Government, Archives)",
)
async def repository(uow: UowDep) -> JSONResponse:
    await _any(uow, "records:oversight", "records:admin")
    return JSONResponse({"items": _dump(await rec.repository_summary(uow.session))})
