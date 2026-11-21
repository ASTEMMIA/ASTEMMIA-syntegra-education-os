"""Publish-time policy checks (§29.1): tests must pass, overrides only tighten, no conflicts."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from syntegra.core.errors import Conflict, ValidationFailed
from syntegra.governance.policy.dsl import PolicyEvaluationError, compile_rule

Direction = Literal["min", "max"]  # "min": larger is stricter; "max": smaller is stricter
MAX_EXCEPTION_DAYS = 366  # policy exceptions are time-boxed to 12 months


@dataclass(frozen=True, slots=True)
class RuleTestCase:
    name: str
    facts: Mapping[str, Any]
    expect: bool


@dataclass(frozen=True, slots=True)
class RuleSpec:
    rule_key: str
    owner_scope_id: uuid.UUID
    expression: str
    valid_from: date
    valid_to: date | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    strictness: Mapping[str, Direction] = field(default_factory=dict)
    tests: tuple[RuleTestCase, ...] = ()
    mandatory_below: bool = True


@dataclass(frozen=True, slots=True)
class TestFailure:
    name: str
    message: str


def run_tests(spec: RuleSpec) -> list[TestFailure]:
    rule = compile_rule(spec.expression)
    missing = rule.params - set(spec.parameters)
    if missing:
        return [TestFailure("<parameters>", f"undeclared parameters: {sorted(missing)}")]
    failures: list[TestFailure] = []
    for case in spec.tests:
        try:
            got = rule.evaluate(case.facts, spec.parameters)
        except PolicyEvaluationError as exc:
            failures.append(TestFailure(case.name, f"could not evaluate: {exc}"))
            continue
        if got is not case.expect:
            failures.append(TestFailure(case.name, f"expected {case.expect}, got {got}"))
    return failures


def check_publishable(spec: RuleSpec) -> None:
    if not spec.tests:
        raise ValidationFailed(
            f"rule {spec.rule_key} has no test cases",
            next_action="Add at least one passing and one failing sample record.",
        )
    failures = run_tests(spec)
    if failures:
        raise ValidationFailed(
            f"rule {spec.rule_key} failed {len(failures)} test(s)",
            next_action="Fix the expression or the test cases, then publish again.",
            failures=[{"name": f.name, "message": f.message} for f in failures],
        )


def check_tightens(parent: RuleSpec, child: RuleSpec) -> None:
    """A rule may be tightened below its owner but never relaxed (§29)."""
    if not parent.mandatory_below:
        return
    if parent.expression.strip() != child.expression.strip():
        raise ValidationFailed(
            f"{child.rule_key} is mandatory below its owner; only parameters may be tightened",
            next_action="Keep the owner's expression and change parameter values instead.",
        )
    for name, direction in parent.strictness.items():
        p, c = parent.parameters.get(name), child.parameters.get(name)
        if p is None or c is None:
            continue
        relaxed = c < p if direction == "min" else c > p
        if relaxed:
            raise ValidationFailed(
                f"{child.rule_key}: {name}={c} relaxes the owner's {name}={p}",
                next_action="Request a policy exception from the level above instead.",
            )


def _overlaps(a: RuleSpec, b: RuleSpec) -> bool:
    a_end, b_end = a.valid_to or date.max, b.valid_to or date.max
    return a.valid_from <= b_end and b.valid_from <= a_end


def detect_conflicts(candidate: RuleSpec, published: Iterable[RuleSpec]) -> None:
    for other in published:
        if (
            other.rule_key == candidate.rule_key
            and other.owner_scope_id == candidate.owner_scope_id
            and _overlaps(other, candidate)
        ):
            raise Conflict(
                f"{candidate.rule_key} is already in force at this scope for an overlapping period",
                next_action="End-date the current version before publishing a new one.",
            )


def validate_exception_window(start: date, end: date) -> None:
    if end < start or (end - start).days > MAX_EXCEPTION_DAYS:
        raise ValidationFailed(
            "policy exceptions last at most 12 months",
            next_action="Shorten the exception and renew it when it is reviewed.",
        )
