"""The release-baseline role and permission matrix (CEDA §21), transcribed as data.

Permissions are data (role_permission). This module is the seed; tenants clone built-ins.
"""

from __future__ import annotations

from collections.abc import Mapping

from syntegra.iam.domain import Cell, Clearance, PermissionDef

ROLES: dict[str, str] = {
    "TCH": "Teacher",
    "PAST": "Pastoral lead",
    "DSL": "Designated Safeguarding Lead",
    "SENCO": "SEND coordinator",
    "REG": "Registrar",
    "FINO": "Finance officer",
    "IADM": "Institution administrator",
    "HEAD": "Head / principal",
    "CURA": "Curriculum authority",
    "CERT": "Certificate authority",
    "INSP": "Inspector",
    "STA": "State officer",
    "FED": "Federal officer",
    "DPO": "Data Protection Officer",
    "PAR": "Parent",
    "STU": "Student",
    "REC": "Records officer",
    # Phase 0 extension (ADR-004): the programme administrator at the global level (§2 level 1).
    "PADM": "Platform administrator",
}

MATRIX_COLUMNS = (
    "TCH",
    "PAST",
    "DSL",
    "SENCO",
    "REG",
    "FINO",
    "IADM",
    "HEAD",
    "CURA",
    "CERT",
    "INSP",
    "STA",
    "FED",
    "DPO",
    "PAR",
    "STU",
    "REC",
)

T = Clearance
PERMISSIONS: dict[str, PermissionDef] = {
    p.code: p
    for p in [
        PermissionDef("learner:view", "Learner 360 — view", T.CONFIDENTIAL),
        PermissionDef("learner_national_id:reveal", "Learner national ID — reveal", T.RESTRICTED),
        PermissionDef("attendance:mark", "Attendance — mark", T.CONFIDENTIAL),
        PermissionDef("markbook:edit", "Markbook / gradebook — edit", T.CONFIDENTIAL),
        PermissionDef("lms_assignment:create", "LMS assignment — create", T.INTERNAL),
        PermissionDef("lms_submission:submit", "LMS submission — submit", T.INTERNAL),
        PermissionDef("safeguarding_concern:record", "Safeguarding — record concern", T.INTERNAL),
        PermissionDef("safeguarding_case:view", "Safeguarding — view case", T.SPECIAL),
        PermissionDef("send_profile:view", "SEND — full profile", T.RESTRICTED),
        PermissionDef("send_adjustments:view", "SEND — adjustments", T.CONFIDENTIAL),
        PermissionDef("payment:record", "Billing — record payment", T.CONFIDENTIAL),
        PermissionDef("fee_rules:publish", "Fee rules — publish", T.INTERNAL),
        PermissionDef("invoice:view_pay", "Invoices — view and pay", T.CONFIDENTIAL),
        PermissionDef("curriculum:author", "Curriculum — author", T.INTERNAL),
        PermissionDef("curriculum:publish", "Curriculum — publish", T.INTERNAL),
        PermissionDef("certificate:issue", "Certificate — issue", T.CONFIDENTIAL),
        PermissionDef("certificate:revoke", "Certificate — revoke (four-eyes)", T.CONFIDENTIAL),
        PermissionDef("institution:edit", "Institution master — edit", T.INTERNAL),
        PermissionDef("inspection_finding:record", "Inspection — record finding", T.INTERNAL),
        PermissionDef("document:index", "Documents — index and validate", T.CONFIDENTIAL),
        PermissionDef("disposal:approve", "Records — approve disposal", T.CONFIDENTIAL),
        PermissionDef("message:broadcast", "Messaging — broadcast", T.INTERNAL),
        PermissionDef("user:manage", "Users — manage", T.CONFIDENTIAL),
        PermissionDef("audit:view", "Audit — view", T.CONFIDENTIAL),
        PermissionDef("policy:publish", "Policy — publish", T.INTERNAL),
        # Phase 0 platform permissions (ADR-004)
        PermissionDef("tenant:manage", "Tenants — create and configure", T.INTERNAL),
        PermissionDef("scope_node:manage", "Scope hierarchy — manage", T.INTERNAL),
        # Person 360 (mdm, ADR-008). Revealing a national ID uses learner_national_id:reveal.
        PermissionDef("person:view", "Person 360 — view", T.CONFIDENTIAL),
        PermissionDef("person:create", "Person 360 — register", T.CONFIDENTIAL),
        PermissionDef("person:edit", "Person 360 — edit", T.CONFIDENTIAL),
        PermissionDef("person:merge", "Person 360 — resolve duplicates and merge", T.CONFIDENTIAL),
    ]
}

