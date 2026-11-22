"""Person 360 services: create with duplicate detection, read, update, identifiers, contacts,
match reviews, merge and un-merge (CEDA §4, §6.2, §8.1, §9.1, §16)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import today, utcnow
from syntegra.core.context import current_context, use_context
from syntegra.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import mask
from syntegra.governance import api as governance
from syntegra.mdm import events
from syntegra.mdm.domain import (
    ContactKind,
    FieldDecision,
    IdentifierScheme,
    MatchOutcome,
    MatchScore,
    MatchThresholds,
    NameScript,
    PersonFacts,
    QualityInput,
    Sex,
    SourcedRecord,
    assess_quality,
    blocking_key,
    ensure_mergeable,
    name_tokens,
    normalize_contact,
    normalize_identifier,
    phonetic_keys,
    score_match,
    survive,
)
from syntegra.mdm.models import (
    ExternalIdentifier,
    MatchReview,
    Person,
    PersonContact,
    PersonMerge,
    PersonName,
)
from syntegra.platform.crypto import FieldCrypto
from syntegra.platform.outbox import publish
from syntegra.platform.session import PLATFORM_BYPASS, append_audit

MAX_CANDIDATES = 200
SHOWN_CANDIDATES = 5
IDENTIFIER_TABLE = "mdm.external_identifier"


# --------------------------------------------------------------------------- inputs and views


@dataclass(frozen=True, slots=True)
class ContactIn:
    kind: ContactKind
    value: str
    is_primary: bool = False


@dataclass(frozen=True, slots=True)
class IdentifierIn:
    scheme: IdentifierScheme
    value: str
    verified_by_adapter: str | None = None


@dataclass(frozen=True, slots=True)
class PersonIn:
    given_name: str
    family_name: str
    other_names: str | None = None
    preferred_name: str | None = None
    name_script: NameScript = NameScript.LATIN
    date_of_birth: date | None = None
    sex_at_registration: Sex = Sex.NOT_RECORDED
    nationality: str | None = None
    primary_language: str | None = None
    contacts: tuple[ContactIn, ...] = ()
    identifiers: tuple[IdentifierIn, ...] = ()
    source_system: str | None = None
    source_ref: str | None = None
    scope_node_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """Internal match result. ``person_id`` is always set; when ``visible`` is false the
    candidate is outside the caller's scope and must be shown without details (P6)."""

    person_id: uuid.UUID
    score: int
    outcome: MatchOutcome
    reasons: list[dict[str, Any]]  # empty when not visible
    display_name: str | None
    date_of_birth: date | None
    visible: bool
    reasons_full: list[dict[str, Any]] = field(default_factory=list, repr=False)


@dataclass(frozen=True, slots=True)
class CreateResult:
    person_id: uuid.UUID
    review_ids: list[uuid.UUID]
    candidates: list[Candidate]


class DuplicateBlocked(Conflict):
    """Creation stopped: an existing record matches above the auto-link threshold (§4)."""

    type_slug = "possible-duplicate"
    title = "This person may already be registered"


@dataclass(frozen=True, slots=True)
class IdentifierView:
    id: uuid.UUID
    scheme: str
    masked: str
    verified_at: Any


@dataclass(frozen=True, slots=True)
class ContactView:
    id: uuid.UUID
    kind: str
    value: str
    is_primary: bool
    verified_at: Any


@dataclass
class PersonView:
    id: uuid.UUID
    tenant_id: uuid.UUID
    scope_node_id: uuid.UUID
    given_name: str
    family_name: str
    other_names: str | None
    preferred_name: str | None
    name_script: str
    date_of_birth: date | None
    sex_at_registration: str
    nationality: str | None
    primary_language: str | None
    status: str
    row_version: int
    quality: dict[str, Any] | None
    identifiers: list[IdentifierView] = field(default_factory=list)
    contacts: list[ContactView] = field(default_factory=list)
    other_recorded_names: list[dict[str, Any]] = field(default_factory=list)
    merged_from: list[uuid.UUID] = field(default_factory=list)
    requested_id: uuid.UUID | None = None  # set when the requested id was merged into this one
    source_system: str | None = None


# --------------------------------------------------------------------------- helpers


DEMO_SOURCE = "demo"


def is_demo(source_system: str | None) -> bool:
    """A demonstration record (ADR-031): made up for a demonstration, shown as such everywhere."""
    return source_system == DEMO_SOURCE


def _ctx_actor() -> uuid.UUID | None:
    return current_context().user_id


def _event(event_type: str, person: Person, payload: dict[str, Any] | None = None) -> EventEnvelope:
    ctx = current_context()
    return EventEnvelope(
        event_type=event_type,
        tenant_id=person.tenant_id,
        scope_node_id=person.scope_node_id,
        aggregate_type="person",
        aggregate_id=person.id,
        actor_id=ctx.user_id,
        correlation_id=ctx.correlation_id,
        payload=payload or {},
    )


