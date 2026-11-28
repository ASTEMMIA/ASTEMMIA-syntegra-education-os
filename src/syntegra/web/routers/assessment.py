"""Continuous assessment, examinations, report cards and transcripts (ADR-032)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.assessment import api as ass
from syntegra.attendance import api as att
from syntegra.core.errors import Forbidden
from syntegra.iam import api as iam
from syntegra.learners import api as lrn
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.routers.common import (
    dump,
    is_parent_only,
    may_view_learner,
    my_staff_ids,
    require_any,
)
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["assessment"])


async def _may_mark(uow: UowDep, offering_id: uuid.UUID) -> None:
    """Subject teachers mark their own subjects; heads and administrators any."""
    d = await uow.require("markbook:edit")
    if d.condition == "own_classes_only" and not await ass.teaches(
        uow.session, await my_staff_ids(uow), offering_id
    ):
        raise Forbidden("only the subject's teacher enters its scores")


async def _may_see_sheet(uow: UowDep, offering_id: uuid.UUID) -> None:
    try:
        await uow.require("results:approve")
    except Forbidden:
        await _may_mark(uow, offering_id)


# --------------------------------------------------------------------------- rules


@router.get("/assessment/policies", summary="The assessment rules in force here")
async def policies(uow: UowDep) -> JSONResponse:
    return JSONResponse({"items": dump(await ass.policies(uow.session))})


@router.put("/assessment/policies/{group}", summary="Set this scope's own rules for a group")
async def set_policy(
    group: Literal["basic", "senior", "nbte", "nuc"], body: dict[str, Any], uow: UowDep
) -> JSONResponse:
    await uow.require("assessment:configure")
    return JSONResponse(dump(await ass.set_policy(uow.session, {**body, "group": group})))


# --------------------------------------------------------------------------- result sheets


@router.get("/my/subjects", summary="The subjects I teach, and their terms")
async def my_subjects(uow: UowDep) -> JSONResponse:
    return JSONResponse({"items": dump(await ass.taught_by(uow.session, await my_staff_ids(uow)))})


@router.get("/offerings/{offering_id}/results/{term_id}", summary="A subject's result sheet")
async def sheet(offering_id: uuid.UUID, term_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await _may_see_sheet(uow, offering_id)
    return JSONResponse(dump(await ass.get_sheet(uow.session, offering_id, term_id)))


class ScoreBody(Model):
    learner_id: uuid.UUID
    component: str = Field(min_length=1, max_length=8)
    score: Decimal | None = Field(default=None, ge=0, le=100)
    absent: bool = False


class ScoresBody(Model):
    entries: list[ScoreBody] = Field(min_length=1, max_length=2000)


@router.put("/offerings/{offering_id}/results/{term_id}/scores", summary="Enter scores")
async def enter(
    offering_id: uuid.UUID, term_id: uuid.UUID, body: ScoresBody, uow: UowDep
) -> JSONResponse:
    await _may_mark(uow, offering_id)
    v = await ass.enter_scores(
        uow.session,
        offering_id,
        term_id,
        [ass.ScoreIn(e.learner_id, e.component, e.score, e.absent) for e in body.entries],
    )
    return JSONResponse(dump(v))


class NoteBody(Model):
    note: str = Field(min_length=5, max_length=500)


@router.post(
    "/offerings/{offering_id}/results/{term_id}/{action}",
    summary="Submit (teacher), approve or send back (head) a result sheet",
)
async def sheet_action(
    offering_id: uuid.UUID,
    term_id: uuid.UUID,
    action: Literal["submit", "approve", "return"],
    request: Request,
    uow: UowDep,
    body: NoteBody | None = None,
) -> JSONResponse:
    if action == "submit":
        await _may_mark(uow, offering_id)
    else:
        await uow.require("results:approve")
    payload = {"o": str(offering_id), "t": str(term_id), "a": action}
    key, digest, replayed = await replay(uow, request, payload | {"n": body.note if body else None})
    if replayed:
        return replayed
    if action == "submit":
        v = await ass.submit(uow.session, offering_id, term_id)
    elif action == "approve":
        v = await ass.approve(uow.session, offering_id, term_id)
    else:
        if body is None:
            raise Forbidden("say what needs changing")
        v = await ass.send_back(uow.session, offering_id, term_id, body.note)
    return remember(uow, key, digest, 200, {"status": v.status})


@router.get("/institutions/{institution_id}/results", summary="Every subject's results status")
async def sheets(institution_id: uuid.UUID, term_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await require_any(uow, "results:approve", "exam:schedule")
    return JSONResponse({"items": dump(await ass.sheets(uow.session, institution_id, term_id))})


# --------------------------------------------------------------------------- timetable


@router.get("/institutions/{institution_id}/exam-timetable", summary="The examination timetable")
async def timetable(institution_id: uuid.UUID, term_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await require_any(uow, "exam:schedule", "results:approve", "markbook:edit", "learner:view")
    return JSONResponse({"items": dump(await ass.timetable(uow.session, institution_id, term_id))})


class PaperBody(Model):
    offering_id: uuid.UUID
    term_id: uuid.UUID
    starts_at: datetime
    duration_minutes: int = Field(ge=10, le=360)
    room_id: uuid.UUID | None = None
    invigilator_staff_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=200)


@router.post("/exam-papers", summary="Put a paper on the timetable", status_code=201)
async def schedule(body: PaperBody, request: Request, uow: UowDep) -> JSONResponse:
    await uow.require("exam:schedule")
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    p = await ass.schedule_paper(
        uow.session,
        ass.PaperIn(
            body.offering_id,
            body.term_id,
            body.starts_at,
            body.duration_minutes,
            body.room_id,
            body.invigilator_staff_id,
            body.note,
        ),
    )
    return remember(uow, key, digest, 201, dump(p))


@router.delete("/exam-papers/{paper_id}", summary="Take a paper off the timetable")
async def cancel(paper_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await uow.require("exam:schedule")
    return JSONResponse(dump(await ass.cancel_paper(uow.session, paper_id)))


# --------------------------------------------------------------------------- report cards


async def _class_teacher_or_head(uow: UowDep, class_id: uuid.UUID) -> bool:
    """True for the head (results:approve); class teachers may look; others may not."""
    try:
        await uow.require("results:approve")
        return True
    except Forbidden:
        if await ass.is_class_teacher(uow.session, await my_staff_ids(uow), class_id):
            return False
        raise


@router.get("/classes/{class_id}/results", summary="A class's term results (before publishing)")
async def class_results(class_id: uuid.UUID, term_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await _class_teacher_or_head(uow, class_id)
    return JSONResponse(dump(await ass.class_results(uow.session, class_id, term_id)))


@router.post(
    "/classes/{class_id}/results/{term_id}/publish", summary="Publish the class's report cards"
)
async def publish(
    class_id: uuid.UUID, term_id: uuid.UUID, request: Request, uow: UowDep
) -> JSONResponse:
    await uow.require("results:approve")
    key, digest, replayed = await replay(uow, request, {"c": str(class_id), "t": str(term_id)})
    if replayed:
        return replayed
    r = await ass.publish_reports(uow.session, class_id, term_id)
    return remember(uow, key, digest, 200, {"published": r.published})


class CommentBody(Model):
    class_teacher: str | None = Field(default=None, max_length=500)
    head: str | None = Field(default=None, max_length=500)


@router.put("/learners/{learner_id}/comments/{term_id}", summary="Remarks for the report card")
async def comment(
    learner_id: uuid.UUID, term_id: uuid.UUID, body: CommentBody, uow: UowDep
) -> JSONResponse:
    v = await lrn.get_learner(uow.session, learner_id)
    class_id = v.current.class_id if v.current else None
    head = class_id is not None and await _class_teacher_or_head(uow, class_id)
    if body.head is not None and not head:
        raise Forbidden("the head teacher writes the head's remark")
    await ass.set_comment(
        uow.session, learner_id, term_id, class_teacher=body.class_teacher, head=body.head
    )
    return JSONResponse({"ok": True})


@router.get("/learners/{learner_id}/report-cards", summary="A learner's published report cards")
async def cards(learner_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await may_view_learner(uow, learner_id)
    return JSONResponse({"items": dump(await ass.report_cards(uow.session, learner_id))})


@router.get("/report-cards/{card_id}", summary="A published report card")
async def card(card_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    c = await ass.get_report_card(uow.session, card_id)
    await may_view_learner(uow, c.learner_id)
    return JSONResponse(dump(c))


@router.get("/learners/{learner_id}/course-results", summary="Course results, GPA and CGPA")
async def course_results(learner_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await may_view_learner(uow, learner_id)
    return JSONResponse({"items": dump(await ass.course_results(uow.session, learner_id))})


# --------------------------------------------------------------------------- transcripts


@router.get("/learners/{learner_id}/transcript", summary="Preview a learner's transcript")
async def transcript(learner_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await uow.require("transcript:issue")
    return JSONResponse(await ass.transcript(uow.session, learner_id))


class TranscriptBody(Model):
    purpose: str = Field(min_length=3, max_length=200)


@router.post("/learners/{learner_id}/transcripts", summary="Issue a transcript", status_code=201)
async def issue(
    learner_id: uuid.UUID, body: TranscriptBody, request: Request, uow: UowDep
) -> JSONResponse:
    await uow.require("transcript:issue")
    key, digest, replayed = await replay(uow, request, {"l": str(learner_id), "p": body.purpose})
    if replayed:
        return replayed
    t = await ass.issue_transcript(uow.session, learner_id, body.purpose)
    return remember(uow, key, digest, 201, {"id": str(t.id), "reference": t.reference})


@router.get("/learners/{learner_id}/transcripts", summary="Transcripts issued for a learner")
async def issued(learner_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await may_view_learner(uow, learner_id)
    items = [
        {
            "id": str(t.id),
            "reference": t.reference,
            "purpose": t.purpose,
            "issued_on": t.issued_on.isoformat(),
        }
        for t in await ass.transcripts(uow.session, learner_id)
    ]
    return JSONResponse({"items": items})


@router.get("/transcripts/{transcript_id}", summary="An issued transcript")
async def get_transcript(transcript_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    t = await ass.get_transcript(uow.session, transcript_id)
    await may_view_learner(uow, t.learner_id)
    return JSONResponse(dump(t))


# --------------------------------------------------------------------------- parents


@router.get("/my/children", summary="My children: attendance this term and report cards")
async def my_children(uow: UowDep) -> JSONResponse:
    if not await is_parent_only(uow):
        return JSONResponse({"items": []})
    person = await iam.person_of_user(uow.session, uow.principal.user_id)
    out = []
    for lid in await ass.children_of(uow.session, person):
        v = await lrn.get_learner(uow.session, lid)
        cur = v.current
        term = None
        summary = None
        if cur:
            term = await att.term_on(uow.session, cur.session_id, att.local_now().date())
            if term:
                summary = (await att.learner_summary(uow.session, lid, term.id)).as_dict()
        out.append(
            {
                "learner_id": str(lid),
                "name": f"{v.first_name} {v.surname}",
                "demo": v.demo,
                "school": cur.institution_name if cur else None,
                "class_name": (cur.class_name or cur.year_name) if cur else None,
                "term": term.name if term else None,
                "attendance": summary,
                "report_cards": [
                    {
                        "id": str(c.id),
                        "session": c.content["session"],
                        "term": c.content["term"]["name"],
                        "published_at": c.published_at.isoformat(),
                    }
                    for c in await ass.report_cards(uow.session, lid)
                ],
            }
        )
    return JSONResponse({"items": out})
