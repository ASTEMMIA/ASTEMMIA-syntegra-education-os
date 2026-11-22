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
    # HRMS (ADR-024): runs staff records and postings for an employer (e.g. ASUBEB HR).
    "HRO": "HR officer",
    # Records (ADR-027): the State Archives oversee the permanent repository with the State
    # Government; capture operators scan in-house or for a contractor, into assigned batches.
    "ARCH": "State Archives officer",
    "CAPT": "Capture operator (in-house or contractor)",
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
        # Staff 360 (hr, ADR-024)
        PermissionDef("staff:view", "Staff 360 — view (nominal roll)", T.CONFIDENTIAL),
        PermissionDef("staff:edit", "Staff 360 — appoint, edit, exit", T.CONFIDENTIAL),
        PermissionDef(
            "staff:post", "Staff postings — request and decide (four-eyes)", T.CONFIDENTIAL
        ),
        # Appraisal (hr, ADR-025)
        PermissionDef("appraisal:view", "Appraisal — view", T.CONFIDENTIAL),
        PermissionDef(
            "appraisal:conduct", "Appraisal — appraise and observe lessons", T.CONFIDENTIAL
        ),
        PermissionDef("appraisal:countersign", "Appraisal — countersign or return", T.CONFIDENTIAL),
        # Continuous professional development (hr, ADR-026)
        PermissionDef("cpd:view", "CPD — view records and compliance", T.INTERNAL),
        PermissionDef(
            "cpd:manage", "CPD — programmes, nominations, verification, plans", T.INTERNAL
        ),
        # Document and records management (rec, ADR-027). Reading is by classification tier.
        PermissionDef("document:view_internal", "Documents — read Internal", T.INTERNAL),
        PermissionDef(
            "document:view_confidential", "Documents — read Confidential", T.CONFIDENTIAL
        ),
        PermissionDef("document:view_restricted", "Documents — read Restricted", T.RESTRICTED),
        PermissionDef("document:view_special", "Documents — read Special", T.SPECIAL),
        PermissionDef("document:capture", "Documents — scan and upload", T.CONFIDENTIAL),
        PermissionDef("file:manage", "Paper files — open, track, close", T.INTERNAL),
        PermissionDef(
            "records:admin", "Records — file plan, batches, holds, disposition", T.CONFIDENTIAL
        ),
        PermissionDef(
            "records:oversight", "Records — State oversight of the repository", T.SPECIAL
        ),
        # Learner registration and classes (learners, academic; ADR-029)
        PermissionDef(
            "learner:register",
            "Learners — applications, enrolment, class placement, transfers",
            T.CONFIDENTIAL,
        ),
        PermissionDef("admission:decide", "Admissions — offer, waitlist or reject", T.CONFIDENTIAL),
        PermissionDef(
            "class:manage", "Classes — rooms, arms, class teachers, subjects", T.INTERNAL
        ),
        PermissionDef("calendar:manage", "Academic calendar — sessions and terms", T.INTERNAL),
        # Messaging (msg, ADR-030)
        PermissionDef("message:moderate", "Messaging — handle reported messages", T.CONFIDENTIAL),
        PermissionDef(
            "message:oversee", "Messaging — review a conversation, with a reason", T.CONFIDENTIAL
        ),
        PermissionDef(
            "message:oversee_protected",
            "Messaging — review a protected (pastoral, HR) conversation",
            T.RESTRICTED,
        ),
        PermissionDef(
            "teaching:assign", "Teaching — assign subject teachers and lecturers", T.INTERNAL
        ),
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
    "STA": {
        "message:oversee": Cell.YES,
        "calendar:manage": Cell.YES,
        "tenant:manage": Cell.YES,
        "scope_node:manage": Cell.YES,
        "staff:view": Cell.YES,
        "staff:post": Cell.YES,
        "appraisal:view": Cell.YES,
        "appraisal:countersign": Cell.YES,
        "cpd:view": Cell.YES,
        "cpd:manage": Cell.YES,
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:view_restricted": Cell.YES,
        "document:view_special": Cell.YES,
        "records:oversight": Cell.YES,
    },
    "ARCH": {
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:view_restricted": Cell.YES,
        "document:view_special": Cell.YES,
        "records:oversight": Cell.YES,
        "records:admin": Cell.YES,
        "disposal:approve": Cell.YES,
        "audit:view": Cell.YES,
    },
    "CAPT": {"document:capture": Cell.YES},
    "REC": {
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:view_restricted": Cell.PARTIAL,
        "document:capture": Cell.YES,
        "file:manage": Cell.YES,
        "records:admin": Cell.YES,
    },
    "HRO": {
        "appraisal:view": Cell.YES,
        "appraisal:countersign": Cell.YES,
        "staff:view": Cell.YES,
        "staff:edit": Cell.YES,
        "staff:post": Cell.YES,
        "person:view": Cell.YES,
        "person:create": Cell.YES,
        "person:edit": Cell.YES,
        "cpd:view": Cell.YES,
        "cpd:manage": Cell.YES,
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:view_restricted": Cell.YES,  # HR casework
    },
    "IADM": {
        "message:moderate": Cell.YES,
        "learner:register": Cell.YES,
        "admission:decide": Cell.PARTIAL,
        "class:manage": Cell.YES,
        "calendar:manage": Cell.YES,  # a tertiary institution sets its own semesters
        "teaching:assign": Cell.YES,
        "scope_node:manage": Cell.YES,
        "person:view": Cell.YES,
        "person:create": Cell.YES,
        "person:edit": Cell.YES,
        "person:merge": Cell.YES,
        "staff:view": Cell.YES,
        "staff:edit": Cell.YES,
        "appraisal:view": Cell.YES,
        "appraisal:conduct": Cell.YES,
        "cpd:view": Cell.YES,
        "cpd:manage": Cell.YES,
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:capture": Cell.YES,
        "file:manage": Cell.YES,
        "records:admin": Cell.YES,
    },
    "HEAD": {
        "message:moderate": Cell.YES,
        "message:oversee": Cell.YES,
        "learner:register": Cell.YES,
        "admission:decide": Cell.YES,
        "class:manage": Cell.YES,
        "teaching:assign": Cell.YES,
        "scope_node:manage": Cell.YES,
        "person:view": Cell.YES,
        "staff:view": Cell.YES,
        "appraisal:view": Cell.YES,
        "appraisal:conduct": Cell.YES,
        "cpd:view": Cell.YES,
        "cpd:manage": Cell.YES,
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:view_restricted": Cell.PARTIAL,
        "file:manage": Cell.YES,
        "records:admin": Cell.YES,
    },
    # Inspectors observe lessons (§29) and may appraise where the state assigns them.
    "INSP": {
        "appraisal:view": Cell.YES,
        "appraisal:conduct": Cell.YES,
        "cpd:view": Cell.YES,
        "document:view_internal": Cell.YES,
    },
    # Registrar is the Person 360 data steward (§4 "Registry officer", §16.1).
    "REG": {
        "learner:register": Cell.YES,
        "person:view": Cell.YES,
        "person:create": Cell.YES,
        "person:edit": Cell.YES,
        "person:merge": Cell.YES,
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:capture": Cell.YES,
    },
    "TCH": {"person:view": Cell.OWN, "document:view_internal": Cell.YES},
    "DPO": {  # subject access requests (§8.4)
        "message:oversee": Cell.YES,
        "message:oversee_protected": Cell.YES,
        "person:view": Cell.YES,
        "document:view_internal": Cell.YES,
        "document:view_confidential": Cell.YES,
        "document:view_restricted": Cell.YES,
    },
    "DSL": {
        "document:view_restricted": Cell.YES,
        "document:view_special": Cell.YES,
        "message:moderate": Cell.YES,
        "message:oversee": Cell.YES,
        "message:oversee_protected": Cell.YES,
    },
    "SENCO": {"document:view_restricted": Cell.PARTIAL},
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
