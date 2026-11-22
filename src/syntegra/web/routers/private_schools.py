"""Private provider registration (ADR-014, ADR-015).

* Public, no account: proprietors apply, confirm their email, and check status.
* Regulator: a review queue that moves applications through the §22 workflow.

The public endpoints accept no Idempotency-Key (no caller identity to scope it to); duplicate
applications are refused by school name and LGA instead. Submitting and re-sending need a passed
CAPTCHA, and review starts only once the proprietor's email is verified (ADR-015). WAF rate limits
still sit in front in production.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.institutions import api as register
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
    # The human-check token from the CAPTCHA widget on the form (ADR-015)
    captcha_token: str | None = Field(default=None, max_length=4096)


class VerifyEmail(Model):
    code: str = Field(min_length=10, max_length=128)


class Resend(Model):
    email: str = Field(min_length=5, max_length=254)
    captcha_token: str | None = Field(default=None, max_length=4096)


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


async def _human(request: Request, token: str | None) -> None:
    """The CAPTCHA must pass before any database work."""
    ip = request.client.host if request.client else None
    if not await request.app.state.captcha.verify(token, ip):
        raise ValidationFailed(
            "the human check (CAPTCHA) was not completed",
            next_action="Complete the check on the form and submit again.",
        )


def _base_url(request: Request) -> str:
    return str(request.app.state.settings.public_base_url)


def _view(v: registration.ApplicationView) -> dict[str, Any]:
    return {
        "reference": v.reference,
        "status": v.status,
        "regulator": v.regulator_code,
        "email_verified": v.email_verified,
        "email_delivery": v.email_delivery,
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


@public.get(
    "/{jurisdiction}/private-school-applications/form", summary="What the application form needs"
)
async def form_config(jurisdiction: str, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    async with request.app.state.sessionmaker() as session:
        with use_context(await _public_context(request, session, jurisdiction)):
            rules = await register.address_rules(session)
    return {
        "captcha_provider": settings.captcha_provider,
        "captcha_site_key": settings.captcha_site_key,
        "required_address_fields": sorted(rules.required),
        "postcode": {"label": rules.postcode_label, "example": rules.postcode_example},
    }


@public.post(
    "/{jurisdiction}/private-school-applications",
    status_code=201,
    summary="Apply to register a private school (no account needed)",
)
async def apply(jurisdiction: str, body: ApplicationForm, request: Request) -> JSONResponse:
    await _human(request, body.captcha_token)
    form = {
        k: "" if v is None else str(v)
        for k, v in body.model_dump(exclude={"captcha_token"}).items()
    }
    async with request.app.state.sessionmaker() as session, session.begin():
        with use_context(await _public_context(request, session, jurisdiction)):
            reference = await registration.submit_application(
                session, form, public_base_url=_base_url(request)
            )
    return JSONResponse(
        {
            "reference": reference,
            "status": "awaiting_email_verification",
            "next_action": "We have emailed you a link. Open it within 48 hours to send your "
            "application for review. Keep your reference to check progress.",
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


@public.post(
    "/{jurisdiction}/private-school-applications/{reference}/verify-email",
    summary="Confirm the proprietor's email with the emailed code",
)
async def verify_email(
    jurisdiction: str, reference: str, body: VerifyEmail, request: Request
) -> JSONResponse:
    async with request.app.state.sessionmaker() as session, session.begin():
        with use_context(await _public_context(request, session, jurisdiction)):
            out = await registration.verify_email(session, reference, body.code)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@public.post(
    "/{jurisdiction}/private-school-applications/{reference}/resend-verification",
    status_code=202,
    summary="Send a new verification email",
)
async def resend(jurisdiction: str, reference: str, body: Resend, request: Request) -> JSONResponse:
    await _human(request, body.captcha_token)
    async with request.app.state.sessionmaker() as session, session.begin():
        with use_context(await _public_context(request, session, jurisdiction)):
            await registration.resend_verification(
                session, reference, body.email, public_base_url=_base_url(request)
            )
    return JSONResponse(
        {"sent": True, "next_action": "Check your inbox and spam folder."}, status_code=202
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


@router.get("", summary="Applications in your review queue (verified emails only, by default)")
async def queue(
    uow: UowDep,
    status: Literal["submitted", "under_review", "site_inspection", "approved", "rejected"]
    | None = None,
    include_unverified: bool = False,
) -> dict[str, Any]:
    await uow.require("institution:edit")
    items = await registration.list_applications(
        uow.session, status=status, include_unverified=include_unverified
    )
    return {"items": [_view(v) for v in items]}


@router.get("/{reference}", summary="One application, with its history")
async def get_one(reference: str, uow: UowDep) -> dict[str, Any]:
    await uow.require("institution:edit")
    return _view(await registration.get_application(uow.session, reference))


@router.post("/{reference}/transition", summary="Move an application on (reject needs a reason)")
async def transition(reference: str, body: Transition, uow: UowDep) -> dict[str, Any]:
    await uow.require("institution:edit")
    return _view(await registration.advance(uow.session, reference, body.to, body.note))
