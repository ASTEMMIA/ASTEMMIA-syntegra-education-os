"""Staff 360, postings and the nominal roll (ADR-024)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import Field

from syntegra.hr import api as hr
from syntegra.hr import appraisal as appr
from syntegra.web.deps import MAX_LIMIT, UowDep, decode_cursor, encode_cursor, remember, replay
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["staff (HRMS)"])

Grade = Annotated[int | None, Field(default=None, ge=1, le=17)]
Step = Annotated[int | None, Field(default=None, ge=1, le=15)]


class AppointIn(Model):
    person_id: uuid.UUID
    category: Literal["teaching", "non_teaching"]
    position: str = Field(min_length=2, max_length=100, examples=["Classroom teacher"])
    start_date: date
    institution_id: uuid.UUID | None = None
    place_scope_id: uuid.UUID | None = None
    grade_level: Grade = None
    step: Step = None
    salary_structure: str | None = Field(default=None, max_length=16, examples=["CONPSS"])
    first_appointment_date: date | None = None
    confirmation_date: date | None = None
    government_file_number: str | None = Field(default=None, max_length=40)
    reference: str | None = Field(default=None, max_length=60)


class QualificationIn(Model):
    kind: str = Field(max_length=16, examples=["nce", "degree", "pgde"])
    title: str = Field(min_length=2, max_length=150, examples=["B.Ed Mathematics"])
    awarding_body: str | None = Field(default=None, max_length=200)
    year: int | None = None


class RegistrationIn(Model):
    number: str = Field(min_length=3, max_length=40)
    status: Literal["registered", "provisional", "expired", "suspended"] = "registered"
    body_code: str = Field(default="TRCN", max_length=24)
    registered_on: date | None = None
    expires_on: date | None = None


class PostingBody(Model):
    appointment_type: Literal[
        "posting", "transfer", "promotion", "acting", "secondment", "redeployment"
    ]
    effective_date: date
    institution_id: uuid.UUID | None = None
    place_scope_id: uuid.UUID | None = None
    position: str | None = Field(default=None, max_length=100)
    grade_level: Grade = None
    step: Step = None
    salary_structure: str | None = Field(default=None, max_length=16)
    reason: str | None = Field(default=None, max_length=255)
    reference: str | None = Field(default=None, max_length=60)


class DecisionIn(Model):
    note: str | None = Field(default=None, max_length=255)


class ExitIn(Model):
    exit_date: date
    reason: Literal[
        "resignation",
        "retirement",
        "death",
        "dismissal",
        "termination",
        "end_of_contract",
        "transfer_out",
    ]


def _post(p: hr.PostView) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "place": {"name": p.place_name, "scope_node_id": str(p.place_scope_id)},
        "institution": {"id": str(p.institution_id), "code": p.institution_code}
        if p.institution_id
        else None,
        "position": p.position,
        "grade_level": p.grade_level,
        "step": p.step,
        "salary_structure": p.salary_structure,
        "primary": p.is_primary,
        "appointment_type": p.appointment_type,
        "start_date": p.start_date.isoformat(),
        "end_date": p.end_date.isoformat() if p.end_date else None,
        "current": p.current,
        "reference": p.reference,
    }


def _staff(v: hr.StaffView, *, full: bool = True) -> dict[str, Any]:
    post = v.current_post
    best = v.highest_qualification
    out: dict[str, Any] = {
        "id": str(v.id),
        "staff_number": v.staff_number,
        "person_id": str(v.person_id),
        "surname": v.surname,
        "first_name": v.first_name,
        "other_names": v.other_names,
        "sex": v.sex,
        "date_of_birth": v.date_of_birth.isoformat() if v.date_of_birth else None,
        "category": v.category,
        "status": v.status,
        "government_file_number": v.government_file_number,
        "first_appointment_date": v.first_appointment_date.isoformat()
        if v.first_appointment_date
        else None,
        "years_of_service": v.years_of_service,
        "retirement_date": v.retirement_date.isoformat() if v.retirement_date else None,
        "current_post": _post(post) if post else None,
        "highest_qualification": best.label if best else None,
        "registered_teacher": v.registered_teacher,
        "qualified_teacher": v.qualified_teacher,
    }
    if full:
        out |= {
            "confirmation_date": v.confirmation_date.isoformat() if v.confirmation_date else None,
            "exit_date": v.exit_date.isoformat() if v.exit_date else None,
            "exit_reason": v.exit_reason,
            "posts": [_post(p) for p in v.posts],
            "qualifications": [
                {
                    "id": str(q.id),
                    "kind": q.kind,
                    "label": q.label,
                    "title": q.title,
                    "awarding_body": q.awarding_body,
                    "year": q.year,
                    "teaching": q.teaching,
                    "verified": q.verified,
                }
                for q in v.qualifications
            ],
            "registrations": [
                {
                    "id": str(r.id),
                    "body": r.body_code,
                    "number": r.number,
                    "status": r.status,
                    "registered_on": r.registered_on.isoformat() if r.registered_on else None,
                    "expires_on": r.expires_on.isoformat() if r.expires_on else None,
                    "current": r.current,
                }
                for r in v.registrations
            ],
        }
    return out


def _posting(p: hr.PostingView) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "staff_id": str(p.staff_id),
        "staff_number": p.staff_number,
        "to_place": p.to_place,
        "position": p.position,
        "grade_level": p.grade_level,
        "step": p.step,
        "appointment_type": p.appointment_type,
        "effective_date": p.effective_date.isoformat(),
        "status": p.status,
        "reason": p.reason,
        "reference": p.reference,
        "decision_note": p.decision_note,
    }


def _filter(
    institution_id: uuid.UUID | None,
    category: str | None,
    status: str | None,
    position: str | None,
    grade_min: int | None,
    grade_max: int | None,
    retiring_within_days: int | None,
    unregistered_teachers: bool,
) -> hr.RollFilter:
    return hr.RollFilter(
        institution_id=institution_id,
        category=category,
        status=status,
        position=position,
        grade_min=grade_min,
        grade_max=grade_max,
        retiring_within_days=retiring_within_days,
        unregistered_teachers=unregistered_teachers,
    )


@router.get("/staff", summary="Nominal roll: staff in your scope")
async def roll(
    uow: UowDep,
    institution_id: uuid.UUID | None = None,
    category: Literal["teaching", "non_teaching"] | None = None,
    status: Literal["active", "suspended", "on_leave", "exited"] | None = "active",
    position: str | None = None,
    grade_min: Annotated[int | None, Query(ge=1, le=17)] = None,
    grade_max: Annotated[int | None, Query(ge=1, le=17)] = None,
    retiring_within_days: Annotated[int | None, Query(ge=0, le=3650)] = None,
    unregistered_teachers: bool = False,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> dict[str, Any]:
    await uow.require("staff:view")
    f = _filter(
        institution_id,
        category,
        status,
        position,
        grade_min,
        grade_max,
        retiring_within_days,
        unregistered_teachers,
    )
    rows = await hr.nominal_roll(uow.session, f, after=decode_cursor(cursor), limit=limit + 1)
    page = rows[:limit]
    return {
        "items": [_staff(v, full=False) for v in page],
        "next_cursor": encode_cursor(page[-1].id) if len(rows) > limit else None,
    }


def _csv_row(v: hr.StaffView) -> list[object]:
    p = v.current_post
    best = v.highest_qualification
    blank = ""
    return [
        v.staff_number,
        v.government_file_number or blank,
        v.surname,
        v.first_name,
        v.other_names or blank,
        v.sex or blank,
        v.date_of_birth or blank,
        v.category,
        p.place_name if p else blank,
        (p.institution_code or blank) if p else blank,
        p.position if p else blank,
        (p.grade_level or blank) if p else blank,
        (p.step or blank) if p else blank,
        v.first_appointment_date or blank,
        blank if v.years_of_service is None else v.years_of_service,
        v.retirement_date or blank,
        best.label if best else blank,
        "yes" if v.registered_teacher else "no",
        "yes" if v.qualified_teacher else "no",
    ]


@router.get(
    "/staff/nominal-roll.csv",
    response_class=PlainTextResponse,
    summary="Nominal roll as CSV (same filters)",
)
async def roll_csv(
    uow: UowDep,
    institution_id: uuid.UUID | None = None,
    category: Literal["teaching", "non_teaching"] | None = None,
    status: Literal["active", "suspended", "on_leave", "exited"] | None = "active",
) -> PlainTextResponse:
    await uow.require("staff:view")
    rows = await hr.nominal_roll(
        uow.session,
        hr.RollFilter(institution_id=institution_id, category=category, status=status),
        limit=100_000,
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "staff_number", "file_number", "surname", "first_name", "other_names", "sex",
            "date_of_birth", "category", "place_of_work", "institution_code", "position",
            "grade_level", "step", "first_appointment", "years_of_service", "retirement_date",
            "highest_qualification", "trcn_registered", "qualified_teacher",
        ]
    )  # fmt: skip
    for v in rows:
        w.writerow(_csv_row(v))
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="nominal-roll.csv"'},
    )


@router.get("/staff/{staff_id}", summary="One staff record, with posts, qualifications and TRCN")
async def get_one(staff_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("staff:view")
    return _staff(await hr.get_staff(uow.session, staff_id))


@router.post("/staff", status_code=201, summary="Appoint: make a person a staff member")
async def appoint(body: AppointIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await uow.require("staff:edit")
    v = await hr.appoint(uow.session, hr.AppointmentIn(**body.model_dump()))
    return remember(uow, key, digest, 201, _staff(v))


@router.post("/staff/{staff_id}/qualifications", status_code=201, summary="Add a qualification")
async def add_qualification(
    staff_id: uuid.UUID, body: QualificationIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"staff": str(staff_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("staff:edit")
    v = await hr.add_qualification(uow.session, staff_id, **body.model_dump())
    return remember(uow, key, digest, 201, _staff(v))


@router.post(
    "/staff-qualifications/{qualification_id}/verify",
    summary="Mark a qualification verified (certificate seen)",
)
async def verify(qualification_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"q": str(qualification_id)})
    if replayed:
        return replayed
    await uow.require("staff:edit")
    v = await hr.verify_qualification(uow.session, qualification_id)
    return remember(uow, key, digest, 200, _staff(v))


@router.post("/staff/{staff_id}/registrations", summary="Record TRCN registration")
async def registration(
    staff_id: uuid.UUID, body: RegistrationIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"staff": str(staff_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("staff:edit")
    v = await hr.set_registration(uow.session, staff_id, **body.model_dump())
    return remember(uow, key, digest, 200, _staff(v))


@router.post(
    "/staff/{staff_id}/postings",
    status_code=201,
    summary="Request a posting, transfer or promotion (a second officer decides)",
)
async def request_posting(
    staff_id: uuid.UUID, body: PostingBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"staff": str(staff_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("staff:post")
    v = await hr.request_posting(uow.session, staff_id, hr.PostingIn(**body.model_dump()))
    return remember(uow, key, digest, 201, _posting(v))


@router.get("/posting-requests", summary="Posting requests in your scope")
async def list_requests(
    uow: UowDep,
    status: Literal["pending", "approved", "rejected", "cancelled"] | None = "pending",
) -> dict[str, Any]:
    await uow.require("staff:view")
    return {"items": [_posting(p) for p in await hr.posting_requests(uow.session, status=status)]}


async def _decide(
    request_id: uuid.UUID, body: DecisionIn, request: Request, uow: UowDep, approve: bool
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"r": str(request_id), "approve": approve} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("staff:post")
    v = await hr.decide_posting(uow.session, request_id, approve=approve, note=body.note)
    return remember(uow, key, digest, 200, _posting(v))


@router.post("/posting-requests/{request_id}/approve", summary="Approve a posting (four-eyes)")
async def approve(
    request_id: uuid.UUID, body: DecisionIn, request: Request, uow: UowDep
) -> JSONResponse:
    return await _decide(request_id, body, request, uow, True)


@router.post("/posting-requests/{request_id}/reject", summary="Reject a posting (give a reason)")
async def reject(
    request_id: uuid.UUID, body: DecisionIn, request: Request, uow: UowDep
) -> JSONResponse:
    return await _decide(request_id, body, request, uow, False)


@router.post("/posting-requests/{request_id}/cancel", summary="Cancel your own posting request")
async def cancel(request_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"cancel": str(request_id)})
    if replayed:
        return replayed
    await uow.require("staff:post")
    v = await hr.cancel_posting(uow.session, request_id)
    return remember(uow, key, digest, 200, _posting(v))


@router.post("/staff/{staff_id}/exit", summary="End employment (resignation, retirement…)")
async def exit_(staff_id: uuid.UUID, body: ExitIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"staff": str(staff_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("staff:edit")
    v = await hr.end_employment(uow.session, staff_id, exit_date=body.exit_date, reason=body.reason)
    return remember(uow, key, digest, 200, _staff(v))


@router.get("/staffing-summary", summary="Staff per place of work: numbers and qualification")
async def summary(uow: UowDep) -> dict[str, Any]:
    await uow.require("staff:view")
    return {
        "items": [
            {
                "place": s.place_name,
                "institution_code": s.institution_code,
                "teaching": s.teaching,
                "non_teaching": s.non_teaching,
                "qualified_teachers": s.qualified_teachers,
                "registered_teachers": s.registered_teachers,
                "retiring_within_a_year": s.retiring_within_a_year,
            }
            for s in await hr.staffing_summary(uow.session)
        ]
    }


# --------------------------------------------------------------------------- appraisal (ADR-025)


class CycleIn(Model):
    name: str = Field(min_length=3, max_length=100, examples=["2026 annual appraisal"])
    period_start: date
    period_end: date
    criteria: dict[str, Any] | None = None


class RatingIn(Model):
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class AppraisalIn(Model):
    ratings: dict[str, RatingIn] = Field(default_factory=dict)
    strengths: str | None = Field(default=None, max_length=1000)
    improvements: str | None = Field(default=None, max_length=1000)
    development_plan: str | None = Field(default=None, max_length=1000)
    recommendation: Literal["none", "promotion", "commendation", "training", "improvement_plan"] = (
        "none"
    )
    appraiser_comment: str | None = Field(default=None, max_length=1000)
    submit: bool = False


class CommentIn(Model):
    comment: str = Field(min_length=1, max_length=1000)


class CountersignIn(Model):
    comment: str | None = Field(default=None, max_length=1000)


class ObservationIn(Model):
    observed_on: date
    class_name: str = Field(min_length=1, max_length=60, examples=["Primary 4B"])
    subject: str = Field(min_length=2, max_length=80, examples=["Mathematics"])
    topic: str | None = Field(default=None, max_length=200)
    ratings: dict[str, RatingIn]
    strengths: str | None = Field(default=None, max_length=1000)
    improvements: str | None = Field(default=None, max_length=1000)


def _cycle(c: appr.CycleView) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "name": c.name,
        "period_start": c.period_start.isoformat(),
        "period_end": c.period_end.isoformat(),
        "status": c.status,
        "criteria": c.criteria,
    }


def _obs(o: appr.ObservationView) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "observed_on": o.observed_on.isoformat(),
        "class": o.class_name,
        "subject": o.subject,
        "topic": o.topic,
        "ratings": o.ratings,
        "score": str(o.score),
        "strengths": o.strengths,
        "improvements": o.improvements,
    }


def _appraisal(a: appr.AppraisalView) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "cycle": {"id": str(a.cycle.id), "name": a.cycle.name, "status": a.cycle.status},
        "staff": {
            "id": str(a.staff_id),
            "staff_number": a.staff_number,
            "name": a.staff_name,
            "category": a.category,
            "position": a.position,
            "place": a.place,
        },
        "status": a.status,
        "criteria": a.criteria,
        "ratings": a.ratings,
        "score": str(a.score) if a.score is not None else None,
        "band": a.band,
        "strengths": a.strengths,
        "improvements": a.improvements,
        "development_plan": a.development_plan,
        "recommendation": a.recommendation,
        "appraiser_comment": a.appraiser_comment,
        "appraisee_comment": a.appraisee_comment,
        "countersigner_comment": a.countersigner_comment,
        "lesson_observations": [_obs(o) for o in a.observations],
    }


def _ratings(r: dict[str, RatingIn]) -> dict[str, dict[str, Any]]:
    return {k: v.model_dump() for k, v in r.items()}


@router.get("/appraisal-cycles", summary="Appraisal cycles that apply to you")
async def list_cycles(uow: UowDep) -> dict[str, Any]:
    await uow.require("appraisal:view")
    return {"items": [_cycle(c) for c in await appr.cycles(uow.session)]}


@router.post("/appraisal-cycles", status_code=201, summary="Open an appraisal cycle (HR)")
async def open_cycle(body: CycleIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await uow.require("appraisal:countersign")
    c = await appr.open_cycle(uow.session, **body.model_dump())
    return remember(uow, key, digest, 201, _cycle(c))


@router.post("/appraisal-cycles/{cycle_id}/close", summary="Close an appraisal cycle")
async def close_cycle(cycle_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"close": str(cycle_id)})
    if replayed:
        return replayed
    await uow.require("appraisal:countersign")
    return remember(uow, key, digest, 200, _cycle(await appr.close_cycle(uow.session, cycle_id)))


@router.get("/appraisal-cycles/{cycle_id}/progress", summary="Progress per place of work")
async def cycle_progress(cycle_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("appraisal:view")
    return {
        "items": [
            {
                "place": p.place,
                "staff": p.staff,
                "not_started": p.not_started,
                "draft": p.draft,
                "submitted": p.submitted,
                "returned": p.returned,
                "countersigned": p.countersigned,
                "average_score": str(p.average_score) if p.average_score is not None else None,
            }
            for p in await appr.progress(uow.session, cycle_id)
        ]
    }


@router.post(
    "/appraisal-cycles/{cycle_id}/appraisals/{staff_id}",
    status_code=201,
    summary="Start a staff member's appraisal (you become the appraiser)",
)
async def start_appraisal(
    cycle_id: uuid.UUID, staff_id: uuid.UUID, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"c": str(cycle_id), "s": str(staff_id)})
    if replayed:
        return replayed
    await uow.require("appraisal:conduct")
    return remember(
        uow, key, digest, 201, _appraisal(await appr.start(uow.session, cycle_id, staff_id))
    )


@router.get("/appraisals/{appraisal_id}", summary="One appraisal, with lesson observations")
async def get_appraisal(appraisal_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("appraisal:view")
    return _appraisal(await appr.get_appraisal(uow.session, appraisal_id))


@router.get("/staff/{staff_id}/appraisals", summary="A staff member's appraisals")
async def staff_appraisals(staff_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("appraisal:view")
    return {"items": [_appraisal(a) for a in await appr.appraisals_of(uow.session, staff_id)]}


@router.post(
    "/appraisals/{appraisal_id}/ratings",
    summary="Save ratings and comments; submit=true sends it for countersigning",
)
async def rate(
    appraisal_id: uuid.UUID, body: AppraisalIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"a": str(appraisal_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("appraisal:conduct")
    data = appr.RatingsIn(
        ratings=_ratings(body.ratings),
        strengths=body.strengths,
        improvements=body.improvements,
        development_plan=body.development_plan,
        recommendation=body.recommendation,
        appraiser_comment=body.appraiser_comment,
    )
    a = await appr.rate(uow.session, appraisal_id, data, submit=body.submit)
    return remember(uow, key, digest, 200, _appraisal(a))


@router.post(
    "/appraisals/{appraisal_id}/appraisee-comment",
    summary="The appraised staff member's own comment",
)
async def comment(
    appraisal_id: uuid.UUID, body: CommentIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"a": str(appraisal_id)} | body.model_dump())
    if replayed:
        return replayed
    a = await appr.appraisee_comment(uow.session, appraisal_id, body.comment)
    return remember(uow, key, digest, 200, _appraisal(a))


async def _countersign(
    appraisal_id: uuid.UUID, body: CountersignIn, request: Request, uow: UowDep, approve: bool
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"a": str(appraisal_id), "ok": approve} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("appraisal:countersign")
    a = await appr.countersign(uow.session, appraisal_id, approve=approve, comment=body.comment)
    return remember(uow, key, digest, 200, _appraisal(a))


@router.post("/appraisals/{appraisal_id}/countersign", summary="Countersign (four-eyes)")
async def countersign(
    appraisal_id: uuid.UUID, body: CountersignIn, request: Request, uow: UowDep
) -> JSONResponse:
    return await _countersign(appraisal_id, body, request, uow, True)


@router.post("/appraisals/{appraisal_id}/return", summary="Return to the appraiser with a reason")
async def return_(
    appraisal_id: uuid.UUID, body: CountersignIn, request: Request, uow: UowDep
) -> JSONResponse:
    return await _countersign(appraisal_id, body, request, uow, False)


@router.post(
    "/staff/{staff_id}/lesson-observations", status_code=201, summary="Record a lesson observed"
)
async def observe(
    staff_id: uuid.UUID, body: ObservationIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"s": str(staff_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("appraisal:conduct")
    o = await appr.observe_lesson(
        uow.session,
        staff_id,
        observed_on=body.observed_on,
        class_name=body.class_name,
        subject=body.subject,
        topic=body.topic,
        ratings=_ratings(body.ratings),
        strengths=body.strengths,
        improvements=body.improvements,
    )
    return remember(uow, key, digest, 201, _obs(o))