async def thresholds(session: AsyncSession, scope_node_id: uuid.UUID) -> MatchThresholds:
    """Configurable per jurisdiction through the scope hierarchy (P4)."""
    auto = await governance.resolve_config(session, scope_node_id, "mdm.match.auto_link")
    review = await governance.resolve_config(session, scope_node_id, "mdm.match.review")
    return MatchThresholds(
        int(auto) if auto is not None else 85, int(review) if review is not None else 60
    )


def _set_keys(p: Person) -> None:
    p.given_key = blocking_key(p.given_name)
    p.family_key = blocking_key(p.family_name)


def _identifier_digest(
    crypto: FieldCrypto, scheme: IdentifierScheme | str, normalized: str
) -> bytes:
    return crypto.index.digest(f"id:{scheme}", normalized)


def _bypass() -> dict[str, Any]:
    return {PLATFORM_BYPASS: True}


async def _get_person(session: AsyncSession, person_id: uuid.UUID) -> Person:
    # Tenant- and scope-filtered by the session guard.
    p = (await session.execute(select(Person).where(Person.id == person_id))).scalar_one_or_none()
    if p is None:
        raise NotFound("person not found")
    return p


async def _facts_for(
    session: AsyncSession, people: Sequence[Person]
) -> dict[uuid.UUID, list[PersonFacts]]:
    """Comparable facts per person, one entry per recorded name (current, previous, alias)."""
    ids = [p.id for p in people]
    if not ids:
        return {}
    idents: dict[uuid.UUID, dict[str, bytes]] = {i: {} for i in ids}
    for row in (
        await session.execute(
            select(ExternalIdentifier)
            .where(ExternalIdentifier.person_id.in_(ids))
            .execution_options(**_bypass())
        )
    ).scalars():
        idents[row.person_id][row.scheme] = row.value_hmac
    contacts: dict[uuid.UUID, set[str]] = {i: set() for i in ids}
    for c in (
        await session.execute(
            select(PersonContact)
            .where(PersonContact.person_id.in_(ids), PersonContact.valid_to.is_(None))
            .execution_options(**_bypass())
        )
    ).scalars():
        contacts[c.person_id].add(c.value)
    alt: dict[uuid.UUID, list[PersonName]] = {i: [] for i in ids}
    for n in (
        await session.execute(
            select(PersonName).where(PersonName.person_id.in_(ids)).execution_options(**_bypass())
        )
    ).scalars():
        alt[n.person_id].append(n)
    out: dict[uuid.UUID, list[PersonFacts]] = {}
    for p in people:
        sex = Sex(p.sex_at_registration) if p.sex_at_registration else None
        names = [(p.given_name, p.family_name, p.other_names)]
        names += [(n.given_name, n.family_name, n.other_names) for n in alt[p.id]]
        facts = [
            PersonFacts(
                given_name=g,
                family_name=f,
                other_names=o,
                date_of_birth=p.date_of_birth,
                sex=sex,
                identifiers=idents[p.id],
                contacts=frozenset(contacts[p.id]),
            )
            for g, f, o in names
        ]
        out[p.id] = facts
    return out


