"""Learner registration: admissions, direct entry, class lists, classes, transfers (ADR-029)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import Field

from syntegra.academic import api as acad
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Forbidden, NotFound, ValidationFailed
from syntegra.governance import api as governance
from syntegra.iam import api as iam
from syntegra.learners import api as lrn
from syntegra.platform.crypto import FieldCrypto
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.routers.common import dump, require_any
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["learners"])
public = APIRouter(prefix="/v1/public", tags=["public forms"])

Relationship = Literal[
    "mother",
    "father",
    "guardian",
    "grandparent",
    "sibling",
    "aunt_uncle",
    "foster_carer",
    "sponsor",
    "self",
    "other",
]
Sex = Literal["F", "M"]
Action = Literal["review", "assess", "offer", "waitlist", "reject", "accept", "decline", "withdraw"]


def _crypto(request: Request) -> FieldCrypto:
    crypto: FieldCrypto = request.app.state.field_crypto
    return crypto


async def _guard(
    uow: UowDep, request: Request, payload: dict[str, Any]
) -> tuple[str, bytes, JSONResponse | None]:
    return await replay(uow, request, payload)


# --------------------------------------------------------------------------- applications


class ApplicationBody(Model):
    session_id: uuid.UUID
    year_code: str = Field(min_length=2, max_length=8, examples=["P1"])
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    other_names: str | None = Field(default=None, max_length=150)
    date_of_birth: date
    sex: Sex
    guardian_name: str = Field(min_length=3, max_length=200)
    guardian_phone: str = Field(min_length=7, max_length=20)
    guardian_relationship: Relationship
    guardian_email: str | None = Field(default=None, max_length=254)
    previous_school: str | None = Field(default=None, max_length=200)


class PublicApplicationBody(ApplicationBody):
    captcha_token: str | None = Field(default=None, max_length=4096)


class MoveBody(Model):
    note: str | None = Field(default=None, max_length=500)
    score: float | None = Field(default=None, ge=0, le=100)
    offer_days: int = Field(default=14, ge=1, le=60)


class AdmitBody(Model):
    class_id: uuid.UUID | None = None
    admission_number: str | None = Field(default=None, max_length=30)
    age_override: str | None = Field(default=None, max_length=255)
    person_id: uuid.UUID | None = None
    not_duplicate_of: list[uuid.UUID] = Field(default_factory=list)
    override_reason: str | None = Field(default=None, max_length=255)


@router.post(
    "/institutions/{institution_id}/applications",
    status_code=201,
    summary="Record an admission application",
)
async def apply(
    institution_id: uuid.UUID, body: ApplicationBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"i": str(institution_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    v = await lrn.submit_application(
        uow.session, institution_id, lrn.ApplicationIn(**body.model_dump())
    )
    return remember(uow, key, digest, 201, dump(v))


@router.get(
    "/institutions/{institution_id}/applications",
    summary="Admission applications (status=open for the live ones)",
)
async def list_applications(
    institution_id: uuid.UUID, uow: UowDep, status: str | None = "open"
) -> JSONResponse:
    await require_any(uow, "learner:register", "admission:decide")
    return JSONResponse(
        {"items": dump(await lrn.applications(uow.session, institution_id, status=status))}
    )


@router.get("/applications/{application_id}", summary="One application and its history")
async def get_application(application_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await require_any(uow, "learner:register", "admission:decide")
    return JSONResponse(dump(await lrn.get_application(uow.session, application_id)))


@router.post(
    "/applications/{application_id}/enrolment",
    status_code=201,
    summary="Enrol an accepted applicant",
)
async def enrol_applicant(
    application_id: uuid.UUID, body: AdmitBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"a": str(application_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    data = lrn.AdmitIn(**(body.model_dump() | {"not_duplicate_of": tuple(body.not_duplicate_of)}))
    return remember(
        uow,
        key,
        digest,
        201,
        dump(await lrn.enrol_applicant(uow.session, _crypto(request), application_id, data)),
    )


@router.post(
    "/applications/{application_id}/{action}",
    summary="review, assess, offer, waitlist, reject (heads), accept, decline or withdraw",
)
async def move(
    application_id: uuid.UUID, action: Action, body: MoveBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"a": str(application_id), "x": action} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("admission:decide" if action in lrn.DECISIONS else "learner:register")
    v = await lrn.move_application(
        uow.session,
        application_id,
        action,
        note=body.note,
        score=body.score,
        offer_days=body.offer_days,
    )
    return remember(uow, key, digest, 200, dump(v))


@public.post(
    "/{jurisdiction}/institutions/{institution_id}/applications",
    status_code=201,
    summary="Apply for a place online (CAPTCHA required)",
)
async def apply_online(
    jurisdiction: str, institution_id: uuid.UUID, body: PublicApplicationBody, request: Request
) -> JSONResponse:
    ip = request.client.host if request.client else None
    if not await request.app.state.captcha.verify(body.captcha_token, ip):
        raise ValidationFailed(
            "the human check (CAPTCHA) was not completed",
            next_action="Complete the check and submit again.",
        )
    async with request.app.state.sessionmaker() as session, session.begin():
        tenant = await governance.tenant_by_code(session, jurisdiction.upper())
        if tenant is None:
            raise NotFound("this state or country is not on the system")
        with use_context(
            RequestContext(
                tenant.id,
                tenant.root_scope_node_id,
                None,
                system=True,
                purpose="online-admission",
                ip=ip,
            )
        ):
            school = await acad.institution(session, institution_id)
            if school.status != "active":
                raise NotFound("this school is not taking applications")
        with use_context(
            RequestContext(
                tenant.id,
                school.scope_node_id,
                None,
                system=True,
                purpose="online-admission",
                ip=ip,
            )
        ):
            data = body.model_dump(exclude={"captcha_token"})
            v = await lrn.submit_application(
                session, institution_id, lrn.ApplicationIn(**data), channel="online"
            )
    return JSONResponse(
        {
            "reference": v.reference,
            "status": v.status,
            "decision_due_by": v.due_by.isoformat(),
            "next_action": "Keep this reference. The school will call the phone number you gave.",
        },
        status_code=201,
    )


# --------------------------------------------------------------------------- direct entry


class PupilBody(Model):
    given_name: str = Field(min_length=1, max_length=100)
    family_name: str = Field(min_length=1, max_length=100)
    other_names: str | None = Field(default=None, max_length=150)
    date_of_birth: date
    sex: Sex
    guardian_name: str = Field(min_length=3, max_length=200)
    guardian_phone: str = Field(min_length=7, max_length=20)
    guardian_relationship: Relationship
    guardian_email: str | None = Field(default=None, max_length=254)
    person_id: uuid.UUID | None = None
    not_duplicate_of: list[uuid.UUID] = Field(default_factory=list)
    override_reason: str | None = Field(default=None, max_length=255)


class DirectBody(Model):
    pupil: PupilBody
    session_id: uuid.UUID
    year_code: str = Field(min_length=2, max_length=8)
    class_id: uuid.UUID | None = None
    admission_number: str | None = Field(default=None, max_length=30)
    starts_on: date | None = None
    age_override: str | None = Field(default=None, max_length=255)
    mode: Literal["full_time", "part_time", "distance"] = "full_time"


@router.post(
    "/institutions/{institution_id}/learners",
    status_code=201,
    summary="Enrol a pupil directly (no application)",
)
async def register(
    institution_id: uuid.UUID, body: DirectBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"i": str(institution_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    p = body.pupil.model_dump()
    pupil = lrn.PupilIn(**(p | {"not_duplicate_of": tuple(p["not_duplicate_of"])}))
    data = lrn.EnrolIn(**body.model_dump(exclude={"pupil"}))
    e = await lrn.register_direct(uow.session, _crypto(request), institution_id, pupil, data)
    return remember(uow, key, digest, 201, dump(e))


@router.get("/learners", summary="Find enrolled learners by name, LRN or admission number")
async def find(uow: UowDep, q: str, institution_id: uuid.UUID | None = None) -> JSONResponse:
    d = await uow.require("learner:view")
    if d.condition == "own_classes_only":
        raise Forbidden("search the classes you teach instead", next_action="Open My classes.")
    return JSONResponse(
        {"items": dump(await lrn.find_learners(uow.session, q, institution_id=institution_id))}
    )


@router.get("/learners/{learner_id}", summary="Learner 360: identity, guardians, enrolment history")
async def learner(learner_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    d = await uow.require("learner:view")
    v = await lrn.get_learner(uow.session, learner_id)
    if d.condition == "own_classes_only":
        mine = await _my_class_ids(uow)
        if not (v.current and v.current.class_id in mine):
            raise NotFound("learner not found in your classes")
    return JSONResponse(dump(v))


async def _my_class_ids(uow: UowDep) -> set[uuid.UUID]:
    person = await iam.person_of_user(uow.session, uow.principal.user_id)
    staff = await acad.staff_of_person(uow.session, person) if person else []
    return {c.id for c in await acad.classes_taught(uow.session, staff)}


@router.get("/classes/{class_id}/learners", summary="The class list")
async def class_list(class_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    d = await uow.require("learner:view")
    if d.condition == "own_classes_only" and class_id not in await _my_class_ids(uow):
        raise Forbidden("you can see the lists of the classes you teach")
    return JSONResponse({"items": dump(await lrn.class_list(uow.session, class_id))})


class PlaceBody(Model):
    class_id: uuid.UUID


@router.post("/enrolments/{enrolment_id}/class", summary="Place a learner in a class or move arms")
async def place(
    enrolment_id: uuid.UUID, body: PlaceBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"e": str(enrolment_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    return remember(
        uow,
        key,
        digest,
        200,
        dump(await lrn.place_in_class(uow.session, enrolment_id, body.class_id)),
    )


class EndBody(Model):
    status: Literal["withdrawn", "graduated", "completed"]
    reason: str = Field(min_length=3, max_length=255)
    on: date | None = None


@router.post("/enrolments/{enrolment_id}/end", summary="Withdraw a learner, or record completion")
async def end(
    enrolment_id: uuid.UUID, body: EndBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"e": str(enrolment_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    e = await lrn.end_enrolment(
        uow.session, enrolment_id, status=body.status, reason=body.reason, on=body.on
    )
    return remember(uow, key, digest, 200, dump(e))


class SubjectsBody(Model):
    offering_ids: list[uuid.UUID] = Field(min_length=1, max_length=40)


@router.post(
    "/enrolments/{enrolment_id}/subjects",
    summary="Register senior secondary subjects or tertiary courses",
)
async def register_subjects(
    enrolment_id: uuid.UUID, body: SubjectsBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"e": str(enrolment_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    return remember(
        uow,
        key,
        digest,
        200,
        {"items": dump(await lrn.register_subjects(uow.session, enrolment_id, body.offering_ids))},
    )


@router.get(
    "/enrolments/{enrolment_id}/subjects", summary="Subjects or courses a learner has registered"
)
async def subjects(enrolment_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await uow.require("learner:view")
    return JSONResponse({"items": dump(await lrn.registered_subjects(uow.session, enrolment_id))})


class PromoteBody(Model):
    to_class_id: uuid.UUID
    repeat_class_id: uuid.UUID | None = None
    repeaters: list[uuid.UUID] = Field(default_factory=list)


@router.post("/classes/{class_id}/promotion", summary="Promote a class into next session's class")
async def promote(
    class_id: uuid.UUID, body: PromoteBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"c": str(class_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    r = await lrn.promote(
        uow.session,
        class_id,
        body.to_class_id,
        repeat_class_id=body.repeat_class_id,
        repeaters=body.repeaters,
    )
    return remember(uow, key, digest, 200, dump(r))


# --------------------------------------------------------------------------- class-list import


@router.get(
    "/class-lists/template.csv",
    response_class=PlainTextResponse,
    summary="Template for a class list",
)
async def template() -> PlainTextResponse:
    return PlainTextResponse(
        lrn.import_template(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="class-list-template.csv"'},
    )


def _job(job: Any, rows: list[Any]) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "status": job.status,
        "source_name": job.source_name,
        "summary": job.summary,
        "rows": [
            {"row": r.row_no, "status": r.status, "messages": r.messages, "data": r.data}
            for r in rows[:2000]
        ],
    }


@router.post(
    "/institutions/{institution_id}/class-lists",
    status_code=201,
    summary="Upload a class list (CSV body) and check every row",
)
async def preview(
    institution_id: uuid.UUID,
    session_id: uuid.UUID,
    request: Request,
    uow: UowDep,
    source_name: str | None = None,
) -> JSONResponse:
    text = (await request.body()).decode("utf-8", errors="replace")
    key, digest, replayed = await _guard(
        uow, request, {"i": str(institution_id), "s": str(session_id), "csv": text}
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    job_id = await lrn.preview_import(
        uow.session, _crypto(request), institution_id, session_id, text, source_name=source_name
    )
    return remember(uow, key, digest, 201, _job(*await lrn.import_job(uow.session, job_id)))


@router.get("/class-lists/{job_id}", summary="A class-list import and its rows")
async def get_import(job_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await uow.require("learner:register")
    return JSONResponse(_job(*await lrn.import_job(uow.session, job_id)))


@router.post("/class-lists/{job_id}/commit", summary="Enrol the valid rows")
async def commit(job_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"j": str(job_id)})
    if replayed:
        return replayed
    await uow.require("learner:register")
    await lrn.commit_import(uow.session, _crypto(request), job_id)
    return remember(uow, key, digest, 200, _job(*await lrn.import_job(uow.session, job_id)))


# --------------------------------------------------------------------------- transfers


class TransferBody(Model):
    to_institution_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=255)
    effective_on: date | None = None


class DecideTransferBody(Model):
    accept: bool
    class_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=255)


@router.post(
    "/enrolments/{enrolment_id}/transfer",
    status_code=201,
    summary="Ask another school to take a learner",
)
async def transfer(
    enrolment_id: uuid.UUID, body: TransferBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"e": str(enrolment_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    t = await lrn.request_transfer(
        uow.session,
        enrolment_id,
        body.to_institution_id,
        reason=body.reason,
        effective_on=body.effective_on,
    )
    return remember(uow, key, digest, 201, dump(t))


@router.get("/institutions/{institution_id}/transfers", summary="Transfers into or out of a school")
async def list_transfers(
    institution_id: uuid.UUID,
    uow: UowDep,
    direction: Literal["incoming", "outgoing"] = "incoming",
    status: Literal["requested", "accepted", "declined", "cancelled", "all"] = "requested",
) -> JSONResponse:
    await uow.require("learner:register")
    wanted = None if status == "all" else status
    return JSONResponse(
        {
            "items": dump(
                await lrn.transfers(uow.session, institution_id, direction=direction, status=wanted)
            )
        }
    )


class AccountBody(Model):
    email: str = Field(min_length=5, max_length=254)


@router.post(
    "/learners/{learner_id}/guardians/{person_id}/account",
    status_code=201,
    summary="Give a parent or guardian an account (messaging, their children's records)",
)
async def guardian_account(
    learner_id: uuid.UUID, person_id: uuid.UUID, body: AccountBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"l": str(learner_id), "p": str(person_id), "e": body.email}
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    base = str(request.app.state.settings.public_base_url).rstrip("/")
    a = await lrn.give_guardian_account(
        uow.session, learner_id, person_id, body.email, sign_in_url=f"{base}/app/"
    )
    return remember(uow, key, digest, 201, dump(a))


@router.get("/schools", summary="Find a school in the state (to transfer a learner to)")
async def schools(uow: UowDep, q: str) -> JSONResponse:
    await uow.require("learner:register")
    return JSONResponse({"items": dump(await lrn.find_schools(uow.session, q))})


@router.post(
    "/transfers/{transfer_id}/decision", summary="Accept (and place) or decline a transfer"
)
async def decide(
    transfer_id: uuid.UUID, body: DecideTransferBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"t": str(transfer_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("learner:register")
    t = await lrn.decide_transfer(
        uow.session,
        transfer_id,
        accept=body.accept,
        session_id=body.session_id,
        class_id=body.class_id,
        note=body.note,
    )
    return remember(uow, key, digest, 200, dump(t))


@router.post("/transfers/{transfer_id}/cancel", summary="Withdraw a transfer request")
async def cancel(transfer_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"t": str(transfer_id), "a": "cancel"})
    if replayed:
        return replayed
    await uow.require("learner:register")
    return remember(
        uow, key, digest, 200, dump(await lrn.cancel_transfer(uow.session, transfer_id))
    )