# Rows exactly as §21, columns in MATRIX_COLUMNS order. "--" = none.
_MATRIX = """
learner:view                 C  C  Y  Y  Y  P  Y  Y  -- P  P  A  A  -- C  C  P
learner_national_id:reveal   -- -- -- -- Y  -- Y  P  -- Y  -- -- -- -- -- -- --
attendance:mark              C  Y  -- -- Y  -- Y  Y  -- -- -- -- -- -- -- -- --
markbook:edit                C  -- -- -- -- -- P  P  -- -- -- -- -- -- -- -- --
lms_assignment:create        C  -- -- -- -- -- -- P  P  -- -- -- -- -- -- -- --
lms_submission:submit        -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- C  --
safeguarding_concern:record  Y  Y  Y  Y  Y  Y  Y  Y  -- -- Y  -- -- -- P  P  --
safeguarding_case:view       -- -- Y  -- -- -- -- P  -- -- -- P  -- -- -- -- --
send_profile:view            -- -- P  Y  -- -- -- P  -- -- -- A  -- -- P  -- --
send_adjustments:view        C  Y  Y  Y  -- -- -- Y  -- -- -- -- -- -- C  -- --
payment:record               -- -- -- -- -- Y  P  -- -- -- -- -- -- -- -- -- --
fee_rules:publish            -- -- -- -- -- Y  -- P  -- -- -- P  -- -- -- -- --
invoice:view_pay             -- -- -- -- -- Y  P  P  -- -- -- A  A  -- C  P  --
curriculum:author            -- -- -- -- -- -- -- -- Y  -- -- -- -- -- -- -- --
curriculum:publish           -- -- -- -- -- -- -- -- P  -- -- Y  Y  -- -- -- --
certificate:issue            -- -- -- -- -- -- -- -- -- Y  -- -- -- -- -- -- --
certificate:revoke           -- -- -- -- -- -- -- -- -- P  -- -- -- -- -- -- --
institution:edit             -- -- -- -- -- -- P  P  -- -- -- Y  Y  -- -- -- --
inspection_finding:record    -- -- -- -- -- -- -- -- -- -- Y  -- -- -- -- -- --
document:index               -- -- -- -- P  -- P  -- -- -- -- -- -- -- -- -- Y
disposal:approve             -- -- -- -- -- -- -- P  -- -- -- P  -- P  -- -- P
message:broadcast            -- P  -- -- -- -- Y  Y  -- -- -- Y  Y  -- -- -- --
user:manage                  -- -- -- -- -- -- Y  P  -- -- -- P  P  -- -- -- --
audit:view                   -- -- -- -- -- -- P  P  -- -- -- -- -- Y  -- -- --
policy:publish               -- -- -- -- -- -- P  -- -- -- -- Y  Y  -- -- -- --
"""

# Phase 0 extension rows (not in §21; ADR-004).
_EXTENSIONS: dict[str, dict[str, Cell]] = {
    "PADM": {
        "tenant:manage": Cell.YES,
        "scope_node:manage": Cell.YES,
        "user:manage": Cell.YES,
        "audit:view": Cell.YES,
        "policy:publish": Cell.YES,
    },
    "FED": {"tenant:manage": Cell.YES, "scope_node:manage": Cell.YES},
    "STA": {"tenant:manage": Cell.YES, "scope_node:manage": Cell.YES},
    "IADM": {
        "scope_node:manage": Cell.YES,
        "person:view": Cell.YES,
        "person:create": Cell.YES,
        "person:edit": Cell.YES,
        "person:merge": Cell.YES,
    },
    "HEAD": {"scope_node:manage": Cell.YES, "person:view": Cell.YES},
    # Registrar is the Person 360 data steward (§4 "Registry officer", §16.1).
    "REG": {
        "person:view": Cell.YES,
        "person:create": Cell.YES,
        "person:edit": Cell.YES,
        "person:merge": Cell.YES,
    },
    "TCH": {"person:view": Cell.OWN},
    "DPO": {"person:view": Cell.YES},  # subject access requests (§8.4)
}


def _parse() -> dict[str, dict[str, Cell]]:
    roles: dict[str, dict[str, Cell]] = {r: {} for r in ROLES}
    for line in _MATRIX.strip().splitlines():
        perm, *cells = line.split()
        if perm not in PERMISSIONS or len(cells) != len(MATRIX_COLUMNS):
            raise RuntimeError(f"malformed baseline matrix row: {perm}")
        for role, cell in zip(MATRIX_COLUMNS, cells, strict=True):
            if cell != "--":
                roles[role][perm] = Cell(cell)
    for role, perms in _EXTENSIONS.items():
        roles[role].update(perms)
    return roles


BASELINE: Mapping[str, Mapping[str, Cell]] = _parse()