async def find_candidates(
    session: AsyncSession,
    crypto: FieldCrypto,
    data: PersonIn,
    *,
    exclude: set[uuid.UUID] | None = None,
) -> list[Candidate]:
    """Score existing people in the caller's organisation against ``data`` (§4).

    Retrieval is organisation-wide so that duplicates across campuses are caught; candidates
    outside the caller's active scope are reported without any personal details (P6).
    """
    ctx = current_context()
    identifiers = {
        i.scheme.value: _identifier_digest(
            crypto, i.scheme, normalize_identifier(i.scheme, i.value)
        )
        for i in data.identifiers
    }
    contacts = frozenset(normalize_contact(c.kind, c.value) for c in data.contacts)
    probe = PersonFacts(
        data.given_name,
        data.family_name,
        data.other_names,
        data.date_of_birth,
        data.sex_at_registration,
        identifiers,
        contacts,
    )

    keys = {k for k in (blocking_key(data.family_name), blocking_key(data.given_name)) if k}
    clauses = []
    if keys:
        clauses += [Person.family_key.in_(keys), Person.given_key.in_(keys)]
    if data.date_of_birth:
        d = data.date_of_birth
        dobs = {d}
        if d.day <= 12:
            dobs.add(date(d.year, d.day, d.month))
        clauses.append(Person.date_of_birth.in_(dobs))
    id_people: set[uuid.UUID] = set()
    if identifiers:
        id_people |= set(
            (
                await session.execute(
                    select(ExternalIdentifier.person_id)
                    .where(
                        ExternalIdentifier.tenant_id == ctx.tenant_id,
                        ExternalIdentifier.value_hmac.in_(list(identifiers.values())),
                    )
                    .execution_options(**_bypass())
                )
            ).scalars()
        )
    if contacts:
        id_people |= set(
            (
                await session.execute(
                    select(PersonContact.person_id)
                    .where(
                        PersonContact.tenant_id == ctx.tenant_id,
                        PersonContact.value.in_(list(contacts)),
                        PersonContact.valid_to.is_(None),
                    )
                    .execution_options(**_bypass())
                )
            ).scalars()
        )
    alt_people: set[uuid.UUID] = set()
    if keys:
        alt_people = set(
            (
                await session.execute(
                    select(PersonName.person_id)
                    .where(PersonName.tenant_id == ctx.tenant_id, PersonName.family_key.in_(keys))
                    .execution_options(**_bypass())
                )
            ).scalars()
        )
    if id_people or alt_people:
        clauses.append(Person.id.in_(id_people | alt_people))
    if not clauses:
        return []
    people = (
        (
            await session.execute(
                select(Person)
                .where(Person.tenant_id == ctx.tenant_id, Person.status == "active", or_(*clauses))
                .limit(MAX_CANDIDATES)
                .execution_options(**_bypass())
            )
        )
        .scalars()
        .all()
    )
    # Demonstration records are matched only with each other, never with real people (ADR-031).
    demo = is_demo(data.source_system)
    people = [
        p for p in people if p.id not in (exclude or set()) and is_demo(p.source_system) == demo
    ]
    facts = await _facts_for(session, people)
    limits = await thresholds(session, data.scope_node_id or ctx.scope_node_id)
    visible = set((await session.execute(governance.subtree_of(ctx.scope_node_id))).scalars())

    scored: list[tuple[Person, MatchScore]] = []
    for p in people:
        best = max((score_match(probe, f) for f in facts[p.id]), key=lambda m: m.score)
        if limits.outcome(best.score) is not MatchOutcome.NO_MATCH:
            scored.append((p, best))
    scored.sort(key=lambda t: -t[1].score)
    out = []
    for p, m in scored:
        seen = p.scope_node_id in visible
        out.append(
            Candidate(
                person_id=p.id,
                score=m.score,
                outcome=limits.outcome(m.score),
                reasons=m.explain() if seen else [],
                visible=seen,
                reasons_full=m.explain(),
                display_name=f"{p.given_name} {p.family_name}" if seen else None,
                date_of_birth=p.date_of_birth if seen else None,
            )
        )
    return out


# --------------------------------------------------------------------------- quality


async def refresh_quality(session: AsyncSession, person: Person) -> dict[str, Any]:
    ident = (
        (
            await session.execute(
                select(ExternalIdentifier).where(ExternalIdentifier.person_id == person.id)
            )
        )
        .scalars()
        .all()
    )
    n_contacts = len(
        (
            await session.execute(
                select(PersonContact.id).where(
                    PersonContact.person_id == person.id, PersonContact.valid_to.is_(None)
                )
            )
        ).all()
    )
    n_reviews = len(
        (
            await session.execute(
                select(MatchReview.id)
                .where(
                    or_(MatchReview.person_id == person.id, MatchReview.candidate_id == person.id),
                    MatchReview.status == "open",
                )
                .execution_options(**_bypass())
            )
        ).all()
    )
    report = assess_quality(
        QualityInput(
            given_name=person.given_name,
            family_name=person.family_name,
            date_of_birth=person.date_of_birth,
            sex=person.sex_at_registration,
            nationality=person.nationality,
            primary_language=person.primary_language,
            name_script=person.name_script,
            contact_count=n_contacts,
            identifier_count=len(ident),
            verified_identifier_count=sum(1 for i in ident if i.verified_at),
            open_match_reviews=n_reviews,
            last_confirmed_at=person.last_confirmed_at,
        ),
        today=today(),
        now=utcnow(),
    )
    person.quality_score = report.score
    person.quality = report.as_json()
    return person.quality


# --------------------------------------------------------------------------- create


def _validate(data: PersonIn) -> None:
    if not name_tokens(data.given_name) and data.name_script == NameScript.LATIN:
        raise ValidationFailed(
            "given name is required",
            next_action="Enter the given name as on the birth certificate.",
        )
    if not name_tokens(data.family_name) and data.name_script == NameScript.LATIN:
        raise ValidationFailed(
            "family name is required",
            next_action="Enter the family name as on the birth certificate.",
        )
    if data.date_of_birth and data.date_of_birth > today():
        raise ValidationFailed(
            "date of birth is in the future", next_action="Check the date of birth."
        )
    schemes = [i.scheme for i in data.identifiers]
    if len(schemes) != len(set(schemes)):
        raise ValidationFailed("each identifier type can be given only once")


