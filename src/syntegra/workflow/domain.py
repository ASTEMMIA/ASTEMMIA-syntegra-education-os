"""Pure workflow model. No framework imports."""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from syntegra.core.errors import InvariantViolation, ValidationFailed

REMINDER_FRACTION = 0.75
P1_ESCALATION = timedelta(hours=2)


class SlaUnit(StrEnum):
    HOURS = "hours"
    DAYS = "days"
    WORKING_DAYS = "working_days"


@dataclass(frozen=True, slots=True)
class Sla:
    amount: int
    unit: SlaUnit


@dataclass(frozen=True, slots=True)
class StepDef:
    state: str
    assignee_rule: str | None = None  # e.g. "role:DSL@institution"
    sla: Sla | None = None
    four_eyes: bool = False  # needs a second, different approver
    terminal: bool = False


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    key: str
    version: int
    steps: tuple[StepDef, ...]
    transitions: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = [s.state for s in self.steps]
        if len(set(names)) != len(names):
            raise ValidationFailed(f"{self.key}: duplicate states")
        known = set(names)
        for src, dsts in self.transitions.items():
            if src not in known or not dsts <= known:
                raise ValidationFailed(f"{self.key}: transition references an unknown state")
        if not any(s.terminal for s in self.steps):
            raise ValidationFailed(f"{self.key}: needs at least one terminal state")

    @property
    def initial(self) -> str:
        return self.steps[0].state

    def step(self, state: str) -> StepDef:
        for s in self.steps:
            if s.state == state:
                return s
        raise ValidationFailed(f"{self.key}: unknown state {state}")

    def allowed(self, state: str) -> frozenset[str]:
        return self.transitions.get(state, frozenset())


def linear(
    key: str, version: int, *steps: StepDef, extra: Mapping[str, Iterable[str]] | None = None
) -> WorkflowDefinition:
    """A linear definition: each state moves to the next, plus optional extra edges."""
    transitions: dict[str, set[str]] = {}
    for a, b in itertools.pairwise(steps):
        transitions.setdefault(a.state, set()).add(b.state)
    for src, dsts in (extra or {}).items():
        transitions.setdefault(src, set()).update(dsts)
    return WorkflowDefinition(
        key, version, tuple(steps), {k: frozenset(v) for k, v in transitions.items()}
    )


def add_working_days(start: date, days: int, holidays: frozenset[date] = frozenset()) -> date:
    d = start
    remaining = days
    while remaining > 0:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            remaining -= 1
    return d


@dataclass(frozen=True, slots=True)
class SlaWindow:
    started_at: datetime
    remind_at: datetime
    due_at: datetime


def sla_window(
    sla: Sla, started_at: datetime, holidays: frozenset[date] = frozenset()
) -> SlaWindow:
    if sla.unit is SlaUnit.HOURS:
        due = started_at + timedelta(hours=sla.amount)
    elif sla.unit is SlaUnit.DAYS:
        due = started_at + timedelta(days=sla.amount)
    else:
        due_date = add_working_days(started_at.date(), sla.amount, holidays)
        due = datetime.combine(due_date, started_at.time())
    remind = started_at + (due - started_at) * REMINDER_FRACTION
    return SlaWindow(started_at, remind, due)


class SlaStatus(StrEnum):
    ON_TRACK = "on_track"
    REMIND = "remind"
    BREACHED = "breached"


def sla_status(window: SlaWindow, now: datetime) -> SlaStatus:
    if now >= window.due_at:
        return SlaStatus.BREACHED
    if now >= window.remind_at:
        return SlaStatus.REMIND
    return SlaStatus.ON_TRACK


def validate_transition(
    definition: WorkflowDefinition,
    current: str,
    target: str,
    *,
    actor: str,
    previous_actor: str | None,
) -> StepDef:
    step = definition.step(current)
    if step.terminal:
        raise InvariantViolation(f"{definition.key} is already complete ({current})")
    if target not in definition.allowed(current):
        allowed = ", ".join(sorted(definition.allowed(current))) or "none"
        raise ValidationFailed(
            f"cannot move {definition.key} from {current} to {target}",
            next_action=f"Allowed next steps: {allowed}.",
        )
    target_step = definition.step(target)
    if target_step.four_eyes and previous_actor is not None and previous_actor == actor:
        raise InvariantViolation(
            f"{target} needs a second person (four-eyes)",
            next_action="Ask another authorised approver to complete this step.",
        )
    return target_step


# --------------------------------------------------------------------------- catalogue (§22)


def _s(
    state: str,
    sla: tuple[int, SlaUnit] | None = None,
    *,
    rule: str | None = None,
    four_eyes: bool = False,
    terminal: bool = False,
) -> StepDef:
    return StepDef(state, rule, Sla(*sla) if sla else None, four_eyes, terminal)


WD, H, D = SlaUnit.WORKING_DAYS, SlaUnit.HOURS, SlaUnit.DAYS

CATALOGUE: dict[str, WorkflowDefinition] = {
    d.key: d
    for d in [
        linear(
            "admission",
            1,
            _s("application"),
            _s("review", (10, WD), rule="role:REG@institution"),
            _s("assessment"),
            _s("decision"),
            _s("offer"),
            _s("acceptance"),
            _s("enrolment", terminal=True),
            extra={"decision": {"enrolment"}},
        ),
        linear(
            "safeguarding",
            1,
            _s("concern"),
            _s("triage", (1, D), rule="role:DSL@institution"),
            _s("risk_assessment"),
            _s("case"),
            _s("action"),
            _s("referral"),
            _s("review"),
            _s("closure", terminal=True),
            extra={"triage": {"closure"}, "case": {"review"}},
        ),
        linear(
            "certificate_lifecycle",
            1,
            _s("request"),
            _s("evidence", (15, WD)),
            _s("approval", rule="role:CERT@authority", four_eyes=True),
            _s("effect"),
            _s("holder_notified", terminal=True),
        ),
        linear(
            "policy_exception",
            1,
            _s("request"),
            _s("evidence"),
            _s("approval", rule="scope:parent"),
            _s("active"),
            _s("review"),
            _s("expired", terminal=True),
            extra={"review": {"active"}},
        ),
        linear(
            "data_subject_request",
            1,
            _s("received"),
            _s("identity_verified"),
            _s("search"),
            _s("review_redact"),
            _s("respond", terminal=True),  # statutory SLA configured per tenant regime (§8.4)
        ),
        linear(
            "records_disposal",
            1,
            _s("due"),
            _s("hold_check"),
            _s("owner_review"),
            _s("authority_approval", rule="role:REC@institution", four_eyes=True),
            _s("disposed"),
            _s("disposal_record", terminal=True),
        ),
    ]
}
