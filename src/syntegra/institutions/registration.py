"""Private provider registration (state directive: every private school must register), using
the CEDA §22 "Institution registration" workflow and §2 private-institution tenancy (ADR-014).

1. A proprietor submits an application without an account and gets a tracking reference.
2. The regulator (configured per jurisdiction, `private.registration`) moves it through
   under review → site inspection → approved, or rejects it with a reason.
3. Approval creates the school's own tenant (§2: private institutions are full tenants),
   registers it in the state register as a private institution under its LGEA, and creates
   the proprietor's administrator account.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

import phonenumbers
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from syntegra.core.clock import utcnow
from syntegra.core.context import current_context, use_context
from syntegra.core.errors import Conflict, NotFound, ValidationFailed
from syntegra.core.events import EventEnvelope
from syntegra.core.ids import institution_id
from syntegra.governance import api as governance
from syntegra.governance.domain import GovernanceLevel, Ownership
from syntegra.iam import api as iam
from syntegra.institutions import api as register
from syntegra.institutions import events
from syntegra.institutions.domain import (
    LEVEL_LABEL,
    TYPE_LABEL,
    CleanRow,
    Level,
    fold,
    mark_duplicates,
    parse_levels,
    validate_row,
)
from syntegra.institutions.models import Institution, InstitutionLevel, RegistrationApplication
from syntegra.platform.outbox import publish
from syntegra.platform.sequence import next_value
from syntegra.workflow.domain import CATALOGUE, validate_transition

WORKFLOW = CATALOGUE["institution_registration"]
SETTING = "private.registration"
OPEN_STATES = ("submitted", "under_review", "site_inspection")
CAC = re.compile(
    r"^(RC|BN|IT)?\s?-?\d{4,8}$", re.IGNORECASE
)  # Corporate Affairs Commission numbers
SCHOOL_FIELDS = (
    "name",
    "type",
    "lga",
    "levels",
    "government_code",
    "address_line1",
    "address_line2",
    "town",
    "postcode",
    "phone",
    "email",
    "latitude",
    "longitude",
)


@dataclass(frozen=True, slots=True)
class ApplicationView:
    reference: str
    status: str
    regulator_code: str
    school_name: str
    institution_type: str
    lga: str
    levels: list[str]
    full_address: str
    proprietor_name: str
    proprietor_email: str
    proprietor_phone: str
    cac_number: str | None
    submitted_at: Any
    history: list[dict[str, Any]]
    decision_note: str | None
    institution_id: uuid.UUID | None
    allowed_next: list[str]


def _proprietor(form: dict[str, str]) -> tuple[dict[str, str | None], list[str]]:
    errors: list[str] = []
    name = re.sub(r"\s+", " ", (form.get("proprietor_name") or "").strip())
    if not 3 <= len(name) <= 200:
        errors.append("proprietor name is required (3 to 200 characters)")
    email = (form.get("proprietor_email") or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors.append("a valid proprietor email is required: it becomes the school's sign-in")
    phone = None
    try:
        parsed = phonenumbers.parse(form.get("proprietor_phone") or "", "NG")
        if phonenumbers.is_valid_number(parsed):
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    if phone is None:
        errors.append("a valid proprietor phone number is required")
    cac = (form.get("cac_number") or "").strip().upper() or None
    if cac and not CAC.match(cac):
        errors.append("CAC number should look like RC 123456, BN 1234567 or IT 12345")
    return {"name": name, "email": email, "phone": phone, "cac": cac}, errors


async def _regulator_for(
    session: AsyncSession, levels: list[Level]
) -> tuple[str, uuid.UUID, frozenset[Level]]:
    ctx = current_context()
    root = await governance.tenant_root_scope(session, ctx.tenant_id)
    for entry in await governance.resolve_config(session, root, SETTING) or []:
        allowed = frozenset(Level(v) for v in entry["levels"])
        if levels and set(levels) <= allowed:
            node = await governance.scope_node_by_code(session, ctx.tenant_id, entry["body"])
            if node is not None:
                return entry["body"], node.id, allowed
    offered = ", ".join(LEVEL_LABEL[lv] for lv in levels) or "these levels"
    raise ValidationFailed(
        f"private {offered} institutions are not registered through this system here",
        next_action="Check the levels you offer, or contact the state's education office.",
    )


async def _validated(
    session: AsyncSession, form: dict[str, str], allowed: frozenset[Level]
) -> CleanRow:
    """The school's details, validated exactly like an import row for the regulator's body."""
    raw = {k: str(form.get(k) or "") for k in SCHOOL_FIELDS}
    result = validate_row(
        1,
        raw,
        await register.placement(session),
        allowed=allowed,
        address=await register.address_rules(session),
    )
    codes, names = await register._existing(session)
    mark_duplicates([result], existing_codes=codes, existing_names=names)
    if result.clean is None:
        raise ValidationFailed(
            "; ".join(result.errors), next_action="Correct the school's details and submit again."
        )
    return result.clean


async def submit_application(session: AsyncSession, form: dict[str, str]) -> str:
    """Public: a proprietor's application. Returns the tracking reference."""
    ctx = current_context()
    levels, err = parse_levels(form.get("levels") or "")
    if err:
        raise ValidationFailed(err)
    regulator_code, regulator_scope, allowed = await _regulator_for(session, levels)
    proprietor, errors = _proprietor(form)
    with use_context(replace(ctx, scope_node_id=regulator_scope)):
        try:
            clean = await _validated(session, form, allowed)
        except ValidationFailed as exc:
            errors.insert(0, exc.detail)
            clean = None
        if errors:
            raise ValidationFailed(
                "; ".join(errors), next_action="Correct the details and submit again."
            )
        assert clean is not None
        pending = (
            await session.execute(
                select(RegistrationApplication.id).where(
                    RegistrationApplication.name_key == clean.name_key,
                    RegistrationApplication.location_key == fold(clean.location),
                    RegistrationApplication.status.in_(OPEN_STATES),
                )
            )
        ).first()
        if pending is not None:
            raise Conflict(
                f"'{clean.name}' in {clean.location} already has a registration application "
                "in progress",
                next_action="Use the reference you were given to check its status.",
            )
        seq = await next_value(session, ctx.tenant_id, "private_registration")
        reference = f"PSR-{utcnow().year}-{seq:06d}"
        app = RegistrationApplication(
            scope_node_id=regulator_scope,
            reference=reference,
            status="submitted",
            regulator_code=regulator_code,
            details=clean.as_json(),
            name_key=clean.name_key,
            location_key=fold(clean.location),
            proprietor_name=proprietor["name"],
            proprietor_email=proprietor["email"],
            proprietor_phone=proprietor["phone"],
            cac_number=proprietor["cac"],
            history=[{"status": "submitted", "at": utcnow().isoformat(), "by": "applicant"}],
        )
        session.add(app)
        await session.flush()
        publish(
            session,
            EventEnvelope(
                event_type=events.REGISTRATION_SUBMITTED,
                tenant_id=ctx.tenant_id,
                scope_node_id=regulator_scope,
                aggregate_type="registration_application",
                aggregate_id=app.id,
                actor_id=None,
                correlation_id=ctx.correlation_id,
                payload={"reference": reference},
            ),
        )
        await session.flush()
    return reference


