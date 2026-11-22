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
