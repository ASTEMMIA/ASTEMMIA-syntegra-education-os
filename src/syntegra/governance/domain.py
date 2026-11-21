"""Pure governance rules: the 10-level hierarchy and tenancy (CEDA §2). No framework imports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from syntegra.core.errors import ValidationFailed


class GovernanceLevel(IntEnum):
    GLOBAL = 1
    COUNTRY = 2
    REGION = 3
    LOCAL_AUTHORITY = 4
    NETWORK = 5
    INSTITUTION = 6
    CAMPUS = 7
    UNIT = 8
    CLASS = 9
    # Level 10 (learner) is a person, not a scope node.


# Levels at which a node may be a tenant boundary (§2 governance table).
TENANT_LEVELS = frozenset(
    {
        GovernanceLevel.GLOBAL,
        GovernanceLevel.COUNTRY,
        GovernanceLevel.REGION,
        GovernanceLevel.NETWORK,
        GovernanceLevel.INSTITUTION,
    }
)


class TenantKind(StrEnum):
    GLOBAL = "global"
    COUNTRY = "country"
    REGIONAL = "regional"
    NETWORK = "network"
    INSTITUTIONAL = "institutional"


KIND_FOR_LEVEL = {
    GovernanceLevel.GLOBAL: TenantKind.GLOBAL,
    GovernanceLevel.COUNTRY: TenantKind.COUNTRY,
    GovernanceLevel.REGION: TenantKind.REGIONAL,
    GovernanceLevel.NETWORK: TenantKind.NETWORK,
    GovernanceLevel.INSTITUTION: TenantKind.INSTITUTIONAL,
}


class Ownership(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    NOT_APPLICABLE = "n/a"


PATH_SEP = "/"
MAX_PATH = 512


def child_path(parent_path: str | None, node_id: uuid.UUID) -> str:
    """Materialised path (§11.2 ltree replacement): ``/<hex>/<hex>/…``."""
    path = f"{parent_path or ''}{PATH_SEP}{node_id.hex}"
    if len(path) > MAX_PATH:
        raise ValidationFailed("scope hierarchy is too deep", next_action="Flatten the hierarchy.")
    return path


@dataclass(frozen=True, slots=True)
class ClosureRow:
    ancestor_id: uuid.UUID
    descendant_id: uuid.UUID
    depth: int


def closure_rows_for_new_node(
    node_id: uuid.UUID, parent_ancestry: list[ClosureRow]
) -> list[ClosureRow]:
    """Closure rows for a new leaf: itself at depth 0 plus every ancestor of its parent."""
    rows = [ClosureRow(node_id, node_id, 0)]
    rows += [ClosureRow(r.ancestor_id, node_id, r.depth + 1) for r in parent_ancestry]
    return rows


def validate_child_level(parent: GovernanceLevel | None, child: GovernanceLevel) -> None:
    if parent is None:
        if child is not GovernanceLevel.GLOBAL and child is not GovernanceLevel.COUNTRY:
            raise ValidationFailed(
                "a root scope must be global or country level",
                next_action="Create the node under an existing country or region.",
            )
        return
    if child <= parent:
        raise ValidationFailed(
            f"a {child.name.lower()} node cannot sit under a {parent.name.lower()} node",
            next_action="Choose a parent at a higher governance level.",
        )


def validate_tenant_level(level: GovernanceLevel) -> TenantKind:
    if level not in TENANT_LEVELS:
        raise ValidationFailed(
            f"{level.name.lower()} level cannot be a tenant",
            next_action="Create it as a scope node inside the owning tenant instead.",
        )
    return KIND_FOR_LEVEL[level]
