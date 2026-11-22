"""Small helpers the routers share."""

from __future__ import annotations

from typing import Any

import orjson

from syntegra.core.errors import Forbidden
from syntegra.iam.domain import Decision
from syntegra.web.deps import UowDep


def dump(obj: Any) -> Any:
    """Dataclass views as JSON-ready values (uuids, dates and decimals as strings)."""
    return orjson.loads(orjson.dumps(obj, default=_default))


def _default(o: Any) -> Any:
    if hasattr(o, "__dataclass_fields__"):
        return {k: getattr(o, k) for k in o.__dataclass_fields__}
    if isinstance(o, tuple | set | frozenset):
        return list(o)
    from decimal import Decimal

    if isinstance(o, Decimal):
        return str(o)
    raise TypeError


async def require_any(uow: UowDep, *permissions: str) -> Decision:
    """Allowed if the caller holds any one of the permissions at the active scope."""
    for p in permissions:
        try:
            return await uow.require(p)
        except Forbidden:
            continue
    raise Forbidden(
        f"you need one of {', '.join(permissions)} here",
        next_action="Ask your administrator for a role at this scope.",
    )


async def my_staff_ids(uow: UowDep) -> list[Any]:
    """The signed-in person's staff records (a teacher's own classes and subjects)."""
    from syntegra.academic import api as acad
    from syntegra.iam import api as iam

    person = await iam.person_of_user(uow.session, uow.principal.user_id)
    return await acad.staff_of_person(uow.session, person) if person else []


async def is_parent_only(uow: UowDep) -> bool:
    from syntegra.iam import api as iam

    grants = await iam.grants_for(uow.session, uow.principal.user_id)
    return bool(grants) and all(g.role_code == "PAR" for g in grants)


async def may_view_learner(uow: UowDep, learner_id: Any) -> None:
    """Staff with learner:view (teachers: their own classes only), or a parent of the child with
    parental responsibility. Anyone else is told the learner is not found."""
    from syntegra.academic import api as acad
    from syntegra.assessment import api as assessment
    from syntegra.core.errors import NotFound
    from syntegra.iam import api as iam
    from syntegra.learners import api as lrn

    if await is_parent_only(uow):
        person = await iam.person_of_user(uow.session, uow.principal.user_id)
        if learner_id not in await assessment.children_of(uow.session, person):
            raise NotFound("learner not found")
        return
    d = await uow.require("learner:view")
    if d.condition == "own_classes_only":
        v = await lrn.get_learner(uow.session, learner_id)
        mine = {c.id for c in await acad.classes_taught(uow.session, await my_staff_ids(uow))}
        if not (v.current and v.current.class_id in mine):
            raise NotFound("learner not found in your classes")
