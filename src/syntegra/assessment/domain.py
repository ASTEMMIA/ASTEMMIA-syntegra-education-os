"""Assessment rules (ADR-032): what a result is made of, how it is graded, how it is ranked.

Framework-free. The defaults follow common Nigerian practice; the State (or a board, or a school)
can set its own for a group of levels, and every result sheet keeps the rules it was marked under.

* basic (ECCDE, primary, JSS, TVET, adult): 1st test 20, 2nd test 20, exam 60; A-F, pass 40.
* senior (SSS): the same parts; WAEC grades A1-F9, credit 50.
* nbte (ND, HND): CA 30, exam 70; NBTE grades AA-F, 4-point GPA.
* nuc (NCE, degree, postgraduate): CA 30, exam 70; grades A-F, 5-point GPA.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

GROUPS = ("basic", "senior", "nbte", "nuc")
LEVEL_GROUP = {
    "eccde": "basic",
    "primary": "basic",
    "jss": "basic",
    "tvet": "basic",
    "adult": "basic",
    "sss": "senior",
    "nd": "nbte",
    "hnd": "nbte",
    "nce": "nuc",
    "degree": "nuc",
    "postgraduate": "nuc",
}
TERTIARY_GROUPS = frozenset({"nbte", "nuc"})


@dataclass(frozen=True, slots=True)
class Component:
    code: str  # CA1, CA2, EXAM
    name: str
    max_score: int


@dataclass(frozen=True, slots=True)
class Band:
    min_score: int  # the band runs from here up to the next band's minimum
    grade: str
    remark: str
    points: Decimal | None = None  # grade points, tertiary


@dataclass(frozen=True, slots=True)
class Policy:
    group: str
    name: str
    components: tuple[Component, ...]
    bands: tuple[Band, ...]  # highest first
    pass_mark: int
    classes: tuple[tuple[Decimal, str], ...] = ()  # (minimum CGPA, class of award), highest first

    @property
    def tertiary(self) -> bool:
        return self.group in TERTIARY_GROUPS

    def to_json(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "name": self.name,
            "components": [
                {"code": c.code, "name": c.name, "max_score": c.max_score} for c in self.components
            ],
            "bands": [
                {
                    "min_score": b.min_score,
                    "grade": b.grade,
                    "remark": b.remark,
                    "points": str(b.points) if b.points is not None else None,
                }
                for b in self.bands
            ],
            "pass_mark": self.pass_mark,
            "classes": [[str(g), label] for g, label in self.classes],
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> Policy:
        return Policy(
            d["group"],
            d["name"],
            tuple(Component(c["code"], c["name"], int(c["max_score"])) for c in d["components"]),
            tuple(
                Band(
                    int(b["min_score"]),
                    b["grade"],
                    b["remark"],
                    Decimal(b["points"]) if b.get("points") is not None else None,
                )
                for b in d["bands"]
            ),
            int(d["pass_mark"]),
            tuple((Decimal(g), label) for g, label in d.get("classes", [])),
        )

    def grade(self, total: Decimal) -> Band:
        for b in self.bands:
            if total >= b.min_score:
                return b
        return self.bands[-1]

    def award(self, cgpa: Decimal | None) -> str | None:
        if cgpa is None or not self.classes:
            return None
        for minimum, label in self.classes:
            if cgpa >= minimum:
                return label
        return "Fail"


def _bands(rows: Sequence[tuple[int, str, str] | tuple[int, str, str, str]]) -> tuple[Band, ...]:
    return tuple(Band(r[0], r[1], r[2], Decimal(r[3]) if len(r) > 3 else None) for r in rows)


TESTS_AND_EXAM = (
    Component("CA1", "First test", 20),
    Component("CA2", "Second test", 20),
    Component("EXAM", "Examination", 60),
)
CA_AND_EXAM = (Component("CA", "Continuous assessment", 30), Component("EXAM", "Examination", 70))

DEFAULTS: dict[str, Policy] = {
    "basic": Policy(
        "basic",
        "Basic education",
        TESTS_AND_EXAM,
        _bands(
            [
                (70, "A", "Excellent"),
                (60, "B", "Very good"),
                (50, "C", "Good"),
                (45, "D", "Fair"),
                (40, "E", "Pass"),
                (0, "F", "Fail"),
            ]
        ),
        40,
    ),
    "senior": Policy(
        "senior",
        "Senior secondary (WAEC grading)",
        TESTS_AND_EXAM,
        _bands(
            [
                (75, "A1", "Excellent"),
                (70, "B2", "Very good"),
                (65, "B3", "Good"),
                (60, "C4", "Credit"),
                (55, "C5", "Credit"),
                (50, "C6", "Credit"),
                (45, "D7", "Pass"),
                (40, "E8", "Pass"),
                (0, "F9", "Fail"),
            ]
        ),
        50,
    ),
    "nbte": Policy(
        "nbte",
        "Polytechnic, ND and HND (NBTE grading)",
        CA_AND_EXAM,
        _bands(
            [
                (75, "AA", "Distinction", "4.00"),
                (70, "AB", "Very good", "3.50"),
                (65, "B", "Good", "3.25"),
                (60, "BC", "Upper credit", "3.00"),
                (55, "C", "Credit", "2.75"),
                (50, "CD", "Lower credit", "2.50"),
                (45, "D", "Pass", "2.25"),
                (40, "E", "Pass", "2.00"),
                (0, "F", "Fail", "0.00"),
            ]
        ),
        40,
        (
            (Decimal("3.50"), "Distinction"),
            (Decimal("3.00"), "Upper credit"),
            (Decimal("2.50"), "Lower credit"),
            (Decimal("2.00"), "Pass"),
        ),
    ),
    "nuc": Policy(
        "nuc",
        "Degree and NCE (five-point grading)",
        CA_AND_EXAM,
        _bands(
            [
                (70, "A", "Excellent", "5.00"),
                (60, "B", "Very good", "4.00"),
                (50, "C", "Good", "3.00"),
                (45, "D", "Fair", "2.00"),
                (40, "E", "Pass", "1.00"),
                (0, "F", "Fail", "0.00"),
            ]
        ),
        40,
        (
            (Decimal("4.50"), "First class"),
            (Decimal("3.50"), "Second class upper"),
            (Decimal("2.40"), "Second class lower"),
            (Decimal("1.50"), "Third class"),
            (Decimal("1.00"), "Pass"),
        ),
    ),
}


def check_policy(p: Policy) -> list[str]:
    """What is wrong with a policy someone has set, if anything."""
    errors = []
    if p.group not in GROUPS:
        errors.append(f"the group is one of {', '.join(GROUPS)}")
    if not 1 <= len(p.components) <= 6:
        errors.append("give one to six parts (tests, assignments, examination)")
    codes = [c.code for c in p.components]
    if len(set(codes)) != len(codes):
        errors.append("each part needs its own code")
    if any(not c.code.isalnum() or len(c.code) > 8 for c in p.components):
        errors.append("part codes are letters and digits, up to 8")
    if any(c.max_score <= 0 for c in p.components):
        errors.append("every part is out of at least 1")
    if sum(c.max_score for c in p.components) != 100:
        errors.append("the parts add up to 100")
    mins = [b.min_score for b in p.bands]
    ordered = mins == sorted(mins, reverse=True) and len(set(mins)) == len(mins)
    if not p.bands or not ordered or mins[-1] != 0:
        errors.append("grade bands run from highest to lowest and the last starts at 0")
    if not 0 < p.pass_mark <= 100:
        errors.append("the pass mark is between 1 and 100")
    return errors


TWO = Decimal("0.01")


def round2(v: Decimal) -> Decimal:
    return v.quantize(TWO, rounding=ROUND_HALF_UP)


def competition_rank(values: dict[Any, Decimal]) -> dict[Any, int]:
    """1, 2, 2, 4: equal values share a position, and the next position is skipped."""
    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
    out: dict[Any, int] = {}
    prev: Decimal | None = None
    pos = 0
    for i, (k, v) in enumerate(ordered, start=1):
        if v != prev:
            pos, prev = i, v
        out[k] = pos
    return out


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def gpa(rows: Sequence[tuple[Decimal | None, int]]) -> Decimal | None:
    """Weighted by course units: sum(points x units) / sum(units)."""
    units = sum(u for p, u in rows if p is not None)
    if not units:
        return None
    return round2(sum((p * u for p, u in rows if p is not None), Decimal(0)) / units)