async def _add_identifier_row(
    session: AsyncSession, crypto: FieldCrypto, person: Person, ident: IdentifierIn
) -> ExternalIdentifier:
    normalized = normalize_identifier(ident.scheme, ident.value)
    digest = _identifier_digest(crypto, ident.scheme, normalized)
    clash = (
        await session.execute(
            select(ExternalIdentifier.person_id)
            .where(
                ExternalIdentifier.tenant_id == person.tenant_id,
                ExternalIdentifier.scheme == ident.scheme.value,
                ExternalIdentifier.value_hmac == digest,
            )
            .execution_options(**_bypass())
        )
    ).first()
    if clash is not None:
        raise DuplicateBlocked(
            f"this {ident.scheme.value} number is already recorded for another person",
            next_action=(
                "Check the number. If it is the same person, open their record "
                "or ask a registry officer to merge."
            ),
        )
    row_id = uuid.uuid4()
    row = ExternalIdentifier(
        id=row_id,
        person_id=person.id,
        scope_node_id=person.scope_node_id,
        scheme=ident.scheme.value,
        value_enc=crypto.cipher.encrypt(
            normalized,
            tenant_id=person.tenant_id,
            table=IDENTIFIER_TABLE,
            column="value_enc",
            row_id=row_id,
        ),
        value_hmac=digest,
        display_suffix=normalized[-4:],
        verified_at=utcnow() if ident.verified_by_adapter else None,
        verified_by_adapter=ident.verified_by_adapter,
    )
    session.add(row)
    return row


def _add_contact_row(session: AsyncSession, person: Person, c: ContactIn) -> PersonContact:
    row = PersonContact(
        person_id=person.id,
        scope_node_id=person.scope_node_id,
        kind=c.kind.value,
        value=normalize_contact(c.kind, c.value, person.nationality or "NG"),
        is_primary=c.is_primary,
        valid_from=today(),
    )
    session.add(row)
    return row


async def create_person(
    session: AsyncSession,
    crypto: FieldCrypto,
    data: PersonIn,
    *,
    not_duplicate_of: Sequence[uuid.UUID] = (),
    override_reason: str | None = None,
) -> CreateResult:
    """Create a person unless a high-confidence duplicate exists (§4).

    * Score >= auto-link: blocked; the existing record is offered. A data steward can still
      create by naming each blocking candidate in ``not_duplicate_of`` with a reason; that
      decision is recorded as a resolved review.
    * Review band: created, with a match_review task per candidate.
    """
    ctx = current_context()
    _validate(data)
    candidates = await find_candidates(session, crypto, data)
    blocking = [c for c in candidates if c.outcome is MatchOutcome.BLOCK]
    overrides = set(not_duplicate_of)
    overridden = [c for c in blocking if c.visible and c.person_id in overrides]
    unresolved = [c for c in blocking if c not in overridden]
    if unresolved:
        raise DuplicateBlocked(
            "a matching person is already registered",
            next_action=(
                "Open the existing record. If this really is a different person, "
                "confirm that with a reason."
            ),
            candidates=[_candidate_json(c) for c in candidates[:SHOWN_CANDIDATES]],
        )
    if overridden and not (override_reason and override_reason.strip()):
        raise ValidationFailed("give a reason for creating a record despite the match")

    person = Person(
        scope_node_id=data.scope_node_id or ctx.scope_node_id,
        given_name=data.given_name.strip(),
        family_name=data.family_name.strip(),
        other_names=(data.other_names or "").strip() or None,
        preferred_name=(data.preferred_name or "").strip() or None,
        name_script=data.name_script.value,
        date_of_birth=data.date_of_birth,
        sex_at_registration=data.sex_at_registration.value,
        nationality=data.nationality,
        primary_language=data.primary_language,
        source_system=data.source_system,
        source_ref=data.source_ref,
        last_confirmed_at=utcnow(),
        status="active",
    )
    _set_keys(person)
    session.add(person)
    await session.flush()
    for contact in data.contacts:
        _add_contact_row(session, person, contact)
    for i in data.identifiers:
        await _add_identifier_row(session, crypto, person, i)
    await session.flush()

    review_ids: list[uuid.UUID] = []
    for c in candidates:
        status = "open" if c.outcome is MatchOutcome.REVIEW else "different_people"
        if c.visible:
            review_scope = person.scope_node_id
        else:
            # Out of the caller's sight: file it at the lowest scope that holds both records
            # (e.g. the LGEA for two schools in it), where a steward can see both (ADR-009).
            candidate_scope = (
                await session.execute(
                    select(Person.scope_node_id)
                    .where(Person.id == c.person_id)
                    .execution_options(**_bypass())
                )
            ).scalar_one()
            review_scope = await governance.lowest_common_ancestor(
                session, person.scope_node_id, candidate_scope
            )
        review = MatchReview(
            scope_node_id=review_scope,
            person_id=person.id,
            candidate_id=c.person_id,
            score=c.score,
            reasons=c.reasons_full,
            status=status,
        )
        if status != "open":
            review.decided_by, review.decided_at, review.decision_note = (
                ctx.user_id,
                utcnow(),
                override_reason,
            )
        session.add(review)
        if c.visible:
            await session.flush()
        else:
            # Deliberate escalation above the caller's scope, recorded under that purpose.
            with use_context(
                replace(ctx, scope_node_id=review_scope, purpose="duplicate-escalation")
            ):
                await session.flush()
        review_ids.append(review.id)
        if status == "open":
            publish(
                session,
                _event(
                    events.MATCH_REVIEW_OPENED,
                    person,
                    {"review_id": str(review.id), "score": c.score},
                ),
            )
    await refresh_quality(session, person)
    publish(session, _event(events.PERSON_CREATED, person, {"source_system": data.source_system}))
    await session.flush()
    return CreateResult(person.id, review_ids, candidates)


