"""Private provider registration (ADR-014).

* Public, no account: proprietors apply and check their application's status.
* Regulator: a review queue that moves applications through the §22 workflow.

The public endpoints accept no Idempotency-Key (no caller identity to scope it to); duplicate
applications are refused by school name and LGA instead. They must sit behind WAF rate limits
and a challenge in production (ADR-014).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import NotFound
from syntegra.governance import api as governance
from syntegra.institutions import registration
from syntegra.web.deps import UowDep
from syntegra.web.schemas import Model

public = APIRouter(prefix="/v1/public", tags=["private schools: public"])
router = APIRouter(prefix="/v1/private-school-applications", tags=["private schools: regulator"])


class ApplicationForm(Model):
    # The school, exactly as it will appear in the register
    name: str = Field(min_length=3, max_length=200, examples=["Bright Future Academy"])
    type: str | None = Field(default=None, max_length=48)
    lga: str = Field(min_length=2, max_length=64, examples=["Aba North"])
    levels: str = Field(min_length=2, max_length=64, examples=["Nursery; Primary"])
    address_line1: str = Field(min_length=3, max_length=255, examples=["14 Faulks Road"])
    address_line2: str | None = Field(default=None, max_length=255)
    town: str = Field(min_length=2, max_length=100, examples=["Aba"])
    postcode: str = Field(min_length=3, max_length=16, examples=["450001"])
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    latitude: float | None = None
    longitude: float | None = None
    # The proprietor
    proprietor_name: str = Field(min_length=3, max_length=200)
    proprietor_email: str = Field(min_length=5, max_length=254)
    proprietor_phone: str = Field(min_length=7, max_length=32)
    cac_number: str | None = Field(default=None, max_length=20, examples=["RC 1234567"])


class Transition(Model):
    to: Literal["under_review", "site_inspection", "approved", "rejected"]
    note: str | None = Field(default=None, max_length=1000)


async def _public_context(request: Request, session: Any, jurisdiction: str) -> RequestContext:
    tenant = await governance.tenant_by_code(session, jurisdiction.upper())
    if tenant is None:
        raise NotFound("this state or country is not on the system")
    return RequestContext(
        tenant.id,
        tenant.root_scope_node_id,
        None,
        system=True,
        purpose="private-school-application",
        ip=request.client.host if request.client else None,
    )


def _view(v: registration.ApplicationView) -> dict[str, Any]:
    return {
        "reference": v.reference,
        "status": v.status,
        "regulator": v.regulator_code,
        "school": {
            "name": v.school_name,
            "type": v.institution_type,
            "lga": v.lga,
            "levels": v.levels,
            "full_address": v.full_address,
        },
        "proprietor": {
            "name": v.proprietor_name,
            "email": v.proprietor_email,
            "phone": v.proprietor_phone,
            "cac_number": v.cac_number,
        },
        "history": v.history,
        "decision_note": v.decision_note,
        "institution_id": str(v.institution_id) if v.institution_id else None,
        "allowed_next": v.allowed_next,
    }


@public.post(
    "/{jurisdiction}/private-school-applications",
    status_code=201,
    summary="Apply to register a private school (no account needed)",
)
async def apply(jurisdiction: str, body: ApplicationForm, request: Request) -> JSONResponse:
    form = {k: "" if v is None else str(v) for k, v in body.model_dump().items()}
    async with request.app.state.sessionmaker() as session, session.begin():
        with use_context(await _public_context(request, session, jurisdiction)):
            reference = await registration.submit_application(session, form)
    return JSONResponse(
        {
            "reference": reference,
            "status": "submitted",
            "next_action": "Keep this reference. Check progress with it and your email.",
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


@public.get(
    "/{jurisdiction}/private-school-applications/{reference}",
    summary="Check an application's status (reference and proprietor email)",
)
async def status(
    jurisdiction: str,
    reference: str,
    request: Request,
    email: Annotated[str, Query(min_length=5, max_length=254)],
) -> JSONResponse:
    async with request.app.state.sessionmaker() as session:
        with use_context(await _public_context(request, session, jurisdiction)):
            out = await registration.application_status(session, reference, email)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.get("", summary="Applications in your review queue")
async def queue(
    uow: UowDep,
    status: Literal["submitted", "under_review", "site_inspection", "approved", "rejected"]
    | None = None,
) -> dict[str, Any]:
    await uow.require("institution:edit")
    return {
        "items": [
            _view(v) for v in await registration.list_applications(uow.session, status=status)
        ]
    }


@router.get("/{reference}", summary="One application, with its history")
async def get_one(reference: str, uow: UowDep) -> dict[str, Any]:
    await uow.require("institution:edit")
    return _view(await registration.get_application(uow.session, reference))


@router.post("/{reference}/transition", summary="Move an application on (reject needs a reason)")
async def transition(reference: str, body: Transition, uow: UowDep) -> dict[str, Any]:
    await uow.require("institution:edit")
    return _view(await registration.advance(uow.session, reference, body.to, body.note))