async def application_status(session: AsyncSession, reference: str, email: str) -> dict[str, Any]:
    """Public: the status of one application, for whoever holds its reference and email."""
    app = (
        await session.execute(
            select(RegistrationApplication).where(
                RegistrationApplication.reference == reference.strip().upper()
            )
        )
    ).scalar_one_or_none()
    if app is None or app.proprietor_email != email.strip().lower():
        raise NotFound(
            "no application matches this reference and email",
            next_action="Check both against your confirmation.",
        )
    out: dict[str, Any] = {
        "reference": app.reference,
        "status": app.status,
        "school_name": app.details["name"],
        "submitted_at": app.history[0]["at"],
    }
    if app.status == "rejected":
        out["reason"] = app.decision_note
    if app.status == "approved":
        out["next_action"] = "Sign in with your email to set up your school on the system."
    return out


async def _view(session: AsyncSession, app: RegistrationApplication) -> ApplicationView:
    d = app.details
    rules = await register.address_rules(session)
    from syntegra.institutions.domain import InstitutionType, compose_address

    return ApplicationView(
        reference=app.reference,
        status=app.status,
        regulator_code=app.regulator_code,
        school_name=d["name"],
        institution_type=TYPE_LABEL[InstitutionType(d["type"])],
        lga=d["lga"],
        levels=[LEVEL_LABEL[Level(x)] for x in d["levels"]],
        full_address=compose_address(
            line1=d.get("address_line1"),
            line2=d.get("address_line2"),
            town=d.get("town"),
            location=d["lga"],
            postcode=d.get("postcode"),
            state=rules.state,
            country=rules.country,
        ),
        proprietor_name=app.proprietor_name,
        proprietor_email=app.proprietor_email,
        proprietor_phone=app.proprietor_phone,
        cac_number=app.cac_number,
        submitted_at=app.created_at,
        history=app.history,
        decision_note=app.decision_note,
        institution_id=app.institution_id,
        allowed_next=sorted(WORKFLOW.allowed(app.status)),
    )


async def _get(session: AsyncSession, reference: str) -> RegistrationApplication:
    app = (
        await session.execute(
            select(RegistrationApplication).where(
                RegistrationApplication.reference == reference.strip().upper()
            )
        )
    ).scalar_one_or_none()
    if app is None:
        raise NotFound("application not found")
    return app


async def get_application(session: AsyncSession, reference: str) -> ApplicationView:
    return await _view(session, await _get(session, reference))


