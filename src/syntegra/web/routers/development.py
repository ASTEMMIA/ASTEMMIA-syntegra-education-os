"""Performance management and continuous professional development (ADR-026)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.hr import appraisal as appr
from syntegra.hr import cpd
from syntegra.hr.models import CPD_CATEGORIES
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.routers.staff import RatingIn, _appraisal
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["performance and CPD"])

Category = Literal[CPD_CATEGORIES]  # type: ignore[valid-type]


# --------------------------------------------------------------------------- objectives


class ObjectiveIn(Model):
    title: str = Field(min_length=3, max_length=200, examples=["Raise Primary 5 numeracy"])
    measure: str = Field(
        min_length=3, max_length=300, examples=["Share of pupils at expected level"]
    )
    target: str = Field(min_length=1, max_length=200, examples=["70% by the third term"])
    weight: int = Field(ge=1, le=100)


class ReviewIn(Model):
    progress: int = Field(ge=0, le=100)
    note: str | None = Field(default=None, max_length=500)


class AssessIn(Model):
    achievement: int = Field(ge=0, le=100)
    note: str | None = Field(default=None, max_length=500)


class DropIn(Model):
    reason: str = Field(min_length=3, max_length=500)


def _objective(o: appr.ObjectiveView) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "title": o.title,
        "measure": o.measure,
        "target": o.target,
        "weight": o.weight,
        "status": o.status,
        "mid_year_progress": o.mid_year_progress,
        "mid_year_note": o.mid_year_note,
        "achievement": o.achievement,
        "final_note": o.final_note,
    }


async def _guard(
    uow: UowDep, request: Request, payload: dict[str, Any]
) -> tuple[str, bytes, JSONResponse | None]:
    return await replay(uow, request, payload)


@router.post(
    "/appraisal-cycles/{cycle_id}/objectives/{staff_id}",
    status_code=201,
    summary="Agree an objective (KPI) with a staff member for the cycle",
)
async def add_objective(
    cycle_id: uuid.UUID, staff_id: uuid.UUID, body: ObjectiveIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"c": str(cycle_id), "s": str(staff_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("appraisal:conduct")
    items = await appr.add_objective(uow.session, cycle_id, staff_id, **body.model_dump())
    return remember(uow, key, digest, 201, {"items": [_objective(o) for o in items]})


async def _objective_change(
    uow: UowDep, request: Request, objective_id: uuid.UUID, kind: str, body: Model
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"o": str(objective_id), "k": kind} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("appraisal:conduct")
    if kind == "review":
        assert isinstance(body, ReviewIn)
        o = await appr.review_objective(
            uow.session, objective_id, progress=body.progress, note=body.note
        )
    elif kind == "assess":
        assert isinstance(body, AssessIn)
        o = await appr.assess_objective(
            uow.session, objective_id, achievement=body.achievement, note=body.note
        )
    else:
        assert isinstance(body, DropIn)
        o = await appr.drop_objective(uow.session, objective_id, reason=body.reason)
    return remember(uow, key, digest, 200, _objective(o))


@router.post("/objectives/{objective_id}/mid-year-review", summary="Mid-year progress")
async def review(
    objective_id: uuid.UUID, body: ReviewIn, request: Request, uow: UowDep
) -> JSONResponse:
    return await _objective_change(uow, request, objective_id, "review", body)


@router.post("/objectives/{objective_id}/assess", summary="Year-end achievement")
async def assess(
    objective_id: uuid.UUID, body: AssessIn, request: Request, uow: UowDep
) -> JSONResponse:
    return await _objective_change(uow, request, objective_id, "assess", body)


@router.post("/objectives/{objective_id}/drop", summary="Drop an objective, with the reason")
async def drop(
    objective_id: uuid.UUID, body: DropIn, request: Request, uow: UowDep
) -> JSONResponse:
    return await _objective_change(uow, request, objective_id, "drop", body)


# ----------------------------------------------------------------- self-assessment and appeals


class SelfIn(Model):
    ratings: dict[str, RatingIn]


class AppealIn(Model):
    grounds: str = Field(min_length=10, max_length=2000)


class AppealDecisionIn(Model):
    uphold: bool
    note: str = Field(min_length=3, max_length=1000)
    revised_ratings: dict[str, RatingIn] | None = None


@router.post(
    "/appraisals/{appraisal_id}/self-assessment",
    summary="Rate yourself on the appraisal criteria (the appraised staff member)",
)
async def self_assessment(
    appraisal_id: uuid.UUID, body: SelfIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"a": str(appraisal_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    a = await appr.self_assess(
        uow.session, appraisal_id, {k: v.model_dump() for k, v in body.ratings.items()}
    )
    return remember(uow, key, digest, 200, _appraisal(a))


@router.post("/appraisals/{appraisal_id}/appeal", status_code=201, summary="Appeal your appraisal")
async def appeal(
    appraisal_id: uuid.UUID, body: AppealIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"a": str(appraisal_id)} | body.model_dump())
    if replayed:
        return replayed
    a = await appr.lodge_appeal(uow.session, appraisal_id, body.grounds)
    return remember(uow, key, digest, 201, _appraisal(a))


@router.post(
    "/appraisals/{appraisal_id}/appeal/decision",
    summary="Decide an appeal (an officer not involved in the appraisal)",
)
async def appeal_decision(
    appraisal_id: uuid.UUID, body: AppealDecisionIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"a": str(appraisal_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("appraisal:countersign")
    revised = (
        {k: v.model_dump() for k, v in body.revised_ratings.items()}
        if body.revised_ratings
        else None
    )
    a = await appr.decide_appeal(
        uow.session, appraisal_id, uphold=body.uphold, note=body.note, revised_ratings=revised
    )
    return remember(uow, key, digest, 200, _appraisal(a))


# --------------------------------------------------------------------------- CPD programmes


class ProgrammeBody(Model):
    code: str = Field(min_length=2, max_length=30, examples=["ASUBEB-NUM-2026-01"])
    title: str = Field(min_length=3, max_length=200)
    provider: str = Field(min_length=2, max_length=200, examples=["National Teachers' Institute"])
    provider_accredited: bool = False
    category: Category
    mode: Literal["in_person", "online", "blended", "school_based"]
    audience: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    start_date: date
    end_date: date
    venue: str | None = Field(default=None, max_length=200)
    hours: Decimal = Field(gt=0, max_digits=6, decimal_places=1)
    credit_points: int = Field(ge=0)
    capacity: int | None = Field(default=None, ge=1)


class NominateIn(Model):
    staff_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    appraisal_id: uuid.UUID | None = None


class ProgressIn(Model):
    status: Literal["enrolled", "attended", "completed", "withdrawn"]
    completed_on: date | None = None
    evidence: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=500)


class ReportIn(Model):
    title: str = Field(min_length=3, max_length=200)
    provider: str | None = Field(default=None, max_length=200)
    category: Category
    activity_type: Literal[
        "workshop", "course", "conference", "mentoring", "peer_observation", "research",
        "qualification", "other",
    ]  # fmt: skip
    started_on: date | None = None
    completed_on: date
    hours: Decimal | None = Field(default=None, gt=0, max_digits=6, decimal_places=1)
    credit_points: int = Field(default=0, ge=0)
    evidence: str = Field(min_length=3, max_length=300)
    reflection: str | None = Field(default=None, max_length=1000)


class VerifyIn(Model):
    approve: bool
    note: str | None = Field(default=None, max_length=500)
    credit_points: int | None = Field(default=None, ge=0)


class ProgrammeStatusIn(Model):
    status: Literal["planned", "open", "closed", "completed", "cancelled"]


def _programme(p: cpd.ProgrammeView) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "code": p.code,
        "title": p.title,
        "provider": p.provider,
        "provider_accredited": p.provider_accredited,
        "category": p.category,
        "mode": p.mode,
        "audience": p.audience,
        "description": p.description,
        "start_date": p.start_date.isoformat(),
        "end_date": p.end_date.isoformat(),
        "venue": p.venue,
        "hours": str(p.hours),
        "credit_points": p.credit_points,
        "capacity": p.capacity,
        "places_taken": p.places_taken,
        "status": p.status,
    }


def _part(x: cpd.ParticipationView) -> dict[str, Any]:
    return {
        "id": str(x.id),
        "staff_id": str(x.staff_id),
        "programme_id": str(x.programme_id) if x.programme_id else None,
        "title": x.title,
        "provider": x.provider,
        "category": x.category,
        "activity_type": x.activity_type,
        "started_on": x.started_on.isoformat() if x.started_on else None,
        "completed_on": x.completed_on.isoformat() if x.completed_on else None,
        "hours": str(x.hours) if x.hours is not None else None,
        "credit_points": x.credit_points,
        "status": x.status,
        "source": x.source,
        "evidence": x.evidence,
        "reflection": x.reflection,
        "note": x.note,
    }


@router.get("/cpd-programmes", summary="CPD programmes you can nominate staff to")
async def list_programmes(
    uow: UowDep, category: str | None = None, open_only: bool = False
) -> dict[str, Any]:
    await uow.require("cpd:view")
    items = await cpd.programmes(uow.session, category=category, open_only=open_only)
    return {"items": [_programme(p) for p in items]}


@router.post("/cpd-programmes", status_code=201, summary="Create a CPD programme")
async def create_programme(body: ProgrammeBody, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await uow.require("cpd:manage")
    p = await cpd.create_programme(uow.session, cpd.ProgrammeIn(**body.model_dump()))
    return remember(uow, key, digest, 201, _programme(p))


@router.post("/cpd-programmes/{programme_id}/status", summary="Open, close, complete or cancel")
async def programme_status(
    programme_id: uuid.UUID, body: ProgrammeStatusIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"p": str(programme_id)} | body.model_dump())
    if replayed:
        return replayed
    await uow.require("cpd:manage")
    p = await cpd.set_programme_status(uow.session, programme_id, body.status)
    return remember(uow, key, digest, 200, _programme(p))


@router.get("/cpd-programmes/{programme_id}/participants", summary="Who is on a programme")
async def programme_participants(programme_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("cpd:view")
    return {"items": [_part(x) for x in await cpd.participants(uow.session, programme_id)]}


@router.post(
    "/cpd-programmes/{programme_id}/nominations",
    status_code=201,
    summary="Nominate staff (within capacity)",
)
async def nominate(
    programme_id: uuid.UUID, body: NominateIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"p": str(programme_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("cpd:manage")
    items = await cpd.nominate(
        uow.session, programme_id, body.staff_ids, appraisal_id=body.appraisal_id
    )
    return remember(uow, key, digest, 201, {"items": [_part(x) for x in items]})


@router.post("/cpd-participations/{participation_id}/progress", summary="Enrolled, attended…")
async def participation_progress(
    participation_id: uuid.UUID, body: ProgressIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"x": str(participation_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("cpd:manage")
    x = await cpd.progress_participation(uow.session, participation_id, **body.model_dump())
    return remember(uow, key, digest, 200, _part(x))


@router.post("/cpd-participations/{participation_id}/verify", summary="Verify or reject CPD")
async def verify(
    participation_id: uuid.UUID, body: VerifyIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"x": str(participation_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("cpd:manage")
    x = await cpd.verify(uow.session, participation_id, **body.model_dump())
    return remember(uow, key, digest, 200, _part(x))


@router.post("/me/cpd", status_code=201, summary="Report CPD you did yourself (with evidence)")
async def report(body: ReportIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    x = await cpd.report_activity(uow.session, **body.model_dump())
    return remember(uow, key, digest, 201, _part(x))


@router.get("/staff/{staff_id}/cpd", summary="A staff member's CPD record and points by year")
async def staff_cpd(staff_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("cpd:view")
    items = await cpd.record_of(uow.session, staff_id)
    by_year: dict[int, int] = {}
    for x in items:
        if x.status == "verified" and x.completed_on:
            by_year[x.completed_on.year] = by_year.get(x.completed_on.year, 0) + x.credit_points
    return {"items": [_part(x) for x in items], "verified_points_by_year": by_year}


@router.get("/cpd-compliance", summary="Verified CPD points against the annual requirement")
async def compliance(uow: UowDep, year: int, teaching_only: bool = True) -> dict[str, Any]:
    await uow.require("cpd:view")
    rows = await cpd.compliance(uow.session, year, teaching_only=teaching_only)
    places: dict[str, dict[str, int]] = {}
    for r in rows:
        p = places.setdefault(r.place or "(no current post)", {"staff": 0, "met": 0})
        p["staff"] += 1
        p["met"] += int(r.met)
    return {
        "year": year,
        "required_points": await cpd.annual_requirement(uow.session),
        "staff": [
            {
                "staff_id": str(r.staff_id),
                "staff_number": r.staff_number,
                "name": r.name,
                "place": r.place,
                "points": r.points,
                "hours": str(r.hours),
                "shortfall": r.shortfall,
                "met": r.met,
            }
            for r in rows
        ],
        "by_place": [{"place": k} | v for k, v in sorted(places.items())],
    }


# --------------------------------------------------------------------------- development plans


class ActionIn(Model):
    need: str = Field(min_length=3, max_length=300)
    action: str = Field(min_length=3, max_length=300)
    category: Category
    due_date: date | None = None
    appraisal_id: uuid.UUID | None = None


class ActionUpdateIn(Model):
    status: Literal["planned", "in_progress", "done", "dropped"]
    outcome: str | None = Field(default=None, max_length=500)
    participation_id: uuid.UUID | None = None


def _action(a: cpd.ActionView) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "appraisal_id": str(a.appraisal_id) if a.appraisal_id else None,
        "need": a.need,
        "action": a.action,
        "category": a.category,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "status": a.status,
        "overdue": a.overdue,
        "participation_id": str(a.participation_id) if a.participation_id else None,
        "outcome": a.outcome,
    }


@router.get("/staff/{staff_id}/development-plan", summary="Individual development plan")
async def plan(staff_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("cpd:view")
    return {"items": [_action(a) for a in await cpd.plan_of(uow.session, staff_id)]}


@router.post(
    "/staff/{staff_id}/development-plan", status_code=201, summary="Add a development action"
)
async def add_action(
    staff_id: uuid.UUID, body: ActionIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"s": str(staff_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("cpd:manage")
    a = await cpd.add_action(uow.session, staff_id, **body.model_dump())
    return remember(uow, key, digest, 201, _action(a))


@router.post("/development-actions/{action_id}", summary="Update a development action")
async def update_action(
    action_id: uuid.UUID, body: ActionUpdateIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"a": str(action_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("cpd:manage")
    a = await cpd.update_action(uow.session, action_id, **body.model_dump())
    return remember(uow, key, digest, 200, _action(a))


@router.get(
    "/appraisal-cycles/{cycle_id}/training-needs",
    summary="Training needs from countersigned appraisals, by place and CPD category",
)
async def needs(cycle_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("cpd:view")
    out = await cpd.training_needs(uow.session, cycle_id)
    return out | {
        "needs": [
            {
                "place": n.place,
                "criterion": n.criterion,
                "cpd_category": n.cpd_category,
                "staff_rated_low": n.staff_rated_low,
            }
            for n in out["needs"]
        ]
    }
