"""School register and bulk import HTTP API (CEDA §10.4 bulk pattern)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import Field

from syntegra.governance import api as governance
from syntegra.institutions import api as inst
from syntegra.web.deps import MAX_LIMIT, UowDep, decode_cursor, encode_cursor, remember, replay
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["institutions"])


class SchoolBody(Model):
    name: str = Field(min_length=3, max_length=200)
    lga: str = Field(min_length=2, max_length=64)
    levels: str = Field(min_length=2, max_length=64, examples=["Primary", "JSS; SSS"])
    government_code: str | None = Field(default=None, max_length=40)
    town: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    latitude: float | None = None
    longitude: float | None = None


def _view(v: inst.InstitutionView) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "institution_code": v.institution_code,
        "government_code": v.government_code,
        "name": v.name,
        "lga": v.lga,
        "levels": v.levels,
        "town": v.town,
        "status": v.status,
        "scope_node_id": str(v.scope_node_id),
    }


async def _board(uow: UowDep) -> str:
    return (await governance.get_tenant(uow.session, uow.ctx.tenant_id)).name[:64]


@router.get("/institutions", summary="Schools in your scope")
async def list_schools(
    uow: UowDep,
    lga_scope_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> dict[str, Any]:
    rows = await inst.list_institutions(
        uow.session, lga_scope_id=lga_scope_id, after=decode_cursor(cursor), limit=limit + 1
    )
    page = rows[:limit]
    return {
        "items": [_view(v) for v in page],
        "next_cursor": encode_cursor(page[-1].id) if len(rows) > limit else None,
    }


@router.post("/institutions", status_code=201, summary="Register one school")
async def register_school(body: SchoolBody, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await uow.require("institution:edit")
    raw = {k: "" if v is None else str(v) for k, v in body.model_dump().items()}
    view = await inst.register_institution(uow.session, raw, managing_body=await _board(uow))
    return remember(uow, key, digest, 201, _view(view))


@router.get("/institutions/{inst_id}", summary="One school")
async def get_school(inst_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    return _view(await inst.get_institution(uow.session, inst_id))


@router.get(
    "/bulk/institutions/template",
    response_class=PlainTextResponse,
    summary="CSV template for a school import",
)
async def template() -> PlainTextResponse:
    return PlainTextResponse(
        inst.template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="schools-template.csv"'},
    )


@router.post(
    "/bulk/institutions/preview", status_code=201, summary="Upload a school list (CSV) and check it"
)
async def preview(request: Request, uow: UowDep, source_name: str | None = None) -> JSONResponse:
    text = (await request.body()).decode("utf-8", errors="replace")
    key, digest, replayed = await replay(uow, request, {"csv": text, "source": source_name})
    if replayed:
        return replayed
    await uow.require("institution:edit")
    job_id = await inst.preview_import(uow.session, text, source_name=source_name)
    return remember(uow, key, digest, 201, await _job_json(uow, job_id))


async def _job_json(uow: UowDep, job_id: uuid.UUID, errors_only: bool = False) -> dict[str, Any]:
    job, rows = await inst.get_job(uow.session, job_id)
    shown = [r for r in rows if r.status in ("error", "skipped")] if errors_only else rows
    return {
        "id": str(job.id),
        "status": job.status,
        "source_name": job.source_name,
        "summary": job.summary,
        "rows": [
            {
                "row": r.row_no,
                "status": r.status,
                "messages": r.messages,
                "data": r.data,
                "result_id": str(r.result_id) if r.result_id else None,
            }
            for r in shown[:500]
        ],
        "next_action": (
            "Correct the rows with errors and upload again, or register the valid rows now."
            if job.status == "previewed"
            else "Done. Download the report or start a new import."
        ),
    }


@router.get("/bulk/jobs/{job_id}", summary="An import and its row-by-row results")
async def get_job(job_id: uuid.UUID, uow: UowDep, errors_only: bool = False) -> dict[str, Any]:
    return await _job_json(uow, job_id, errors_only)


@router.post("/bulk/jobs/{job_id}/commit", summary="Register the valid rows of a previewed import")
async def commit(job_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"job": str(job_id)})
    if replayed:
        return replayed
    await uow.require("institution:edit")
    await inst.commit_import(uow.session, job_id, managing_body=await _board(uow))
    return remember(uow, key, digest, 200, await _job_json(uow, job_id, errors_only=True))
