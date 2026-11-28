"""Gazetteer: one address register for people and organisations (ADR-022).

    Address Type ─┐
                  ├── Party Address (from … to) ──► Address (lines 1-5, LGA, state, postcode,
    Party ────────┘     person or institution          PAF key, description)

* **Address** is a physical place, held once per scope and shared by every party at it: a family's
  home is one row that each child and guardian links to. It is checked against the jurisdiction's
  structure file (local government areas, postcode format, required parts).
* **Party Address** links a person or an institution to an address with a type and an effective
  period. A party has one current address of each type. Moving ends the old link and starts a new
  one, so the history is kept, not overwritten.
* **Person_Address** (``person_addresses``) is the joined view of names, date of birth and current
  addresses.

Addresses are personal data: they live in their party's scope, so a learner's home address is
visible where the learner is, and nowhere else.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today
from syntegra.core.context import current_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.governance import api as governance
from syntegra.institutions import events as ins_events
from syntegra.institutions.domain import fold, normalize_postcode, unit_key
from syntegra.institutions.models import Institution
from syntegra.mdm import events
from syntegra.mdm.models import Address, AddressType, PartyAddress, Person
from syntegra.platform.outbox import publish
from syntegra.platform.sequence import next_value

LINES = ("address_line1", "address_line2", "address_line3", "address_line4", "address_line5")

DEFAULT_TYPES: tuple[tuple[str, str, str], ...] = (
    ("HOME", "Home (where the person lives)", "person"),
    ("POSTAL", "Postal", "any"),
    ("CORRESPONDENCE", "Correspondence", "any"),
    ("TEMPORARY", "Temporary or term-time", "person"),
    ("PREMISES", "Main premises", "organisation"),
    ("CAMPUS", "Campus or other site", "organisation"),
    ("BILLING", "Billing", "any"),
)


@dataclass(frozen=True, slots=True)
class AddressIn:
    address_line1: str
    address_line2: str | None = None
    address_line3: str | None = None
    address_line4: str | None = None
    address_line5: str | None = None
    local_government_area: str | None = None
    postcode: str | None = None
    paf_key: str | None = None
    address_description: str | None = None


@dataclass(frozen=True, slots=True)
class AddressView:
    id: uuid.UUID
    address_no: int
    line_values: tuple[str | None, ...]  # lines 1-5 in place, blanks included
    local_government_area: str | None
    state: str | None
    country: str | None
    postcode: str | None
    paf_key: str | None
    address_description: str | None

    @property
    def lines(self) -> list[str]:
        return [v for v in self.line_values if v]

    @property
    def one_line(self) -> str:
        tail = [p for p in (self.local_government_area, self.state) if p]
        place = ", ".join([*self.lines, *tail])
        if self.postcode:
            place += f" {self.postcode}"
        return place + (f", {self.country}" if self.country else "")


@dataclass(frozen=True, slots=True)
class LinkView:
    id: uuid.UUID
    party: str  # person | organisation
    party_id: uuid.UUID
    address_type: str
    address_type_description: str
    effective_from: date
    effective_to: date | None
    address: AddressView

    @property
    def current(self) -> bool:
        return self.effective_to is None or self.effective_to >= today()


# --------------------------------------------------------------------------- address types


async def seed_address_types(session: AsyncSession) -> list[str]:
    """Add the standard address types (idempotent). Returns the codes added."""
    have = set((await session.execute(select(AddressType.code))).scalars())
    added = []
    for code, description, applies in DEFAULT_TYPES:
        if code not in have:
            session.add(AddressType(code=code, description=description, applies_to=applies))
            added.append(code)
    await session.flush()
    return added


async def address_types(session: AsyncSession) -> list[AddressType]:
    return list(
        (
            await session.execute(
                select(AddressType).where(AddressType.active.is_(True)).order_by(AddressType.code)
            )
        ).scalars()
    )


async def _type(session: AsyncSession, code: str, party: str) -> AddressType:
    stmt = select(AddressType).where(
        AddressType.code == code.strip().upper(), AddressType.active.is_(True)
    )
    found = (await session.execute(stmt)).scalar_one_or_none()
    if found is None and code.strip().upper() in {c for c, _, _ in DEFAULT_TYPES}:
        await seed_address_types(session)  # a new installation: add the standard types once
        found = (await session.execute(stmt)).scalar_one_or_none()
    if found is None:
        codes = ", ".join(t.code for t in await address_types(session))
        raise ValidationFailed(f"address type '{code}' is not known", next_action=f"Use {codes}.")
    if found.applies_to not in ("any", party):
        raise ValidationFailed(f"a {found.code} address is not for a {party}")
    return found


# --------------------------------------------------------------------------- addresses


async def _rules(session: AsyncSession, scope: uuid.UUID) -> tuple[dict[str, Any], list[str]]:
    rules = await governance.resolve_config(session, scope, "address.rules") or {}
    areas = await governance.resolve_config(session, scope, "location.areas") or []
    return rules, list(areas)


def _clean(v: str | None, limit: int, label: str, errors: list[str]) -> str | None:
    v = re.sub(r"\s+", " ", (v or "").strip())
    if len(v) > limit:
        errors.append(f"{label} must be at most {limit} characters")
    return v or None


async def _checked(session: AsyncSession, data: AddressIn, scope: uuid.UUID) -> dict[str, Any]:
    rules, areas = await _rules(session, scope)
    errors: list[str] = []
    out: dict[str, Any] = {
        line: _clean(getattr(data, line), 255, line.replace("_", " "), errors) for line in LINES
    }
    if not out["address_line1"]:
        errors.append("address line 1 is required")
    lga = _clean(data.local_government_area, 64, "local government area", errors)
    if lga and areas:
        known = {unit_key(a)[0]: a for a in areas} | {unit_key(a)[1]: a for a in areas}
        match = next((known[k] for k in unit_key(lga) if k in known), None)
        if match is None:
            errors.append(f"'{lga}' is not a local government area of this state")
        lga = match
    if not lga and "lga" in (rules.get("required") or []):
        errors.append("local government area is required")
    postcode = normalize_postcode(data.postcode) if data.postcode else None
    pattern = rules.get("postcode_pattern")
    if postcode and pattern:
        postcode = next(
            (c for c in (postcode, postcode.replace(" ", "")) if re.fullmatch(pattern, c)), None
        )
        if postcode is None:
            label = rules.get("postcode_label") or "postcode"
            example = rules.get("postcode_example")
            errors.append(
                f"{label} '{data.postcode}' is not in the expected format"
                + (f", e.g. {example}" if example else "")
            )
    if not postcode and "postcode" in (rules.get("required") or []):
        errors.append(f"{rules.get('postcode_label') or 'postcode'} is required")
    paf = _clean(data.paf_key, 40, "PAF key", errors)
    if paf and not re.fullmatch(r"[A-Za-z0-9/\-. ]+", paf):
        errors.append("PAF key has unexpected characters")
    description = _clean(data.address_description, 255, "address description", errors)
    if errors:
        raise ValidationFailed("; ".join(errors), next_action="Correct the address and try again.")
    out |= {
        "local_government_area": lga,
        "lga_key": fold(lga) if lga else None,
        "state": rules.get("state"),
        "country": rules.get("country"),
        "postcode": postcode,
        "paf_key": paf.upper() if paf else None,
        "address_description": description,
    }
    key = "|".join(fold(out[k]) for k in (*LINES, "local_government_area", "postcode"))
    out["address_key"] = hashlib.sha256(key.encode()).digest()
    return out


def _view(a: Address) -> AddressView:
    return AddressView(
        a.id,
        a.address_no,
        tuple(getattr(a, line) for line in LINES),
        a.local_government_area,
        a.state,
        a.country,
        a.postcode,
        a.paf_key,
        a.address_description,
    )


async def find_or_create(session: AsyncSession, data: AddressIn, scope: uuid.UUID) -> Address:
    """The address within this scope, created if new. The same place typed the same way (case,
    accents, punctuation aside) is one address, shared."""
    ctx = current_context()
    fields = await _checked(session, data, scope)
    found = (
        await session.execute(
            select(Address).where(
                Address.scope_node_id == scope, Address.address_key == fields["address_key"]
            )
        )
    ).scalar_one_or_none()
    if found is not None:
        if fields["paf_key"] and not found.paf_key:
            found.paf_key = fields["paf_key"]
        if fields["address_description"] and not found.address_description:
            found.address_description = fields["address_description"]
        return found
    seq = await next_value(session, ctx.tenant_id, "address")
    row = Address(tenant_id=ctx.tenant_id, scope_node_id=scope, address_no=seq, **fields)
    session.add(row)
    await session.flush()
    return row


async def get_address(session: AsyncSession, address_id: uuid.UUID) -> AddressView:
    a = (
        await session.execute(select(Address).where(Address.id == address_id))
    ).scalar_one_or_none()
    if a is None:
        raise NotFound("address not found in your scope")
    return _view(a)


# --------------------------------------------------------------------------- party links


async def _party(
    session: AsyncSession, *, person_id: uuid.UUID | None, institution_id: uuid.UUID | None
) -> tuple[str, uuid.UUID, uuid.UUID]:
    """(kind, id, scope) of a party the caller may see."""
    if (person_id is None) == (institution_id is None):
        raise ValidationFailed("give a person or an organisation")
    if person_id is not None:
        p = (
            await session.execute(select(Person).where(Person.id == person_id))
        ).scalar_one_or_none()
        if p is None or p.status != "active":
            raise NotFound("person not found in your scope")
        return "person", p.id, p.scope_node_id
    i = (
        await session.execute(select(Institution).where(Institution.id == institution_id))
    ).scalar_one_or_none()
    if i is None:
        raise NotFound("institution not found in your scope")
    return "organisation", i.id, i.scope_node_id


def _cond(kind: str, party_id: uuid.UUID) -> Any:
    return (
        PartyAddress.person_id == party_id
        if kind == "person"
        else PartyAddress.institution_id == party_id
    )


async def _publish(session: AsyncSession, kind: str, row: PartyAddress, event: str) -> None:
    ctx = current_context()
    party_id = row.person_id or row.institution_id
    assert party_id is not None
    publish(
        session,
        EventEnvelope(
            event_type=event,
            tenant_id=row.tenant_id,
            scope_node_id=row.scope_node_id,
            aggregate_type="person" if kind == "person" else "institution",
            aggregate_id=party_id,
            actor_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
            payload={"link_id": str(row.id)},
        ),
    )


async def set_address(
    session: AsyncSession,
    data: AddressIn,
    *,
    address_type: str,
    person_id: uuid.UUID | None = None,
    institution_id: uuid.UUID | None = None,
    effective_from: date | None = None,
) -> LinkView:
    """Give a party its address of one type from a date. A current address of the same type ends
    the day before; the same address again changes nothing."""
    kind, party_id, scope = await _party(
        session, person_id=person_id, institution_id=institution_id
    )
    atype = await _type(session, address_type, kind)
    start = effective_from or today()
    address = await find_or_create(session, data, scope)
    current = (
        (
            await session.execute(
                select(PartyAddress).where(
                    _cond(kind, party_id),
                    PartyAddress.address_type_id == atype.id,
                    (PartyAddress.effective_to.is_(None)) | (PartyAddress.effective_to >= start),
                )
            )
        )
        .scalars()
        .all()
    )
    for old in current:
        if old.address_id == address.id:
            return _link_view(old, kind, atype, address)  # nothing changed
        if old.effective_from >= start:
            raise Conflict(
                f"this {atype.code} address already starts on or after {start.isoformat()}",
                next_action="Give a later start date, or end that address first.",
            )
        old.effective_to = start - timedelta(days=1)
        await _publish(session, kind, old, _ended(kind))
    row = PartyAddress(
        tenant_id=address.tenant_id,
        scope_node_id=scope,
        address_id=address.id,
        person_id=party_id if kind == "person" else None,
        institution_id=party_id if kind == "organisation" else None,
        address_type_id=atype.id,
        effective_from=start,
    )
    session.add(row)
    await session.flush()
    await _publish(session, kind, row, _linked(kind))
    await session.flush()
    return _link_view(row, kind, atype, address)


def _linked(kind: str) -> str:
    return events.ADDRESS_LINKED if kind == "person" else ins_events.ADDRESS_LINKED


def _ended(kind: str) -> str:
    return events.ADDRESS_ENDED if kind == "person" else ins_events.ADDRESS_ENDED


def _link_view(row: PartyAddress, kind: str, atype: AddressType, address: Address) -> LinkView:
    party_id = row.person_id or row.institution_id
    assert party_id is not None
    return LinkView(
        row.id,
        kind,
        party_id,
        atype.code,
        atype.description,
        row.effective_from,
        row.effective_to,
        _view(address),
    )


async def link_party_kind(session: AsyncSession, link_id: uuid.UUID) -> str:
    row = (
        await session.execute(select(PartyAddress).where(PartyAddress.id == link_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("address link not found in your scope")
    return "person" if row.person_id else "organisation"


async def end_address(
    session: AsyncSession, link_id: uuid.UUID, *, effective_to: date | None = None
) -> LinkView:
    """The party no longer has this address (kept as history)."""
    row = (
        await session.execute(select(PartyAddress).where(PartyAddress.id == link_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("address link not found in your scope")
    end = effective_to or today()
    if row.effective_to is not None and row.effective_to < end:
        raise Conflict("this address has already ended")
    if end < row.effective_from:
        raise ValidationFailed("an address cannot end before it starts")
    row.effective_to = end
    kind = "person" if row.person_id else "organisation"
    await _publish(session, kind, row, _ended(kind))
    await session.flush()
    atype = (
        await session.execute(select(AddressType).where(AddressType.id == row.address_type_id))
    ).scalar_one()
    address = (
        await session.execute(select(Address).where(Address.id == row.address_id))
    ).scalar_one()
    return _link_view(row, kind, atype, address)


async def addresses_of(
    session: AsyncSession,
    *,
    person_id: uuid.UUID | None = None,
    institution_id: uuid.UUID | None = None,
    include_history: bool = False,
) -> list[LinkView]:
    kind, party_id, _scope = await _party(
        session, person_id=person_id, institution_id=institution_id
    )
    rows = (
        await session.execute(
            select(PartyAddress, AddressType, Address)
            .join(AddressType, AddressType.id == PartyAddress.address_type_id)
            .join(Address, Address.id == PartyAddress.address_id)
            .where(_cond(kind, party_id))
            .order_by(AddressType.code, PartyAddress.effective_from.desc())
        )
    ).all()
    out = [_link_view(r, kind, t, a) for r, t, a in rows]
    return out if include_history else [v for v in out if v.current]


# --------------------------------------------------------------------------- Person_Address


@dataclass(frozen=True, slots=True)
class PersonAddressRow:
    """The diagram's Person_Address: who, and where, of one type, over a period."""

    link_id: uuid.UUID
    person_id: uuid.UUID
    first_name: str
    middle_name: str | None
    surname: str
    date_of_birth: date | None
    address_type: str
    address: AddressView
    effective_from: date
    effective_to: date | None


