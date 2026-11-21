import uuid
from datetime import date, datetime

import pytest

from syntegra.core.errors import Forbidden, InvariantViolation, ValidationFailed
from syntegra.iam.baseline import BASELINE, MATRIX_COLUMNS, PERMISSIONS
from syntegra.iam.domain import Cell, Clearance, GrantFacts, evaluate, validate_custom_role
from syntegra.workflow.domain import (
    CATALOGUE,
    Sla,
    SlaStatus,
    SlaUnit,
    add_working_days,
    sla_status,
    sla_window,
    validate_transition,
)


def test_baseline_matrix_spot_checks_against_section_21():
    assert BASELINE["TCH"]["learner:view"] is Cell.OWN
    assert BASELINE["DSL"]["safeguarding_case:view"] is Cell.YES
    assert "safeguarding_case:view" not in BASELINE["TCH"]
    assert BASELINE["STA"]["learner:view"] is Cell.AGGREGATE
    assert BASELINE["CERT"]["certificate:revoke"] is Cell.PARTIAL
    assert BASELINE["STU"] == {
        "learner:view": Cell.OWN,
        "lms_submission:submit": Cell.OWN,
        "safeguarding_concern:record": Cell.PARTIAL,
        "invoice:view_pay": Cell.PARTIAL,
    }
    assert set(MATRIX_COLUMNS) <= set(BASELINE)


S = {k: uuid.UUID(int=i) for i, k in enumerate(["state", "school", "campus", "other"], 1)}


def grant(role, scope, cap=Clearance.SPECIAL, frm=date(2026, 1, 1), to=None):
    return GrantFacts(uuid.uuid4(), role, BASELINE[role], S[scope], frm, to, cap)


def test_scope_union_and_ancestry():
    perm = PERMISSIONS["learner:view"]
    ancestors = {S["state"], S["school"], S["campus"]}
    d = evaluate(
        perm, [grant("TCH", "campus"), grant("HEAD", "school")], ancestors, date(2026, 6, 1)
    )
    assert d.allowed and d.cell is Cell.YES  # broadest wins
    d = evaluate(perm, [grant("HEAD", "other")], ancestors, date(2026, 6, 1))
    assert not d.allowed


def test_validity_and_clearance_cap():
    perm = PERMISSIONS["safeguarding_case:view"]
    anc = {S["school"]}
    assert not evaluate(
        perm, [grant("DSL", "school", to=date(2026, 3, 1))], anc, date(2026, 6, 1)
    ).allowed
    assert not evaluate(
        perm, [grant("DSL", "school", cap=Clearance.RESTRICTED)], anc, date(2026, 6, 1)
    ).allowed
    assert evaluate(perm, [grant("DSL", "school")], anc, date(2026, 6, 1)).allowed
    with pytest.raises(Forbidden):
        evaluate(perm, [grant("TCH", "school")], anc, date(2026, 6, 1)).require()


def test_custom_role_never_exceeds_creator():
    with pytest.raises(Forbidden):
        validate_custom_role(
            source_builtin=True,
            editing_builtin=False,
            permissions={"safeguarding_case:view": Cell.YES},
            catalogue=PERMISSIONS,
            creator_clearance=Clearance.CONFIDENTIAL,
        )
    with pytest.raises(ValidationFailed):
        validate_custom_role(
            source_builtin=True,
            editing_builtin=True,
            permissions={},
            catalogue=PERMISSIONS,
            creator_clearance=Clearance.SPECIAL,
        )


def test_working_day_sla_and_reminder():
    assert add_working_days(date(2026, 11, 20), 1) == date(2026, 11, 23)  # Fri → Mon
    w = sla_window(Sla(10, SlaUnit.WORKING_DAYS), datetime(2026, 11, 2, 9))
    assert w.due_at == datetime(2026, 11, 16, 9)
    assert sla_status(w, datetime(2026, 11, 3)) is SlaStatus.ON_TRACK
    assert sla_status(w, datetime(2026, 11, 13)) is SlaStatus.REMIND
    assert sla_status(w, datetime(2026, 11, 16, 9)) is SlaStatus.BREACHED


def test_workflow_transitions_and_four_eyes():
    wf = CATALOGUE["certificate_lifecycle"]
    validate_transition(wf, "request", "evidence", actor="a", previous_actor=None)
    with pytest.raises(ValidationFailed) as exc:
        validate_transition(wf, "request", "effect", actor="a", previous_actor=None)
    assert exc.value.next_action == "Allowed next steps: evidence."
    with pytest.raises(InvariantViolation, match="four-eyes"):
        validate_transition(wf, "evidence", "approval", actor="a", previous_actor="a")
    with pytest.raises(InvariantViolation):
        validate_transition(wf, "holder_notified", "request", actor="a", previous_actor=None)
