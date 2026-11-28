"""Person 360 HTTP API (CEDA §4, §10.4). Identifiers are always masked except via /reveal."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.core.errors import PreconditionRequired
from syntegra.mdm import api as mdm
from syntegra.mdm.domain import ContactKind, IdentifierScheme, NameScript, Sex
from syntegra.platform.crypto import FieldCrypto
from syntegra.web.deps import MAX_LIMIT, UowDep, decode_cursor, encode_cursor, remember, replay
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["persons"])

Name = Field(min_length=1, max_length=100)


def _crypto(request: Request) -> FieldCrypto:
    return request.app.state.field_crypto  # type: ignore[no-any-return]


class ContactBody(Model):
    kind: ContactKind
    value: str = Field(min_length=3, max_length=254)
    is_primary: bool = False


class IdentifierBody(Model):
    scheme: IdentifierScheme
    value: str = Field(min_length=4, max_length=32)


class PersonBody(Model):
    given_name: str = Name
    family_name: str = Name
    other_names: str | None = Field(default=None, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=100)
    name_script: NameScript = NameScript.LATIN
    date_of_birth: date | None = None
    sex_at_registration: Sex = Sex.NOT_RECORDED
    nationality: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    primary_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    contacts: list[ContactBody] = Field(default=[], max_length=10)
    identifiers: list[IdentifierBody] = Field(default=[], max_length=10)
    source_system: str | None = Field(default=None, max_length=64)
    source_ref: str | None = Field(default=None, max_length=128)
    scope_node_id: uuid.UUID | None = None

    def to_input(self) -> mdm.PersonIn:
        return mdm.PersonIn(
            given_name=self.given_name,
            family_name=self.family_name,
            other_names=self.other_names,
            preferred_name=self.preferred_name,
            name_script=self.name_script,
            date_of_birth=self.date_of_birth,
            sex_at_registration=self.sex_at_registration,
            nationality=self.nationality,
            primary_language=self.primary_language,
            contacts=tuple(mdm.ContactIn(c.kind, c.value, c.is_primary) for c in self.contacts),
            identifiers=tuple(mdm.IdentifierIn(i.scheme, i.value) for i in self.identifiers),
            source_system=self.source_system,
            source_ref=self.source_ref,
            scope_node_id=self.scope_node_id,
        )


class PersonCreate(PersonBody):
    # A data steward may create despite a blocking match by naming each one, with a reason.
    not_duplicate_of: list[uuid.UUID] = Field(default=[], max_length=5)
    override_reason: str | None = Field(default=None, max_length=500)


class PersonPatch(Model):
    given_name: str | None = Field(default=None, min_length=1, max_length=100)
    family_name: str | None = Field(default=None, min_length=1, max_length=100)
    other_names: str | None = Field(default=None, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=100)
    name_script: NameScript | None = None
    date_of_birth: date | None = None
    sex_at_registration: Sex | None = None
    nationality: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    primary_language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")


class Decision(Model):
    decision: Literal["same_person", "different_people"]
    note: str = Field(min_length=3, max_length=500)
    survivor_id: uuid.UUID | None = None


class MergeBody(Model):
    merged_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)


class ReasonBody(Model):
    reason: str = Field(min_length=3, max_length=500)


def _view_json(v: mdm.PersonView) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "scope_node_id": str(v.scope_node_id),
        "status": v.status,
        "row_version": v.row_version,
        "given_name": v.given_name,
        "family_name": v.family_name,
        "other_names": v.other_names,
        "preferred_name": v.preferred_name,
        "name_script": v.name_script,
        "date_of_birth": v.date_of_birth.isoformat() if v.date_of_birth else None,
        "sex_at_registration": v.sex_at_registration,
        "nationality": v.nationality,
        "primary_language": v.primary_language,
        "source_system": v.source_system,
        "identifiers": [
            {
                "id": str(i.id),
                "scheme": i.scheme,
                "masked": i.masked,
                "verified": i.verified_at is not None,
            }
            for i in v.identifiers
        ],
        "contacts": [
            {"id": str(c.id), "kind": c.kind, "value": c.value, "is_primary": c.is_primary}
            for c in v.contacts
        ],
        "other_recorded_names": v.other_recorded_names,
        "merged_from": [str(m) for m in v.merged_from],
        "requested_id": str(v.requested_id) if v.requested_id else None,
        "quality": v.quality,
    }


def _etag(v: mdm.PersonView) -> dict[str, str]:
    return {"ETag": f'"{v.row_version}"'}


async def _require_on_person(uow: UowDep, person_id: uuid.UUID, permission: str) -> mdm.PersonView:
    view = await mdm.get_person(uow.session, person_id)
    await uow.require(permission, view.scope_node_id)
    return view


@router.post("/persons", status_code=201, summary="Register a person (with duplicate check)")
async def create_person(body: PersonCreate, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(uow, request, body.model_dump(mode="json"))
    if replayed:
        return replayed
    await uow.require("person:create", body.scope_node_id)
    if body.not_duplicate_of:
        await uow.require(
            "person:merge", body.scope_node_id
        )  # overriding a block is a steward decision
    result = await mdm.create_person(
        uow.session,
        _crypto(request),
        body.to_input(),
        not_duplicate_of=body.not_duplicate_of,
        override_reason=body.override_reason,
    )
    view = await mdm.get_person(uow.session, result.person_id)
    out = _view_json(view) | {
        "possible_duplicates": [
            mdm._candidate_json(c) for c in result.candidates if c.outcome.value == "review"
        ],
        "review_ids": [str(r) for r in result.review_ids],
    }
    return remember(uow, key, digest, 201, out)


@router.post(
    "/persons/match", summary="Check for existing records before registering (saves nothing)"
)
async def match_preview(body: PersonBody, request: Request, uow: UowDep) -> dict[str, Any]:
    await uow.require("person:create", body.scope_node_id)
    candidates = await mdm.find_candidates(uow.session, _crypto(request), body.to_input())
    return {"candidates": [mdm._candidate_json(c) for c in candidates[:5]]}


@router.get(
    "/persons/{person_id}", summary="Person 360 record (merged ids resolve to the survivor)"
)
async def get_person(person_id: uuid.UUID, uow: UowDep) -> JSONResponse:
    view = await _require_on_person(uow, person_id, "person:view")
    return JSONResponse(_view_json(view), headers=_etag(view))


@router.patch("/persons/{person_id}", summary="Edit a person (If-Match required)")
async def patch_person(
    person_id: uuid.UUID, body: PersonPatch, request: Request, uow: UowDep
) -> JSONResponse:
    match = request.headers.get("If-Match", "").strip().strip('"').removeprefix('W/"')
    if not match.isdigit():
        raise PreconditionRequired(
            "send If-Match with the record's ETag",
            next_action="Read the person first and send its ETag back in If-Match.",
        )
    await _require_on_person(uow, person_id, "person:edit")
    changes = body.model_dump(exclude_unset=True)
    view = await mdm.update_person(
        uow.session, person_id, expected_version=int(match), changes=changes
    )
    return JSONResponse(_view_json(view), headers=_etag(view))


@router.post(
    "/persons/{person_id}/identifiers", status_code=201, summary="Add a national or exam identifier"
)
async def add_identifier(
    person_id: uuid.UUID, body: IdentifierBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, body.model_dump(mode="json") | {"person": str(person_id)}
    )
    if replayed:
        return replayed
    await _require_on_person(uow, person_id, "person:edit")
    iid = await mdm.add_identifier(
        uow.session, _crypto(request), person_id, mdm.IdentifierIn(body.scheme, body.value)
    )
    return remember(uow, key, digest, 201, {"id": str(iid)})


@router.post(
    "/persons/{person_id}/identifiers/{identifier_id}/reveal",
    summary="Show an identifier in full (purpose required, audited)",
)
async def reveal_identifier(
    person_id: uuid.UUID, identifier_id: uuid.UUID, request: Request, uow: UowDep
) -> JSONResponse:
    view = await mdm.get_person(uow.session, person_id)
    await uow.require("learner_national_id:reveal", view.scope_node_id)
    value = await mdm.reveal_identifier(uow.session, _crypto(request), view.id, identifier_id)
    return JSONResponse({"value": value}, headers={"Cache-Control": "no-store"})


@router.post(
    "/persons/{person_id}/contacts", status_code=201, summary="Add a phone number or email"
)
async def add_contact(
    person_id: uuid.UUID, body: ContactBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, body.model_dump(mode="json") | {"person": str(person_id)}
    )
    if replayed:
        return replayed
    await _require_on_person(uow, person_id, "person:edit")
    cid = await mdm.add_contact(
        uow.session, person_id, mdm.ContactIn(body.kind, body.value, body.is_primary)
    )
    return remember(uow, key, digest, 201, {"id": str(cid)})


@router.get("/match-reviews", summary="Possible duplicates waiting for a decision")
async def list_reviews(
    uow: UowDep,
    status: Literal["open", "same_person", "different_people"] = "open",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> dict[str, Any]:
    await uow.require("person:merge")
    rows = await mdm.list_reviews(
        uow.session, status=status, after=decode_cursor(cursor), limit=limit + 1
    )
    page = rows[:limit]
    return {
        "items": [
            {
                "id": str(r.id),
                "person_id": str(r.person_id),
                "candidate_id": str(r.candidate_id),
                "score": r.score,
                "reasons": r.reasons,
                "status": r.status,
            }
            for r in page
        ],
        "next_cursor": encode_cursor(page[-1].id) if len(rows) > limit else None,
    }


@router.post("/match-reviews/{review_id}/decision", summary="Decide a possible duplicate")
async def decide_review(
    review_id: uuid.UUID, body: Decision, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, body.model_dump(mode="json") | {"review": str(review_id)}
    )
    if replayed:
        return replayed
    await uow.require("person:merge")
    merge_id = await mdm.decide_review(
        uow.session,
        review_id,
        same_person=body.decision == "same_person",
        note=body.note,
        survivor_id=body.survivor_id,
    )
    return remember(
        uow,
        key,
        digest,
        200,
        {"decision": body.decision, "merge_id": str(merge_id) if merge_id else None},
    )


@router.post(
    "/persons/{person_id}/merge", status_code=201, summary="Merge another record into this one"
)
async def merge(
    person_id: uuid.UUID, body: MergeBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, body.model_dump(mode="json") | {"survivor": str(person_id)}
    )
    if replayed:
        return replayed
    await _require_on_person(uow, person_id, "person:merge")
    await _require_on_person(uow, body.merged_id, "person:merge")
    mid = await mdm.merge_persons(
        uow.session, survivor_id=person_id, merged_id=body.merged_id, reason=body.reason
    )
    return remember(uow, key, digest, 201, {"merge_id": str(mid)})


@router.post("/person-merges/{merge_id}/reverse", summary="Reverse a merge")
async def reverse_merge(
    merge_id: uuid.UUID, body: ReasonBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, body.model_dump(mode="json") | {"merge": str(merge_id)}
    )
    if replayed:
        return replayed
    await uow.require("person:merge")
    kept = await mdm.unmerge(uow.session, merge_id, reason=body.reason)
    return remember(uow, key, digest, 200, {"reversed": True, "survivor_changes_kept": kept})