async def person_addresses(
    session: AsyncSession,
    *,
    local_government_area: str | None = None,
    postcode: str | None = None,
    address_type: str | None = None,
    current_only: bool = True,
    after: uuid.UUID | None = None,
    limit: int = 100,
) -> list[PersonAddressRow]:
    stmt = (
        select(PartyAddress, Person, AddressType, Address)
        .join(Person, Person.id == PartyAddress.person_id)
        .join(AddressType, AddressType.id == PartyAddress.address_type_id)
        .join(Address, Address.id == PartyAddress.address_id)
        .where(Person.status == "active")
        .order_by(PartyAddress.id)
        .limit(limit)
    )
    if local_government_area:
        stmt = stmt.where(Address.lga_key == fold(local_government_area))
    if postcode:
        stmt = stmt.where(Address.postcode == normalize_postcode(postcode).replace(" ", ""))
    if address_type:
        stmt = stmt.where(AddressType.code == address_type.strip().upper())
    if current_only:
        stmt = stmt.where(
            (PartyAddress.effective_to.is_(None)) | (PartyAddress.effective_to >= today())
        )
    if after is not None:
        stmt = stmt.where(PartyAddress.id > after)
    return [
        PersonAddressRow(
            link.id,
            p.id,
            p.given_name,
            p.other_names,
            p.family_name,
            p.date_of_birth,
            t.code,
            _view(a),
            link.effective_from,
            link.effective_to,
        )
        for link, p, t, a in (await session.execute(stmt)).all()
    ]


# --------------------------------------------------------------------------- institutions


async def link_institution_premises(session: AsyncSession, inst: Institution) -> None:
    """An institution's register address becomes its PREMISES address in the gazetteer."""
    if not inst.address_line1:
        return
    await set_address(
        session,
        AddressIn(
            address_line1=inst.address_line1,
            address_line2=inst.address_line2,
            address_line3=inst.town,
            local_government_area=inst.location,
            postcode=inst.postcode,
        ),
        address_type="PREMISES",
        institution_id=inst.id,
    )
