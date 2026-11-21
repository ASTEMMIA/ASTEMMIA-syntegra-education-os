"""Pure access rules: RBAC + ABAC condition + scope + clearance (CEDA §8.1-§8.2, §21)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import IntEnum, StrEnum

from syntegra.core.errors import Forbidden, ValidationFailed


class Clearance(IntEnum):
    """Data classification tiers (§8.1), lowest to highest."""

    PUBLIC = 1
    INTERNAL = 2
    CONFIDENTIAL = 3
    RESTRICTED = 4
    SPECIAL = 5


class Cell(StrEnum):
    """Matrix cells (§21). Ordered by breadth for the union of grants."""

    YES = "Y"  # allowed
    PARTIAL = "P"  # partial or conditional (approval only, own institution)
    OWN = "C"  # own classes or learners only
    AGGREGATE = "A"  # aggregated only


_BREADTH = {Cell.YES: 4, Cell.PARTIAL: 3, Cell.OWN: 2, Cell.AGGREGATE: 1}

CONDITION = {
    Cell.YES: None,
    Cell.PARTIAL: "conditional",
    Cell.OWN: "own_classes_only",
    Cell.AGGREGATE: "aggregated_only",
}


@dataclass(frozen=True, slots=True)
class PermissionDef:
    code: str  # resource:action
    label: str
    tier: Clearance  # the classification of the data this permission reaches


@dataclass(frozen=True, slots=True)
class GrantFacts:
    grant_id: uuid.UUID
    role_code: str
    permissions: Mapping[str, Cell]
    scope_node_id: uuid.UUID
    valid_from: date
    valid_to: date | None
    clearance_cap: Clearance

    def active_on(self, on: date) -> bool:
        return self.valid_from <= on and (self.valid_to is None or on <= self.valid_to)


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    permission: str
    cell: Cell | None = None
    via_grant: uuid.UUID | None = None
    reason: str = ""

    @property
    def condition(self) -> str | None:
        return CONDITION[self.cell] if self.cell else None

    def require(self) -> Decision:
        if not self.allowed:
            raise Forbidden(
                f"you do not have {self.permission} here",
                next_action="Ask your administrator for a role at this scope.",
            )
        return self


def evaluate(
    permission: PermissionDef,
    grants: Iterable[GrantFacts],
    target_ancestors: set[uuid.UUID],
    on: date,
) -> Decision:
    """Effective rights are the union of grants whose scope is an ancestor of the target (§8.2)."""
    best: tuple[int, GrantFacts, Cell] | None = None
    reason = "no grant covers this scope"
    for g in grants:
        if g.scope_node_id not in target_ancestors:
            continue
        if not g.active_on(on):
            reason = "grant is not in its validity period"
            continue
        cell = g.permissions.get(permission.code)
        if cell is None:
            reason = "role does not include this permission"
            continue
        if permission.tier > g.clearance_cap:
            reason = "grant clearance is below the data classification"
            continue
        rank = _BREADTH[cell]
        if best is None or rank > best[0]:
            best = (rank, g, cell)
    if best is None:
        return Decision(False, permission.code, reason=reason)
    return Decision(True, permission.code, best[2], best[1].grant_id, "granted")


def role_clearance(
    permissions: Mapping[str, Cell], catalogue: Mapping[str, PermissionDef]
) -> Clearance:
    tiers = [catalogue[p].tier for p in permissions]
    return max(tiers, default=Clearance.PUBLIC)


def validate_custom_role(
    *,
    source_builtin: bool,
    editing_builtin: bool,
    permissions: Mapping[str, Cell],
    catalogue: Mapping[str, PermissionDef],
    creator_clearance: Clearance,
) -> Clearance:
    """Built-ins are cloned, never edited; a custom role never exceeds its creator (§21)."""
    del source_builtin
    if editing_builtin:
        raise ValidationFailed(
            "built-in roles cannot be edited", next_action="Clone the role and edit the copy."
        )
    unknown = set(permissions) - set(catalogue)
    if unknown:
        raise ValidationFailed(f"unknown permissions: {sorted(unknown)}")
    clearance = role_clearance(permissions, catalogue)
    if clearance > creator_clearance:
        raise Forbidden(
            "a custom role cannot exceed the clearance of the administrator who creates it",
            next_action="Remove the higher-classified permissions or ask a senior administrator.",
        )
    return clearance
