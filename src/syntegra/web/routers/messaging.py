"""Secure messaging (CEDA §26, ADR-030).

Every signed-in teacher, administrator, officer and parent can use it: who may message whom is
decided by the messaging rules, not by a permission. Moderation and officer review need
``message:moderate`` and ``message:oversee``; announcement channels need ``message:broadcast``.

Message text never goes into the idempotency store: a retried POST gets back only the ids, and
the client reads the message itself through GET, decrypted on the way out.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from syntegra.core.errors import ValidationFailed
from syntegra.messaging import api as msg
from syntegra.platform.blobstore import BlobStore
from syntegra.web.deps import UowDep, remember, replay
from syntegra.web.routers.common import dump, require_any
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1/messaging", tags=["messaging"])


def _keys(request: Request) -> msg.Keys:
    return msg.Keys(
        institutional=request.app.state.field_crypto.cipher,
        protected=request.app.state.protected_cipher,
    )


def _store(request: Request) -> BlobStore:
    store: BlobStore = request.app.state.document_store
    return store


# --------------------------------------------------------------------------- conversations


@router.get("/conversations", summary="My conversations, most recent first")
async def conversations(request: Request, uow: UowDep) -> JSONResponse:
    return JSONResponse({"items": dump(await msg.my_conversations(uow.session, _keys(request)))})


@router.get("/updates", summary="Poll: unread total and conversations with new messages")
async def updates(request: Request, uow: UowDep, since: datetime | None = None) -> JSONResponse:
    return JSONResponse(dump(await msg.updates(uow.session, _keys(request), since)))


@router.get("/contacts", summary="People I may message, by name")
async def contacts(uow: UowDep, q: str = "") -> JSONResponse:
    return JSONResponse({"items": dump(await msg.contacts(uow.session, q))})


class DirectIn(Model):
    user_id: uuid.UUID


@router.post("/direct", summary="Start or reopen a one-to-one conversation", status_code=200)
async def direct(body: DirectIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"direct": str(body.user_id)})
    if replayed:
        return replayed
    c = await msg.open_direct(uow.session, _keys(request), body.user_id)
    return remember(uow, key, digest, 200, {"id": str(c.id)})


class GroupIn(Model):
    title: str = Field(min_length=2, max_length=120)
    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=1023)
    protected: bool = False
    announcements: bool = False


@router.post("/groups", summary="Create a group (staff)", status_code=201)
async def group(body: GroupIn, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    c = await msg.create_group(
        uow.session,
        _keys(request),
        title=body.title,
        member_ids=body.member_ids,
        protected=body.protected,
        announcements=body.announcements,
    )
    return remember(uow, key, digest, 201, {"id": str(c.id)})


@router.post("/classes/{class_id}/conversation", summary="The class's parents conversation")
async def class_conversation(class_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"class": str(class_id)})
    if replayed:
        return replayed
    c = await msg.open_class_conversation(uow.session, _keys(request), class_id)
    return remember(uow, key, digest, 200, {"id": str(c.id)})


@router.post("/institutions/{institution_id}/staff-room", summary="The school's staff room")
async def staff_room(institution_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"staff": str(institution_id)})
    if replayed:
        return replayed
    c = await msg.open_staff_room(uow.session, _keys(request), institution_id)
    return remember(uow, key, digest, 200, {"id": str(c.id)})


@router.post("/authority-channel", summary="This office's channel with its heads of schools")
async def authority_channel(request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"authority": str(uow.ctx.scope_node_id)})
    if replayed:
        return replayed
    c = await msg.open_authority_channel(uow.session, _keys(request))
    return remember(uow, key, digest, 200, {"id": str(c.id)})


class AnnounceIn(Model):
    audience: Literal["staff", "parents"]
    title: str = Field(min_length=2, max_length=120)
    institution_id: uuid.UUID | None = None


@router.post("/announcements", summary="A one-way announcement channel", status_code=201)
async def announcement(body: AnnounceIn, request: Request, uow: UowDep) -> JSONResponse:
    await uow.require("message:broadcast")
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    c = await msg.announce(
        uow.session,
        _keys(request),
        audience=body.audience,
        title=body.title,
        institution_id=body.institution_id,
    )
    return remember(uow, key, digest, 201, {"id": str(c.id)})


@router.get("/conversations/{conversation_id}", summary="A conversation and its members")
async def conversation(conversation_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    return JSONResponse(
        dump(await msg.get_conversation(uow.session, _keys(request), conversation_id))
    )


class MuteIn(Model):
    muted: bool


@router.put("/conversations/{conversation_id}/mute", summary="Mute or unmute", status_code=204)
async def mute(conversation_id: uuid.UUID, body: MuteIn, uow: UowDep) -> Response:
    await msg.mute(uow.session, conversation_id, body.muted)
    return Response(status_code=204)


@router.delete(
    "/conversations/{conversation_id}/membership", summary="Leave a group", status_code=204
)
async def leave(conversation_id: uuid.UUID, uow: UowDep) -> Response:
    await msg.leave(uow.session, conversation_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- messages


@router.get("/conversations/{conversation_id}/messages", summary="Messages (and mark them read)")
async def messages(
    conversation_id: uuid.UUID,
    request: Request,
    uow: UowDep,
    after: int = 0,
    before: int | None = None,
    limit: int = 50,
) -> JSONResponse:
    items = await msg.messages(
        uow.session,
        _keys(request),
        conversation_id,
        after_seq=after,
        before_seq=before,
        limit=limit,
    )
    return JSONResponse({"items": dump(items)})


@router.post(
    "/conversations/{conversation_id}/messages",
    summary="Send a message, with up to five documents (multipart/form-data)",
    status_code=201,
)
async def send(
    conversation_id: uuid.UUID,
    request: Request,
    uow: UowDep,
    text: str = Form(default="", max_length=msg.MAX_TEXT),
    reply_to: uuid.UUID | None = Form(default=None),  # noqa: B008
    files: list[UploadFile] = File(default=[]),  # noqa: B008
) -> JSONResponse:
    if len(files) > msg.MAX_FILES:
        raise ValidationFailed(f"attach at most {msg.MAX_FILES} documents at a time")
    ins = []
    for f in files:
        data = await f.read(msg.MAX_FILE_BYTES + 1)
        ins.append(
            msg.FileIn(data, (f.content_type or "").split(";")[0].strip(), f.filename or "file")
        )
    key, digest, replayed = await replay(
        uow,
        request,
        {
            "c": str(conversation_id),
            "t": hashlib.sha256(text.encode()).hexdigest(),
            "r": str(reply_to) if reply_to else None,
            "f": [hashlib.sha256(i.content).hexdigest() for i in ins],
        },
    )
    if replayed:
        return replayed
    m = await msg.send(
        uow.session,
        _keys(request),
        _store(request),
        conversation_id,
        text=text,
        files=ins,
        reply_to_id=reply_to,
    )
    return remember(uow, key, digest, 201, {"id": str(m.id), "seq": m.seq})


class EditIn(Model):
    text: str = Field(min_length=1, max_length=msg.MAX_TEXT)


@router.patch("/messages/{message_id}", summary="Edit my message (the earlier text is kept)")
async def edit(message_id: uuid.UUID, body: EditIn, request: Request, uow: UowDep) -> JSONResponse:
    return JSONResponse(dump(await msg.edit(uow.session, _keys(request), message_id, body.text)))


@router.delete("/messages/{message_id}", summary="Delete for everyone (the record is kept)")
async def delete(message_id: uuid.UUID, request: Request, uow: UowDep) -> JSONResponse:
    return JSONResponse(dump(await msg.delete(uow.session, _keys(request), message_id)))


@router.put("/messages/{message_id}/reactions/{emoji}", summary="React")
async def react(message_id: uuid.UUID, emoji: str, request: Request, uow: UowDep) -> JSONResponse:
    v = await msg.react(uow.session, _keys(request), message_id, emoji, on=True)
    return JSONResponse(dump(v))


@router.delete("/messages/{message_id}/reactions/{emoji}", summary="Take back a reaction")
async def unreact(message_id: uuid.UUID, emoji: str, request: Request, uow: UowDep) -> JSONResponse:
    v = await msg.react(uow.session, _keys(request), message_id, emoji, on=False)
    return JSONResponse(dump(v))


def _download(f: msg.FileOut) -> Response:
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in f.name)[:120] or "file"
    return Response(
        f.content,
        media_type=f.mime,
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/attachments/{attachment_id}", summary="Download a document", response_class=Response)
async def attachment(attachment_id: uuid.UUID, request: Request, uow: UowDep) -> Response:
    return _download(
        await msg.attachment(uow.session, _keys(request), _store(request), attachment_id)
    )


class ReportIn(Model):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/messages/{message_id}/report", summary="Report a message", status_code=201)
async def report(
    message_id: uuid.UUID, body: ReportIn, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, {"m": str(message_id), "r": body.reason})
    if replayed:
        return replayed
    rid = await msg.report(uow.session, message_id, body.reason)
    return remember(uow, key, digest, 201, {"id": str(rid)})


# --------------------------------------------------------------------------- moderation, review


@router.get("/reports", summary="The moderation queue")
async def reports(
    uow: UowDep, status: Literal["open", "dismissed", "actioned", "all"] = "open"
) -> JSONResponse:
    await uow.require("message:moderate")
    items = await msg.reports(uow.session, status=None if status == "all" else status)
    return JSONResponse({"items": dump(items)})


class HandleIn(Model):
    action: Literal["dismiss", "remove"]
    note: str | None = Field(default=None, max_length=500)


@router.post("/reports/{report_id}", summary="Dismiss a report, or remove the message")
async def handle(
    report_id: uuid.UUID, body: HandleIn, request: Request, uow: UowDep
) -> JSONResponse:
    await uow.require("message:moderate")
    key, digest, replayed = await replay(uow, request, {"r": str(report_id)} | body.model_dump())
    if replayed:
        return replayed
    await msg.handle_report(uow.session, report_id, action=body.action, note=body.note)
    return remember(uow, key, digest, 200, {"id": str(report_id), "action": body.action})


class ReviewIn(Model):
    purpose: str = Field(min_length=5, max_length=255)


@router.post(
    "/conversations/{conversation_id}/review",
    summary="An authorised officer reads a conversation, for a stated reason (logged)",
)
async def review(
    conversation_id: uuid.UUID, body: ReviewIn, request: Request, uow: UowDep
) -> JSONResponse:
    # The permission is checked at the conversation's own scope inside, and every opening is
    # recorded; the result is not kept in the idempotency store, so no key is needed.
    await require_any(uow, "message:oversee", "message:oversee_protected")
    r = await msg.review(uow.session, _keys(request), conversation_id, purpose=body.purpose)
    return JSONResponse(dump(r))


@router.get(
    "/conversations/{conversation_id}/accesses", summary="Who has reviewed this conversation"
)
async def accesses(conversation_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    await require_any(uow, "message:oversee", "message:oversee_protected", "audit:view")
    return JSONResponse({"items": await msg.accesses(uow.session, conversation_id)})


@router.get(
    "/attachments/{attachment_id}/review",
    summary="An officer downloads a document from a conversation under review",
    response_class=Response,
)
async def attachment_review(
    attachment_id: uuid.UUID, purpose: str, request: Request, uow: UowDep
) -> Response:
    await require_any(uow, "message:oversee", "message:oversee_protected")
    return _download(
        await msg.attachment(
            uow.session, _keys(request), _store(request), attachment_id, purpose=purpose
        )
    )
