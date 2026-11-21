import uuid
from datetime import date

import pytest

from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.governance.policy.dsl import PolicyEvaluationError, compile_rule
from syntegra.governance.policy.engine import (
    RuleSpec,
    RuleTestCase,
    check_publishable,
    check_tightens,
    detect_conflicts,
    validate_exception_window,
)


def test_spec_example_rule():
    rule = compile_rule("learner.attendance_rate_6w < 0.90 and learner.age >= 6")
    assert rule.facts == {"learner.attendance_rate_6w", "learner.age"}
    assert rule.evaluate({"learner.attendance_rate_6w": 0.85, "learner.age": 7})
    assert not rule.evaluate({"learner.attendance_rate_6w": 0.95, "learner.age": 7})


def test_parameters_lists_functions_and_literals():
    rule = compile_rule("abs(x.delta) > limit and x.code in ['A', 'U'] and not x.excused == true")
    assert rule.params == {"limit"}
    assert rule.evaluate({"x.delta": -5, "x.code": "U", "x.excused": False}, {"limit": 3})


@pytest.mark.parametrize(
    "src",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "x.__class__",
        "[c for c in x.items]",
        "lambda: 1",
        "x.items[0]",
        "(1).__class__",
        "exec('1')",
        "x.a if x.b else x.c",
    ],
)
def test_unsafe_constructs_are_rejected(src):
    with pytest.raises(ValidationFailed):
        compile_rule(src)


def test_missing_fact_is_unknown_not_false():
    with pytest.raises(PolicyEvaluationError):
        compile_rule("learner.age >= 6").evaluate({})


def _spec(**kw):
    base = dict(
        rule_key="ATT-INST-02",
        owner_scope_id=uuid.UUID(int=1),
        expression="learner.attendance_rate_6w < min_rate",
        valid_from=date(2026, 1, 1),
        parameters={"min_rate": 0.9},
        strictness={"min_rate": "min"},
        tests=(
            RuleTestCase("low", {"learner.attendance_rate_6w": 0.5}, True),
            RuleTestCase("ok", {"learner.attendance_rate_6w": 0.99}, False),
        ),
    )
    base.update(kw)
    return RuleSpec(**base)


def test_publish_requires_passing_tests():
    check_publishable(_spec())
    with pytest.raises(ValidationFailed, match="failed 1"):
        check_publishable(
            _spec(tests=(RuleTestCase("wrong", {"learner.attendance_rate_6w": 0.5}, False),))
        )
    with pytest.raises(ValidationFailed, match="no test cases"):
        check_publishable(_spec(tests=()))


def test_tighten_but_never_relax():
    parent = _spec()
    check_tightens(parent, _spec(owner_scope_id=uuid.UUID(int=2), parameters={"min_rate": 0.95}))
    with pytest.raises(ValidationFailed, match="relaxes"):
        check_tightens(parent, _spec(owner_scope_id=uuid.UUID(int=2), parameters={"min_rate": 0.8}))
    with pytest.raises(ValidationFailed, match="only parameters"):
        check_tightens(parent, _spec(expression="learner.attendance_rate_6w < 0.5"))


def test_conflicts_and_exception_window():
    with pytest.raises(Conflict):
        detect_conflicts(_spec(), [_spec()])
    detect_conflicts(_spec(valid_from=date(2027, 1, 1)), [_spec(valid_to=date(2026, 12, 31))])
    with pytest.raises(ValidationFailed):
        validate_exception_window(date(2026, 1, 1), date(2027, 6, 1))