def _candidate_json(c: Candidate) -> dict[str, Any]:
    if not c.visible:
        return {
            "visible": False,
            "score": c.score,
            "outcome": c.outcome.value,
            "message": (
                "A possible match is registered elsewhere in your organisation. "
                "A registry officer can check it."
            ),
        }
    return {
        "visible": True,
        "person_id": str(c.person_id),
        "score": c.score,
        "outcome": c.outcome.value,
        "display_name": c.display_name,
        "date_of_birth": c.date_of_birth.isoformat() if c.date_of_birth else None,
        "reasons": c.reasons,
    }


# --------------------------------------------------------------------------- read


async def get_person(session: AsyncSession, person_id: uuid.UUID) -> PersonView:
    """Returns the surviving record; a merged id resolves to its survivor (alias, §4)."""
    p = await _get_person(session, person_id)
    requested = None
    hops = 0
    while p.status == "merged" and p.merged_into_id is not None:
        requested = requested or person_id
        p = await _get_person(session, p.merged_into_id)
        hops += 1
        if hops > 10:
            raise Conflict("merge chain is too long; ask a registry officer to check this record")
    idents = (
        (
            await session.execute(
                select(ExternalIdentifier)
                .where(ExternalIdentifier.person_id == p.id)
                .order_by(ExternalIdentifier.scheme)
            )
        )
        .scalars()
        .all()
    )
    contacts = (
        (
            await session.execute(
                select(PersonContact)
                .where(PersonContact.person_id == p.id, PersonContact.valid_to.is_(None))
                .order_by(PersonContact.is_primary.desc(), PersonContact.created_at)
            )
        )
        .scalars()
        .all()
    )
    names = (
        (
            await session.execute(
                select(PersonName)
                .where(PersonName.person_id == p.id)
                .order_by(PersonName.valid_from)
            )
        )
        .scalars()
        .all()
    )
    merged_from = list(
        (await session.execute(select(Person.id).where(Person.merged_into_id == p.id))).scalars()
    )
    return PersonView(
        id=p.id,
        tenant_id=p.tenant_id,
        scope_node_id=p.scope_node_id,
        given_name=p.given_name,
        family_name=p.family_name,
        other_names=p.other_names,
        preferred_name=p.preferred_name,
        name_script=p.name_script,
        date_of_birth=p.date_of_birth,
        sex_at_registration=p.sex_at_registration,
        nationality=p.nationality,
        primary_language=p.primary_language,
        status=p.status,
        row_version=p.row_version,
        quality=p.quality,
        source_system=p.source_system,
        identifiers=[
            IdentifierView(i.id, i.scheme, mask("0" * 7 + i.display_suffix), i.verified_at)
            for i in idents
        ],
        contacts=[
            ContactView(c.id, c.kind, c.value, c.is_primary, c.verified_at) for c in contacts
        ],
        other_recorded_names=[
            {
                "type": n.name_type,
                "given_name": n.given_name,
                "family_name": n.family_name,
                "valid_from": n.valid_from.isoformat(),
            }
            for n in names
        ],
        merged_from=merged_from,
        requested_id=requested,
    )


