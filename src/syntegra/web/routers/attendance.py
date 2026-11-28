"""Attendance: class registers, staff attendance, school closures (ADR-032)."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.assessment import api as assessment
from syntegra.attendance import api as att
from syntegra.core.errors import Forbidden
from syntegra.iam import api as iam
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.routers.common import dump, may_view_learner, my_staff_ids, require_any
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["attendance"])
Session = Literal["am", "pm"]


async def _may_take(uow: UowDep, class_id: uuid.UUID) -> bool:
    """May take this class's register; returns whether they may also correct past days."""
    d = await uow.require("attendance:mark")
    if d.condition == "own_classes_only" and not await assessment.is_class_teacher(
        uow.session, await my_staff_ids(uow), class_id
    ):
        raise Forbidden("the class teacher takes this class's register")
    try:
        await uow.require("attendance:amend")
        return True
    except Forbidden:
        return False


@router.get("/classes/{class_id}/register", summary="A class's register for a day")
async def get_register(
    class_id: uuid.UUID, uow: UowDep, on: date, session: Session = "am"
) -> JSONResponse:
    await _may_take(uow, class_id)
    return JSONResponse(dump(await att.get_register(uow.session, class_id, on, session)))


class MarkBody(Model):
    learner_id: uuid.UUID
    status: Literal["present", "late", "absent", "excused"]
    reason: str | None = Field(default=None, max_length=200)


class RegisterBody(Model):
    on: date
    session: Session
    marks: list[MarkBody] = Field(min_length=1, max_length=200)
    amend_reason: str | None = Field(default=None, max_length=200)


@router.put("/classes/{class_id}/register", summary="Take (or correct) a class's register")
async def take_register(class_id: uuid.UUID, body: RegisterBody, uow: UowDep) -> JSONResponse:
    amend = await _may_take(uow, class_id)
    v = await att.take_register(
        uow.session,
        class_id,
        body.on,
        body.session,
        [att.MarkIn(m.learner_id, m.status, m.reason) for m in body.marks],
        may_amend=amend,
        amend_reason=body.amend_reason,
    )
    return JSONResponse(dump(v))


@router.get("/classes/{class_id}/attendance", summary="Each learner's attendance in a term")
async def class_attendance(class_id: uuid.UUID, term_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    d = await require_any(uow, "attendance:mark", "learner:view")
    if d.condition == "own_classes_only" and not await assessment.is_class_teacher(
        uow.session, await my_staff_ids(uow), class_id
    ):
        raise Forbidden("class teachers see their own class's attendance")
    return JSONResponse(dump(await att.class_attendance(uow.session, class_id, term_id)))


@router.get("/learners/{learner_id}/attendance", summary="A learner's attendance in a term")
async def learner_attendance(
    learner_id: uuid.UUID, term_id: uuid.UUID, uow: UowDep
) -> JSONResponse:
    await may_view_learner(uow, learner_id)
    s = await att.learner_summary(uow.session, learner_id, term_id)
    return JSONResponse(s.as_dict())


@router.get(
    "/institutions/{institution_id}/attendance/day",
    summary="Which classes have taken their registers on a day",
)
async def school_day(institution_id: uuid.UUID, on: date, uow: UowDep) -> JSONResponse:
    await require_any(uow, "attendance:amend", "staff_attendance:record", "monitoring:view")
    return JSONResponse(dump(await att.school_day(uow.session, institution_id, on)))


# --------------------------------------------------------------------------- staff


@router.get("/institutions/{institution_id}/staff-attendance", summary="Staff attendance, a day")
async def staff_day(institution_id: uuid.UUID, on: date, uow: UowDep) -> JSONResponse:
    await require_any(uow, "staff_attendance:record", "monitoring:view")
    return JSONResponse({"items": dump(await att.staff_day(uow.session, institution_id, on))})


class StaffBody(Model):
    staff_id: uuid.UUID
    status: Literal["present", "late", "absent", "leave", "sick", "official_duty"]
    time_in: time | None = None
    time_out: time | None = None
    note: str | None = Field(default=None, max_length=200)


class StaffDayBody(Model):
    on: date
    entries: list[StaffBody] = Field(min_length=1, max_length=500)


@router.put("/institutions/{institution_id}/staff-attendance", summary="Record staff attendance")
async def record_staff(institution_id: uuid.UUID, body: StaffDayBody, uow: UowDep) -> JSONResponse:
    await uow.require("staff_attendance:record")
    items = await att.record_staff(
        uow.session,
        institution_id,
        body.on,
        [att.StaffIn(e.staff_id, e.status, e.time_in, e.time_out, e.note) for e in body.entries],
    )
    return JSONResponse({"items": dump(items)})


@router.get("/me/attendance", summary="My attendance today (staff)")
async def my_day(uow: UowDep) -> JSONResponse:
    person = await iam.person_of_user(uow.session, uow.principal.user_id)
    return JSONResponse(dump(await att.my_day(uow.session, person)))


@router.post("/me/attendance/{action}", summary="Sign in or out (staff)")
async def sign(action: Literal["in", "out"], request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"sign": action})
    if replayed:
        return replayed
    person = await iam.person_of_user(uow.session, uow.principal.user_id)
    return remember(uow, key, digest, 200, dump(await att.sign(uow.session, person, action)))


# --------------------------------------------------------------------------- closures


@router.get("/closures", summary="Days with no school, at and above the active scope")
async def closures(start: date, end: date, uow: UowDep) -> JSONResponse:
    return JSONResponse({"items": dump(await att.closures(uow.session, start, end))})


class ClosureBody(Model):
    on: date
    reason: str = Field(min_length=3, max_length=120)


@router.post("/closures", summary="Close every school beneath the active scope for a day")
async def add_closure(body: ClosureBody, request: Request, uow: UowDep) -> JSONResponse:
    await require_any(uow, "calendar:manage", "attendance:amend")
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    c = await att.add_closure(uow.session, body.on, body.reason)
    return remember(uow, key, digest, 201, dump(c))
