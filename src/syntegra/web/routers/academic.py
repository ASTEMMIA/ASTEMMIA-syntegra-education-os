"""Academic structure: calendar, curriculum, rooms, classes, subjects, teaching (ADR-029)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.academic import api as acad
from syntegra.academic.domain import YEARS
from syntegra.iam import api as iam
from syntegra.learners import api as lrn
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.routers.common import dump, require_any
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["academic structure"])

Kind = Literal["core", "elective", "optional"]


async def _guard(
    uow: UowDep, request: Request, payload: dict[str, Any]
) -> tuple[str, bytes, JSONResponse | None]:
    return await replay(uow, request, payload)


@router.get(
    "/class-years", summary="Every class year the platform knows (Primary 4, JSS 1, 100 Level…)"
)
async def class_years(uow: UowDep) -> JSONResponse:
    return JSONResponse(
        {
            "items": [
                {
                    "code": y.code,
                    "name": y.name,
                    "level": y.level,
                    "order": y.order,
                    "typical_age": y.typical_age,
                }
                for y in YEARS.values()
            ]
        }
    )


# --------------------------------------------------------------------------- calendar


class Part(Model):
    starts_on: date
    ends_on: date


class SessionBody(Model):
    name: str = Field(pattern=r"^\d{4}/\d{4}$", examples=["2026/2027"])
    parts: list[Part] = Field(min_length=2, max_length=3)
    tertiary: bool = False


@router.get("/sessions", summary="Sessions, with their terms or semesters, that apply here")
async def list_sessions(uow: UowDep) -> JSONResponse:
    return JSONResponse({"items": dump(await acad.sessions(uow.session))})


@router.post(
    "/sessions", status_code=201, summary="Set a session and its terms (or semesters) here"
)
async def new_session(body: SessionBody, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await uow.require("calendar:manage")
    v = await acad.create_session(
        uow.session,
        acad.SessionIn(body.name, [(p.starts_on, p.ends_on) for p in body.parts], body.tertiary),
    )
    return remember(uow, key, digest, 201, dump(v))


@router.post("/periods/{period_id}/current", summary="Make a session, term or semester current")
async def make_current(period_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"p": str(period_id)})
    if replayed:
        return replayed
    await uow.require("calendar:manage")
    return remember(uow, key, digest, 200, dump(await acad.set_current(uow.session, period_id)))


# --------------------------------------------------------------------------- curriculum


class AdoptBody(Model):
    years: list[str] | None = Field(default=None, description="Class years; empty for all")
    from_session: str = Field(pattern=r"^\d{4}/\d{4}$")


@router.get("/curricula", summary="Curriculum versions and where they are adopted")
async def list_curricula(uow: UowDep) -> JSONResponse:
    return JSONResponse({"items": dump(await acad.curricula(uow.session))})


@router.get("/curricula/{version_id}/years/{year_code}", summary="The subjects of one year")
async def curriculum_year(version_id: uuid.UUID, year_code: str, uow: UowDep) -> JSONResponse:
    return JSONResponse(
        {"items": dump(await acad.entries_for(uow.session, version_id, year_code.upper()))}
    )


@router.post("/curricula/{version_id}/adopt", summary="Adopt a curriculum version here")
async def adopt(
    version_id: uuid.UUID, body: AdoptBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"v": str(version_id)} | body.model_dump())
    if replayed:
        return replayed
    await uow.require("curriculum:publish")
    v = await acad.adopt(uow.session, version_id, years=body.years, from_session=body.from_session)
    return remember(uow, key, digest, 200, dump(v))


# --------------------------------------------------------------------------- rooms and subjects


class RoomBody(Model):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=2, max_length=100)
    capacity: int = Field(ge=1, le=2000)
    kind: Literal[
        "classroom", "laboratory", "workshop", "ict", "library", "hall", "lecture_theatre", "other"
    ] = "classroom"
    building: str | None = Field(default=None, max_length=100)


@router.get("/institutions/{institution_id}/rooms", summary="Rooms")
async def list_rooms(institution_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    return JSONResponse({"items": dump(await acad.rooms(uow.session, institution_id))})


@router.post("/institutions/{institution_id}/rooms", status_code=201, summary="Add a room")
async def add_room(
    institution_id: uuid.UUID, body: RoomBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"i": str(institution_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("class:manage")
    return remember(
        uow,
        key,
        digest,
        201,
        dump(await acad.add_room(uow.session, institution_id, **body.model_dump())),
    )


class SubjectBody(Model):
    code: str = Field(min_length=2, max_length=16, examples=["CSC 101"])
    name: str = Field(min_length=2, max_length=150)
    units: int | None = Field(default=None, ge=0, le=30)
    year_code: str | None = None
    semester: Literal[1, 2] | None = None
    department: str | None = Field(default=None, max_length=100)
    group: str | None = Field(default=None, max_length=60)


@router.get(
    "/institutions/{institution_id}/subjects", summary="State subjects and this institution's own"
)
async def list_subjects(institution_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await acad.institution(uow.session, institution_id)
    return JSONResponse({"items": dump(await acad.subjects(uow.session, institution_id))})


@router.post(
    "/institutions/{institution_id}/subjects",
    status_code=201,
    summary="Add a local subject or a course",
)
async def add_subject(
    institution_id: uuid.UUID, body: SubjectBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"i": str(institution_id)} | body.model_dump()
    )
    if replayed:
        return replayed
    await uow.require("class:manage")
    v = await acad.add_subject(uow.session, institution_id, acad.CourseIn(**body.model_dump()))
    return remember(uow, key, digest, 201, dump(v))


# --------------------------------------------------------------------------- classes


class ClassBody(Model):
    session_id: uuid.UUID
    year_code: str = Field(min_length=2, max_length=8, examples=["P4"])
    arm: str = Field(min_length=1, max_length=20, examples=["A"])
    capacity: int = Field(default=40, ge=1, le=120)
    room_id: uuid.UUID | None = None
    class_teacher_staff_id: uuid.UUID | None = None


class ClassChangeBody(Model):
    capacity: int | None = Field(default=None, ge=1, le=120)
    room_id: uuid.UUID | None = None
    clear_room: bool = False
    class_teacher_staff_id: uuid.UUID | None = None
    assistant_staff_id: uuid.UUID | None = None


def _class(c: acad.ClassView, size: int) -> dict[str, Any]:
    out: dict[str, Any] = dump(c)
    out["size"] = size
    out["places_left"] = max(0, c.capacity - size)
    return out


@router.get("/institutions/{institution_id}/classes", summary="Classes, with their sizes")
async def list_classes(
    institution_id: uuid.UUID, uow: UowDep, session_id: uuid.UUID | None = None
) -> JSONResponse:
    items = await acad.classes(uow.session, institution_id, session_id)
    sizes = await lrn.class_sizes(uow.session, [c.id for c in items])
    return JSONResponse({"items": [_class(c, sizes[c.id]) for c in items]})


@router.post(
    "/institutions/{institution_id}/classes",
    status_code=201,
    summary="Open a class (an arm of a year)",
)
async def new_class(
    institution_id: uuid.UUID, body: ClassBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"i": str(institution_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("class:manage")
    c = await acad.create_class(uow.session, institution_id, acad.ClassIn(**body.model_dump()))
    return remember(uow, key, digest, 201, _class(c, 0))


@router.get("/classes/{class_id}", summary="A class: its teacher, room, size and subjects")
async def get_class(class_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    c = await acad.class_view(uow.session, await acad.get_class(uow.session, class_id))
    size = (await lrn.class_sizes(uow.session, [c.id]))[c.id]
    out = _class(c, size)
    out["subjects"] = dump(await acad.class_offerings(uow.session, class_id))
    return JSONResponse(out)


@router.post(
    "/classes/{class_id}/settings", summary="Change capacity, room, class teacher or assistant"
)
async def change_class(
    class_id: uuid.UUID, body: ClassChangeBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"c": str(class_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("class:manage")
    size = (await lrn.class_sizes(uow.session, [class_id]))[class_id]
    c, warnings = await acad.change_class(
        uow.session, class_id, acad.ClassChange(**body.model_dump()), members=size
    )
    return remember(uow, key, digest, 200, _class(c, size) | {"warnings": warnings})


@router.post("/classes/{class_id}/close", summary="Close a class")
async def close_class(class_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"c": str(class_id), "a": "close"})
    if replayed:
        return replayed
    await uow.require("class:manage")
    return remember(uow, key, digest, 200, dump(await acad.close_class(uow.session, class_id)))


class ClassSubjectBody(Model):
    subject_id: uuid.UUID
    kind: Kind = "core"
    periods_per_week: int | None = Field(default=None, ge=0, le=20)


@router.post(
    "/classes/{class_id}/subjects", status_code=201, summary="Teach another subject in this class"
)
async def add_class_subject(
    class_id: uuid.UUID, body: ClassSubjectBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"c": str(class_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("class:manage")
    o = await acad.add_class_subject(
        uow.session,
        class_id,
        body.subject_id,
        kind=body.kind,
        periods_per_week=body.periods_per_week,
    )
    return remember(uow, key, digest, 201, dump(o))


@router.post(
    "/offerings/{offering_id}/drop", summary="Stop teaching a subject in a class, or a course"
)
async def drop(offering_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"o": str(offering_id), "a": "drop"})
    if replayed:
        return replayed
    await uow.require("class:manage")
    return remember(uow, key, digest, 200, dump(await acad.drop_offering(uow.session, offering_id)))


class CourseRunBody(Model):
    subject_id: uuid.UUID
    capacity: int | None = Field(default=None, ge=1, le=5000)


@router.post(
    "/semesters/{semester_id}/courses", status_code=201, summary="Run a course in a semester"
)
async def run_course(
    semester_id: uuid.UUID, body: CourseRunBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"s": str(semester_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("class:manage")
    o = await acad.offer_course(uow.session, semester_id, body.subject_id, capacity=body.capacity)
    return remember(uow, key, digest, 201, dump(o))


@router.get("/institutions/{institution_id}/courses", summary="Courses running in a semester")
async def list_courses(
    institution_id: uuid.UUID, semester_id: uuid.UUID, uow: UowDep
) -> JSONResponse:
    return JSONResponse(
        {"items": dump(await acad.course_offerings(uow.session, institution_id, semester_id))}
    )


# --------------------------------------------------------------------------- teaching staff


class AssignBody(Model):
    staff_id: uuid.UUID
    role: Literal["lead", "assistant"] = "lead"
    starts_on: date | None = None
    note: str | None = Field(default=None, max_length=200)


@router.post(
    "/offerings/{offering_id}/teachers", summary="Assign a subject teacher or course lecturer"
)
async def assign(
    offering_id: uuid.UUID, body: AssignBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await _guard(
        uow, request, {"o": str(offering_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("teaching:assign")
    r = await acad.assign_teacher(
        uow.session,
        offering_id,
        body.staff_id,
        role=body.role,
        starts_on=body.starts_on,
        note=body.note,
    )
    return remember(uow, key, digest, 200, {"offering": dump(r.offering), "warnings": r.warnings})


@router.post("/assignments/{assignment_id}/end", summary="End a teaching assignment")
async def end_assignment(assignment_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await _guard(uow, request, {"a": str(assignment_id)})
    if replayed:
        return replayed
    await uow.require("teaching:assign")
    return remember(
        uow, key, digest, 200, dump(await acad.end_assignment(uow.session, assignment_id))
    )


@router.get("/staff/{staff_id}/teaching", summary="A teacher's subjects and weekly periods")
async def teaching(staff_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await require_any(uow, "teaching:assign", "staff:view")
    load, items = await acad.teaching_load(uow.session, staff_id)
    return JSONResponse({"periods_per_week": load, "items": dump(items)})


@router.get("/my/classes", summary="Classes I teach or am class teacher of")
async def my_classes(uow: UowDep) -> JSONResponse:
    person = await iam.person_of_user(uow.session, uow.principal.user_id)
    staff = await acad.staff_of_person(uow.session, person) if person else []
    items = [
        await acad.class_view(uow.session, c) for c in await acad.classes_taught(uow.session, staff)
    ]
    sizes = await lrn.class_sizes(uow.session, [c.id for c in items])
    return JSONResponse({"items": [_class(c, sizes[c.id]) for c in items]})