async def reveal_identifier(
    session: AsyncSession,
    crypto: FieldCrypto,
    person_id: uuid.UUID,
    identifier_id: uuid.UUID,
) -> str:
    """Decrypt one identifier. The caller must state a purpose; the read is audited (§8, §9.1)."""
    ctx = current_context()
    if not ctx.purpose:
        raise ValidationFailed(
            "state why you need to see this number",
            next_action="Send the purpose in the X-Purpose header, e.g. 'exam-registration'.",
        )
    person = await _get_person(session, person_id)
    row = (
        await session.execute(
            select(ExternalIdentifier).where(
                ExternalIdentifier.id == identifier_id, ExternalIdentifier.person_id == person.id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("identifier not found")
    value = crypto.cipher.decrypt(
        row.value_enc,
        tenant_id=row.tenant_id,
        table=IDENTIFIER_TABLE,
        column="value_enc",
        row_id=row.id,
    )
    await session.run_sync(
        lambda sync: append_audit(
            sync,
            tenant_id=row.tenant_id,
            action="reveal",
            entity_type="external_identifier",
            entity_id=row.id,
            scope_node_id=row.scope_node_id,
            before=None,
            after=None,
        )
    )
    return value


# --------------------------------------------------------------------------- update

EDITABLE = (
    "given_name",
    "family_name",
    "other_names",
    "preferred_name",
    "name_script",
    "date_of_birth",
    "sex_at_registration",
    "nationality",
    "primary_language",
)


async def update_person(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    expected_version: int,
    changes: dict[str, Any],
    keep_previous_name: bool = True,
) -> PersonView:
    """Optimistic concurrency via If-Match on row_version (§10.4). A changed legal name is
    kept as a 'previous' name so old paperwork still matches."""
    p = await _get_person(session, person_id)
    if p.status != "active":
        raise Conflict(
            "this record has been merged", next_action="Edit the surviving record instead."
        )
    if p.row_version != expected_version:
        raise PreconditionFailed(
            "this person was changed by someone else since you opened it",
            next_action="Reload the record, check the changes, then try again.",
        )
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise ValidationFailed(f"these fields cannot be changed here: {sorted(unknown)}")
    if changes.get("date_of_birth") and changes["date_of_birth"] > today():
        raise ValidationFailed(
            "date of birth is in the future", next_action="Check the date of birth."
        )
    old_given, old_family = p.given_name, p.family_name
    for k, v in changes.items():
        if isinstance(v, (Sex, NameScript)):
            v = v.value
        setattr(p, k, v)
    if keep_previous_name and (p.given_name != old_given or p.family_name != old_family):
        session.add(
            PersonName(
                person_id=p.id,
                scope_node_id=p.scope_node_id,
                name_type="previous",
                given_name=old_given,
                family_name=old_family,
                family_key=blocking_key(old_family),
                valid_from=today(),
            )
        )
    _set_keys(p)
    p.last_confirmed_at = utcnow()
    await session.flush()
    await refresh_quality(session, p)
    publish(session, _event(events.PERSON_UPDATED, p, {"fields": sorted(changes)}))
    await session.flush()
    return await get_person(session, p.id)


async def add_identifier(
    session: AsyncSession, crypto: FieldCrypto, person_id: uuid.UUID, ident: IdentifierIn
) -> uuid.UUID:
    p = await _get_person(session, person_id)
    if p.status != "active":
        raise Conflict(
            "this record has been merged", next_action="Add it to the surviving record instead."
        )
    if (
        await session.execute(
            select(ExternalIdentifier.id).where(
                ExternalIdentifier.person_id == p.id,
                ExternalIdentifier.scheme == ident.scheme.value,
            )
        )
    ).first():
        raise Conflict(
            f"this person already has a {ident.scheme.value} number",
            next_action="Correct the existing number with a registry officer.",
        )
    row = await _add_identifier_row(session, crypto, p, ident)
    await session.flush()
    await refresh_quality(session, p)
    publish(session, _event(events.PERSON_UPDATED, p, {"fields": ["identifiers"]}))
    return row.id


async def add_contact(session: AsyncSession, person_id: uuid.UUID, contact: ContactIn) -> uuid.UUID:
    p = await _get_person(session, person_id)
    if p.status != "active":
        raise Conflict(
            "this record has been merged", next_action="Add it to the surviving record instead."
        )
    row = _add_contact_row(session, p, contact)
    await session.flush()
    await refresh_quality(session, p)
    publish(session, _event(events.PERSON_UPDATED, p, {"fields": ["contacts"]}))
    return row.id


# --------------------------------------------------------------------------- reviews


async def list_reviews(
    session: AsyncSession,
    *,
    status: str = "open",
    after: uuid.UUID | None = None,
    limit: int = 50,
) -> list[MatchReview]:
    stmt = (
        select(MatchReview)
        .where(MatchReview.status == status)
        .order_by(MatchReview.id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(MatchReview.id > after)
    return list((await session.execute(stmt)).scalars())


async def decide_review(
    session: AsyncSession,
    review_id: uuid.UUID,
    *,
    same_person: bool,
    note: str,
    survivor_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """A steward's decision. 'Same person' merges the two records; returns the merge id."""
    if not note.strip():
        raise ValidationFailed("give a short reason for the decision")
    review = (
        await session.execute(select(MatchReview).where(MatchReview.id == review_id))
    ).scalar_one_or_none()
    if review is None:
        raise NotFound("review not found")
    if review.status != "open":
        raise Conflict("this review has already been decided")
    review.status = "same_person" if same_person else "different_people"
    review.decided_by, review.decided_at, review.decision_note = (
        _ctx_actor(),
        utcnow(),
        note.strip(),
    )
    await session.flush()
    merge_id = None
    if same_person:
        survivor = survivor_id or review.candidate_id  # default: keep the older record
        if survivor not in (review.person_id, review.candidate_id):
            raise ValidationFailed("the surviving record must be one of the two under review")
        merged = review.person_id if survivor == review.candidate_id else review.candidate_id
        merge_id = await merge_persons(
            session, survivor_id=survivor, merged_id=merged, reason=note, review_id=review.id
        )
    for pid in (review.person_id, review.candidate_id):
        p = (
            await session.execute(
                select(Person).where(Person.id == pid).execution_options(**_bypass())
            )
        ).scalar_one()
        await refresh_quality(session, p)
    person = (
        await session.execute(
            select(Person).where(Person.id == review.person_id).execution_options(**_bypass())
        )
    ).scalar_one()
    publish(
        session,
        _event(
            events.MATCH_REVIEW_RESOLVED,
            person,
            {"review_id": str(review.id), "decision": review.status},
        ),
    )
    await session.flush()
    return merge_id


# --------------------------------------------------------------------------- merge / un-merge


def _fields(p: Person) -> dict[str, Any]:
    return {
        f: (getattr(p, f).isoformat() if isinstance(getattr(p, f), date) else getattr(p, f))
        for f in (
            "given_name",
            "family_name",
            "other_names",
            "preferred_name",
            "name_script",
            "date_of_birth",
            "sex_at_registration",
            "nationality",
            "primary_language",
            "source_system",
        )
    }


def _restore(value: Any, f: str) -> Any:
    return date.fromisoformat(value) if f == "date_of_birth" and value else value


async def merge_persons(
    session: AsyncSession,
    *,
    survivor_id: uuid.UUID,
    merged_id: uuid.UUID,
    reason: str,
    review_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Merge ``merged`` into ``survivor``. The merged id remains as an alias, and the merge is
    reversible from its snapshot (§4)."""
    if not reason.strip():
        raise ValidationFailed("give a reason for the merge")
    survivor = await _get_person(session, survivor_id)
    merged = await _get_person(session, merged_id)
    ensure_mergeable(survivor.id, merged.id, survivor.status, merged.status)

    s_ids = {
        i.scheme: i
        for i in (
            await session.execute(
                select(ExternalIdentifier).where(ExternalIdentifier.person_id == survivor.id)
            )
        ).scalars()
    }
    m_ids = list(
        (
            await session.execute(
                select(ExternalIdentifier).where(ExternalIdentifier.person_id == merged.id)
            )
        ).scalars()
    )
    for i in m_ids:
        if i.scheme in s_ids and s_ids[i.scheme].value_hmac != i.value_hmac:
            raise Conflict(
                f"the two records have different {i.scheme} numbers",
                next_action="Confirm which number is correct and fix the other record first.",
            )
    m_contacts = list(
        (
            await session.execute(select(PersonContact).where(PersonContact.person_id == merged.id))
        ).scalars()
    )
    s_contact_values = set(
        (
            await session.execute(
                select(PersonContact.value).where(
                    PersonContact.person_id == survivor.id, PersonContact.valid_to.is_(None)
                )
            )
        ).scalars()
    )
    m_names = list(
        (
            await session.execute(select(PersonName).where(PersonName.person_id == merged.id))
        ).scalars()
    )

    trust = await governance.resolve_config(session, survivor.scope_node_id, "mdm.source_trust")
    decisions: list[FieldDecision] = survive(
        SourcedRecord(_fields(survivor), survivor.source_system, survivor.updated_at),
        SourcedRecord(_fields(merged), merged.source_system, merged.updated_at),
        trust if isinstance(trust, dict) else None,
    )
    snapshot = {
        "survivor_fields": _fields(survivor),
        "survivor_version": survivor.row_version,
        "moved_identifier_ids": [str(i.id) for i in m_ids if i.scheme not in s_ids],
        "duplicate_identifier_ids": [str(i.id) for i in m_ids if i.scheme in s_ids],
        "moved_contact_ids": [str(c.id) for c in m_contacts if c.value not in s_contact_values],
        "closed_contact_ids": [
            str(c.id) for c in m_contacts if c.value in s_contact_values and c.valid_to is None
        ],
        "moved_name_ids": [str(n.id) for n in m_names],
    }
    for d in decisions:
        if d.from_record == "merged":
            setattr(
                survivor,
                d.field,
                _restore(d.value, d.field) if isinstance(d.value, str) else d.value,
            )
    # Identifiers: move the merged record's; an identical one (same scheme, same value) is
    # kept only on the survivor, and the merged copy stays with the merged record.
    for i in m_ids:
        if i.scheme not in s_ids:
            i.person_id = survivor.id
    for c in m_contacts:
        if c.value in s_contact_values:
            if c.valid_to is None:
                c.valid_to = today()
        else:
            c.person_id = survivor.id
    for n in m_names:
        n.person_id = survivor.id
    alias = None
    if (merged.given_name, merged.family_name) != (survivor.given_name, survivor.family_name):
        alias = PersonName(
            person_id=survivor.id,
            scope_node_id=survivor.scope_node_id,
            name_type="alias",
            given_name=merged.given_name,
            family_name=merged.family_name,
            other_names=merged.other_names,
            family_key=blocking_key(merged.family_name),
            valid_from=today(),
        )
        session.add(alias)
    merged.status, merged.merged_into_id = "merged", survivor.id
    _set_keys(survivor)
    await session.flush()
    snapshot["alias_name_id"] = str(alias.id) if alias else None
    snapshot["survivor_fields_after"] = _fields(survivor)
    record = PersonMerge(
        scope_node_id=survivor.scope_node_id,
        survivor_id=survivor.id,
        merged_id=merged.id,
        snapshot=snapshot,
        decisions=[
            {"field": d.field, "from": d.from_record, "reason": d.reason} for d in decisions
        ],
        reason=reason.strip(),
        review_id=review_id,
        status="applied",
    )
    session.add(record)
    # Re-point open reviews that mention the merged record.
    for r in (
        await session.execute(
            select(MatchReview).where(
                MatchReview.status == "open",
                or_(MatchReview.person_id == merged.id, MatchReview.candidate_id == merged.id),
            )
        )
    ).scalars():
        r.status, r.decided_at, r.decided_by = "same_person", utcnow(), _ctx_actor()
        r.decision_note = "closed by merge"
    await session.flush()
    await refresh_quality(session, survivor)
    publish(
        session,
        _event(
            events.PERSON_MERGED,
            survivor,
            {"merged_id": str(merged.id), "merge_id": str(record.id)},
        ),
    )
    await session.flush()
    return record.id


async def unmerge(session: AsyncSession, merge_id: uuid.UUID, *, reason: str) -> list[str]:
    """Reverse a merge from its snapshot. Survivor fields changed since the merge are kept,
    and returned so the steward can check them."""
    if not reason.strip():
        raise ValidationFailed("give a reason for reversing the merge")
    record = (
        await session.execute(select(PersonMerge).where(PersonMerge.id == merge_id))
    ).scalar_one_or_none()
    if record is None:
        raise NotFound("merge not found")
    if record.status != "applied":
        raise Conflict("this merge has already been reversed")
    survivor = await _get_person(session, record.survivor_id)
    merged = await _get_person(session, record.merged_id)
    if survivor.status == "merged":
        raise Conflict(
            "the surviving record was merged again later",
            next_action="Reverse the later merge first.",
        )
    snap = record.snapshot
    kept_changes: list[str] = []
    after, before = snap["survivor_fields_after"], snap["survivor_fields"]
    current = _fields(survivor)
    for f, old in before.items():
        if current.get(f) == after.get(f):
            setattr(survivor, f, _restore(old, f))
        elif old != current.get(f):
            kept_changes.append(f)
    for i in (
        await session.execute(
            select(ExternalIdentifier).where(
                ExternalIdentifier.id.in_([uuid.UUID(x) for x in snap["moved_identifier_ids"]])
            )
        )
    ).scalars():
        i.person_id = merged.id
    for c in (
        await session.execute(
            select(PersonContact).where(
                PersonContact.id.in_([uuid.UUID(x) for x in snap["moved_contact_ids"]])
            )
        )
    ).scalars():
        c.person_id = merged.id
    for c in (
        await session.execute(
            select(PersonContact).where(
                PersonContact.id.in_([uuid.UUID(x) for x in snap["closed_contact_ids"]])
            )
        )
    ).scalars():
        c.valid_to = None
    for n in (
        await session.execute(
            select(PersonName).where(
                PersonName.id.in_([uuid.UUID(x) for x in snap["moved_name_ids"]])
            )
        )
    ).scalars():
        n.person_id = merged.id
    if snap.get("alias_name_id"):
        alias = (
            await session.execute(
                select(PersonName).where(PersonName.id == uuid.UUID(snap["alias_name_id"]))
            )
        ).scalar_one_or_none()
        if alias is not None:
            await session.delete(alias)
    merged.status, merged.merged_into_id = "active", None
    record.status, record.reversed_by, record.reversed_at = "reversed", _ctx_actor(), utcnow()
    _set_keys(survivor)
    await session.flush()
    for p in (survivor, merged):
        await refresh_quality(session, p)
    publish(
        session,
        _event(
            events.PERSON_UNMERGED,
            survivor,
            {"merged_id": str(merged.id), "merge_id": str(record.id)},
        ),
    )
    await session.flush()
    return kept_changes


__all__ = [
    "Candidate",
    "ContactIn",
    "CreateResult",
    "DuplicateBlocked",
    "IdentifierIn",
    "PersonIn",
    "PersonView",
    "add_contact",
    "add_identifier",
    "create_person",
    "decide_review",
    "find_candidates",
    "get_person",
    "list_reviews",
    "merge_persons",
    "phonetic_keys",
    "refresh_quality",
    "reveal_identifier",
    "thresholds",
    "unmerge",
    "update_person",
]
