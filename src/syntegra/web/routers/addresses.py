"""Gazetteer: addresses of people and institutions (ADR-022)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from syntegra.mdm import gazetteer
from syntegra.web.deps import MAX_LIMIT, UowDep, decode_cursor, encode_cursor, remember, replay
from syntegra.web.schemas import Model

router = APIRouter(prefix="/v1", tags=["addresses (gazetteer)"])


class AddressBody(Model):
    address_type: str = Field(min_length=2, max_length=24, examples=["HOME", "PREMISES"])
    address_line1: str = Field(min_length=2, max_length=255, examples=["14 Faulks Road"])
    address_line2: str | None = Field(default=None, max_length=255)
    address_line3: str | None = Field(default=None, max_length=255, examples=["Aba"])
    address_line4: str | None = Field(default=None, max_length=255)
    address_line5: str | None = Field(default=None, max_length=255)
    local_government_area: str | None = Field(default=None, max_length=64, examples=["Aba North"])
    postcode: str | None = Field(default=None, max_length=16, examples=["450001"])
    zipcode: str | None = Field(default=None, max_length=16, description="Same as postcode")
    paf_key: str | None = Field(default=None, max_length=40)
    address_description: str | None = Field(default=None, max_length=255)
    effective_from: date | None = None

    def data(self) -> gazetteer.AddressIn:
        return gazetteer.AddressIn(
            address_line1=self.address_line1,
            address_line2=self.address_line2,
            address_line3=self.address_line3,
            address_line4=self.address_line4,
            address_line5=self.address_line5,
            local_government_area=self.local_government_area,
            postcode=self.postcode or self.zipcode,
            paf_key=self.paf_key,
            address_description=self.address_description,
        )


class EndBody(Model):
    effective_to: date | None = None


def _address(a: gazetteer.AddressView) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "address_no": a.address_no,
        **{f"address_line{i + 1}": v for i, v in enumerate(a.line_values)},
        "local_government_area": a.local_government_area,
        "state": a.state,
        "country": a.country,
        "postcode": a.postcode,
        "paf_key": a.paf_key,
        "address_description": a.address_description,
        "full": a.one_line,
    }


def _link(v: gazetteer.LinkView) -> dict[str, Any]:
    return {
        "id": str(v.id),
        "party": {"kind": v.party, "id": str(v.party_id)},
        "address_type": {"code": v.address_type, "description": v.address_type_description},
        "effective_from": v.effective_from.isoformat(),
        "effective_to": v.effective_to.isoformat() if v.effective_to else None,
        "current": v.current,
        "address": _address(v.address),
    }


@router.get("/address-types", summary="Kinds of address (home, postal, premises…)")
async def list_types(uow: UowDep) -> dict[str, Any]:
    types = await gazetteer.address_types(uow.session)
    if not types:
        await gazetteer.seed_address_types(uow.session)
        types = await gazetteer.address_types(uow.session)
    return {
        "items": [
            {"code": t.code, "description": t.description, "applies_to": t.applies_to}
            for t in types
        ]
    }


@router.get("/persons/{person_id}/addresses", summary="A person's addresses")
async def person_list(person_id: uuid.UUID, uow: UowDep, history: bool = False) -> dict[str, Any]:
    await uow.require("person:view")
    items = await gazetteer.addresses_of(uow.session, person_id=person_id, include_history=history)
    return {"items": [_link(v) for v in items]}


@router.post(
    "/persons/{person_id}/addresses", status_code=201, summary="Set a person's address of a type"
)
async def person_set(
    person_id: uuid.UUID, body: AddressBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"person": str(person_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("person:edit")
    v = await gazetteer.set_address(
        uow.session,
        body.data(),
        address_type=body.address_type,
        person_id=person_id,
        effective_from=body.effective_from,
    )
    return remember(uow, key, digest, 201, _link(v))


@router.get("/institutions/{inst_id}/addresses", summary="An institution's addresses")
async def institution_list(
    inst_id: uuid.UUID, uow: UowDep, history: bool = False
) -> dict[str, Any]:
    items = await gazetteer.addresses_of(
        uow.session, institution_id=inst_id, include_history=history
    )
    return {"items": [_link(v) for v in items]}


@router.post(
    "/institutions/{inst_id}/addresses",
    status_code=201,
    summary="Set an institution's address of a type (premises, campus, postal…)",
)
async def institution_set(
    inst_id: uuid.UUID, body: AddressBody, request: Request, uow: UowDep
) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"inst": str(inst_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    await uow.require("institution:edit")
    v = await gazetteer.set_address(
        uow.session,
        body.data(),
        address_type=body.address_type,
        institution_id=inst_id,
        effective_from=body.effective_from,
    )
    return remember(uow, key, digest, 201, _link(v))


@router.post("/party-addresses/{link_id}/end", summary="End an address (kept as history)")
async def end(link_id: uuid.UUID, body: EndBody, request: Request, uow: UowDep) -> JSONResponse:
    key, digest, replayed = await replay(
        uow, request, {"link": str(link_id)} | body.model_dump(mode="json")
    )
    if replayed:
        return replayed
    kind = await gazetteer.link_party_kind(uow.session, link_id)
    await uow.require("person:edit" if kind == "person" else "institution:edit")
    v = await gazetteer.end_address(uow.session, link_id, effective_to=body.effective_to)
    return remember(uow, key, digest, 200, _link(v))


@router.get("/addresses/{address_id}", summary="One address")
async def get_one(address_id: uuid.UUID, uow: UowDep) -> dict[str, Any]:
    await uow.require("person:view")
    return _address(await gazetteer.get_address(uow.session, address_id))


@router.get(
    "/person-addresses",
    summary="Person_Address: people with their addresses, by LGA, postcode or type",
)
async def person_addresses(
    uow: UowDep,
    lga: str | None = None,
    postcode: str | None = None,
    address_type: str | None = None,
    current: bool = True,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
) -> dict[str, Any]:
    await uow.require("person:view")
    rows = await gazetteer.person_addresses(
        uow.session,
        local_government_area=lga,
        postcode=postcode,
        address_type=address_type,
        current_only=current,
        after=decode_cursor(cursor),
        limit=limit + 1,
    )
    page = rows[:limit]
    return {
        "items": [
            {
                "person_id": str(r.person_id),
                "first_name": r.first_name,
                "middle_name": r.middle_name,
                "surname": r.surname,
                "date_of_birth": r.date_of_birth.isoformat() if r.date_of_birth else None,
                "address_type": r.address_type,
                **_address(r.address),
                "effective_from": r.effective_from.isoformat(),
                "effective_to": r.effective_to.isoformat() if r.effective_to else None,
            }
            for r in page
        ],
        "next_cursor": encode_cursor(page[-1].link_id) if len(rows) > limit else None,
    }