async def list_applications(
    session: AsyncSession, *, status: str | None, limit: int = 100
) -> list[ApplicationView]:
    stmt = select(RegistrationApplication).order_by(RegistrationApplication.id).limit(limit)
    if status:
        stmt = stmt.where(RegistrationApplication.status == status)
    return [await _view(session, a) for a in (await session.execute(stmt)).scalars()]


async def advance(
    session: AsyncSession, reference: str, to_state: str, note: str | None
) -> ApplicationView:
    """Regulator: move an application on.

    Rejection needs a reason; approval registers the school.
    """
    ctx = current_context()
    app = await _get(session, reference)
    validate_transition(WORKFLOW, app.status, to_state, actor=str(ctx.user_id), previous_actor=None)
    if to_state == "rejected" and not (note and note.strip()):
        raise ValidationFailed(
            "give the reason for rejecting", next_action="The proprietor will see this reason."
        )
    if to_state == "approved":
        app.institution_id = await _approve(session, app)
    app.status = to_state
    app.decision_note = note.strip() if note and note.strip() else app.decision_note
    app.history = [
        *app.history,
        {
            "status": to_state,
            "at": utcnow().isoformat(),
            "by": str(ctx.user_id),
            "note": (note or "").strip() or None,
        },
    ]
    publish(
        session,
        EventEnvelope(
            event_type=events.REGISTRATION_DECIDED
            if to_state in ("approved", "rejected")
            else events.REGISTRATION_ADVANCED,
            tenant_id=app.tenant_id,
            scope_node_id=app.scope_node_id,
            aggregate_type="registration_application",
            aggregate_id=app.id,
            actor_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
            payload={"reference": app.reference, "status": to_state},
        ),
    )
    await session.flush()
    return await _view(session, app)


async def _approve(session: AsyncSession, app: RegistrationApplication) -> uuid.UUID:
    """Create the private school's tenant, its register entry and the proprietor's account."""
    ctx = current_context()
    allowed = frozenset(Level(v) for v in app.details["levels"])
    with use_context(replace(ctx, scope_node_id=app.scope_node_id)):
        clean = await _validated(
            session, register._as_raw(app.details), allowed
        )  # re-checked at decision time
    state = await governance.get_tenant(session, app.tenant_id)
    regulator = await governance.get_scope_node(session, app.scope_node_id)
    seq = await next_value(session, app.tenant_id, "institution")
    code = institution_id("NG", seq)
    school = await governance.create_child_tenant(
        session,
        parent_scope_id=uuid.UUID(clean.parent_scope_id),
        code=code,
        name=clean.name,
        level=GovernanceLevel.INSTITUTION,
        residency_region=state.residency_region,
        ownership=Ownership.PRIVATE,
        time_zone="Africa/Lagos",
        local_label=TYPE_LABEL[clean.institution_type],
    )
    inst = Institution(
        scope_node_id=school.root_scope_node_id,
        institution_code=code,
        name=clean.name,
        name_key=clean.name_key,
        parent_scope_id=uuid.UUID(clean.parent_scope_id),
        location=clean.location,
        location_key=fold(clean.location),
        institution_type=clean.institution_type.value,
        ownership="private",
        managing_body=regulator.name[:64],
        own_tenant_id=school.id,
        proprietor_name=app.proprietor_name,
        address_line1=clean.address_line1,
        address_line2=clean.address_line2,
        town=clean.town,
        postcode=clean.postcode,
        phone=clean.phone,
        email=clean.email,
        latitude=Decimal(str(clean.latitude)) if clean.latitude is not None else None,
        longitude=Decimal(str(clean.longitude)) if clean.longitude is not None else None,
    )
    session.add(inst)
    await session.flush()
    for lv in clean.levels:
        session.add(
            InstitutionLevel(
                scope_node_id=school.root_scope_node_id, institution_id=inst.id, level=lv.value
            )
        )
    publish(
        session,
        EventEnvelope(
            event_type=events.INSTITUTION_REGISTERED,
            tenant_id=app.tenant_id,
            scope_node_id=school.root_scope_node_id,
            aggregate_type="institution",
            aggregate_id=inst.id,
            actor_id=ctx.user_id,
            correlation_id=ctx.correlation_id,
            payload={
                "institution_code": code,
                "ownership": "private",
                "levels": [lv.value for lv in clean.levels],
            },
        ),
    )
    await session.flush()
    # The proprietor administers their own school (§2: private institutions control their roles).
    with use_context(replace(ctx, tenant_id=school.id, scope_node_id=school.root_scope_node_id)):
        user = await iam.create_user(
            session,
            tenant_id=school.id,
            login=app.proprietor_email,
            display_name=app.proprietor_name,
        )
        await iam.grant_role(
            session, user_id=user.id, role_code="IADM", scope_node_id=school.root_scope_node_id
        )
    return inst.id
