"""Education bodies and institutions' links to them (ADR-019)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.institutions import bodies
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["education bodies"])


class LinkIn(Model):
    body: str = Field(min_length=2, max_length=24, examples=["WAEC"])
    link_type: Literal["exam_centre", "admissions_code", "accreditation", "approval", "beneficiary"]
    exam: str | None = Field(default=None, max_length=24, examples=["WASSCE-SC"])
    reference: str | None = Field(default=None, max_length=40, examples=["1234567"])
    status: Literal["active", "provisional", "suspended", "withdrawn"] = "active"
    valid_from: date | None = None
    valid_to: date | None = None
    note: str | None = Field(default=None, max_length=255)


class EndIn(Model):
    status: Literal["suspended", "withdrawn"] = "withdrawn"
    note: str | None = Field(default=None, max_length=255)


def _body(v: bodies.BodyView) -> dict[str, Any]:
    return {
        "code": v.code,
        "name": v.name,
        "scope": v.scope,
        "countries": v.countries,
        "roles": v.roles,
        "covers": v.covers,
        "examinations": v.examinations,
        "links_recorded": v.link_types,
        "note": v.note,
    }


def _link(v: bodies.LinkView) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "institution": {
            "id": str(v.institution_id),
            "code": v.institution_code,
            "name": v.institution_name,
        },
        "body": {"code": v.body_code, "name": v.body_name},
        "link_type": v.link_type,
        "exam": {"code": v.exam_code, "name": v.exam_name} if v.exam_code else None,
        "reference": v.reference,
        "status": v.status,
        "valid_from": v.valid_from.isoformat() if v.valid_from else None,
        "valid_to": v.valid_to.isoformat() if v.valid_to else None,
        "current": v.current,
        "note": v.note,
    }


@router.get("/education-bodies", summary="Your jurisdiction's own bodies and external bodies")
async def list_bodies(
    uow: UowDep, role: str | None = None, country: str | None = None
) -> dict[str, Any]:
    return {
        "internal": await bodies.jurisdiction_bodies(uow.session),
        "external": [
            _body(b) for b in await bodies.list_bodies(uow.session, role=role, country=country)
        ],
    }


@router.get("/education-bodies/{code}", summary="One external body, with its examinations")
async def get_body(code: str, uow: UowDep) -> dict[str, Any]:
    return _body(await bodies.get_body(uow.session, code))


@router.get(
    "/education-bodies/{code}/institutions",
    summary="Institutions in your scope linked to a body (e.g. all WAEC centres)",
)
async def body_institutions(
    code: str, uow: UowDep, link_type: str | None = None, current: bool = True
) -> dict[str, Any]:
    await bodies.get_body(uow.session, code)
    items = await bodies.links(
        uow.session, body_code=code, link_type=link_type, current_only=current
    )
    return {"items": [_link(v) for v in items]}


@router.get(
    "/institutions/{inst_id}/bodies",
    summary="An institution's exam centres, codes and accreditations",
)
async def institution_links(inst_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    return {"items": [_link(v) for v in await bodies.links(uow.session, institution_id=inst_id)]}


@router.post("/institutions/{inst_id}/bodies", status_code=201, summary="Record a link to a body")
async def add_link(inst_id: uuid.UUID, body: LinkIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"inst": str(inst_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("institution:edit")
    view = await bodies.link_institution(
        uow.session,
        inst_id,
        body_code=body.body,
        link_type=body.link_type,
        exam_code=body.exam,
        reference=body.reference,
        status=body.status,
        valid_from=body.valid_from,
        valid_to=body.valid_to,
        note=body.note,
    )
    return remember(uow, key, digest, 201, _link(view))


@router.post("/institution-body-links/{link_id}/end", summary="Suspend or withdraw a link")
async def end_link(link_id: uuid.UUID, body: EndIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"link": str(link_id)} | body.model_dump())
    if replayed:
        return replayed
    await uow.require("institution:edit")
    view = await bodies.end_link(uow.session, link_id, status=body.status, note=body.note)
    return remember(uow, key, digest, 200, _link(view))


@router.post(
    "/bulk/institution-body-links",
    summary="Check (and with commit=true, record) a CSV of links, e.g. a body's centre list",
)
async def bulk_links(request: Request, uow: UowDep, commit: bool = False) -> JSONResponse:
    text = (await request.body()).decode("utf-8", errors="replace")
    key, digest, replayed = await replay(uow, request, {"csv": text, "commit": commit})
    if replayed:
        return replayed
    await uow.require("institution:edit")
    summary, problems = await bodies.import_links(uow.session, text, commit=commit)
    out = {
        "summary": summary,
        "problems": [{"row": n, "message": m} for n, m in problems[:500]],
        "next_action": "Done."
        if commit
        else "Fix the problem rows, or send again with commit=true to record the valid rows.",
    }
    return remember(uow, key, digest, 200, out)
