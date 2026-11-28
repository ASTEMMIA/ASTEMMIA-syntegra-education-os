"""Pure academic rules (CEDA §5 academic structure, ADR-029).

* **Schools** run a session (e.g. 2026/2027) of three terms. Each level has years (Primary 1-6,
  JSS 1-3, SSS 1-3) and each year has arms (Primary 4A, JSS 1 Gold), each a class with a class
  teacher, a room and a capacity. Subjects come from the adopted national curriculum.
* **Tertiary institutions** run a session of two semesters. Students take courses (CSC 101, 3
  units) at a level (100L, ND 1, NCE 2); lecturers are assigned to each course offering.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

DATA = Path(__file__).with_name("data")


@dataclass(frozen=True, slots=True)
class Year:
    code: str  # P4, JSS1, 100L
    name: str  # Primary 4, JSS 1, 100 Level
    level: str  # institution level: primary, jss, degree …
    order: int  # progression order within the level
    typical_age: int | None  # age at the start of the session; None: no age band


def _years() -> list[Year]:
    out: list[Year] = []
    out += [
        Year("N1", "Nursery 1", "eccde", 1, 3),
        Year("N2", "Nursery 2", "eccde", 2, 4),
        Year("KG", "Kindergarten", "eccde", 3, 5),
    ]
    out += [Year(f"P{i}", f"Primary {i}", "primary", i, 5 + i) for i in range(1, 7)]
    out += [Year(f"JSS{i}", f"JSS {i}", "jss", i, 11 + i) for i in range(1, 4)]
    out += [Year(f"SSS{i}", f"SSS {i}", "sss", i, 14 + i) for i in range(1, 4)]
    out += [
        Year("BL", "Basic literacy", "adult", 1, None),
        Year("PL", "Post-literacy", "adult", 2, None),
        Year("CE", "Continuing education", "adult", 3, None),
    ]
    out += [Year(f"TV{i}", f"TVET year {i}", "tvet", i, None) for i in range(1, 4)]
    out += [Year(f"NCE{i}", f"NCE {i}", "nce", i, None) for i in range(1, 4)]
    out += [Year(f"ND{i}", f"ND {i}", "nd", i, None) for i in range(1, 3)]
    out += [Year(f"HND{i}", f"HND {i}", "hnd", i, None) for i in range(1, 3)]
    out += [Year(f"{i}00L", f"{i}00 Level", "degree", i, None) for i in range(1, 7)]
    out += [
        Year("PGD", "Postgraduate diploma", "postgraduate", 1, None),
        Year("MAS", "Master's", "postgraduate", 2, None),
        Year("PHD", "Doctorate", "postgraduate", 3, None),
    ]
    return out


YEARS: dict[str, Year] = {y.code: y for y in _years()}
SCHOOL_LEVELS = frozenset({"eccde", "primary", "jss", "sss", "adult"})
TERTIARY_LEVELS = frozenset({"tvet", "nce", "nd", "hnd", "degree", "postgraduate"})
TERMS = ("First term", "Second term", "Third term")
SEMESTERS = ("First semester", "Second semester")
SESSION_NAME = re.compile(r"^(\d{4})/(\d{4})$")
ARM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,19}$")
ROOM_KINDS = (
    "classroom",
    "laboratory",
    "workshop",
    "ict",
    "library",
    "hall",
    "lecture_theatre",
    "other",
)
OFFER_KINDS = ("core", "elective", "optional")
MAX_ARM_CAPACITY = 120  # a lecture theatre; a class is usually 30-50


def year(code: str) -> Year:
    y = YEARS.get(code.strip().upper())
    if y is None:
        raise ValueError(f"unknown class year {code!r}")
    return y


def next_year(code: str) -> Year | None:
    """The year a learner is promoted into, or None at the end of the level."""
    y = year(code)
    return next((x for x in YEARS.values() if x.level == y.level and x.order == y.order + 1), None)


def check_session_name(name: str) -> tuple[int, int]:
    m = SESSION_NAME.match(name.strip())
    if not m or int(m.group(2)) != int(m.group(1)) + 1:
        raise ValueError("a session is named like 2026/2027 (two consecutive years)")
    return int(m.group(1)), int(m.group(2))


def class_name(y: Year, arm: str) -> str:
    """Primary 4A, JSS 1 Gold: a single letter joins the year, a word follows a space."""
    arm = arm.strip()
    return f"{y.name}{arm}" if len(arm) == 1 else f"{y.name} {arm}"


def age_on(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def age_problem(
    born: date | None, y: Year, session_start: date, *, below: int = 1, above: int = 3
) -> str | None:
    """Record-level check (CEDA §16.2): a learner far outside the usual age for the class."""
    if born is None or y.typical_age is None:
        return None
    age = age_on(born, session_start)
    if age < y.typical_age - below or age > y.typical_age + above:
        return (
            f"the learner is {age} at the start of the session; {y.name} is usually "
            f"{y.typical_age - below}-{y.typical_age + above}"
        )
    return None


# --------------------------------------------------------------------------- curriculum


@dataclass(frozen=True, slots=True)
class SubjectDef:
    code: str
    name: str
    group: str  # e.g. "Languages", "Sciences", "Trade/entrepreneurship"


@dataclass(frozen=True, slots=True)
class EntryDef:
    year_code: str
    subject_code: str
    kind: str  # core | elective | optional
    periods_per_week: int | None


@dataclass(frozen=True, slots=True)
class CurriculumDef:
    code: str
    name: str
    authority: str
    note: str
    subjects: tuple[SubjectDef, ...]
    entries: tuple[EntryDef, ...]


@cache
def national_curriculum(name: str = "ng-basic-and-senior") -> CurriculumDef:
    doc = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    subjects = tuple(SubjectDef(s["code"], s["name"], s["group"]) for s in doc["subjects"])
    known = {s.code for s in subjects}
    entries: list[EntryDef] = []
    for block in doc["timetable"]:
        for y in block["years"]:
            year(y)  # validates
            for code, kind, periods in block["subjects"]:
                if code not in known:
                    raise ValueError(f"{name}: subject {code} is not defined")
                if kind not in OFFER_KINDS:
                    raise ValueError(f"{name}: bad kind {kind}")
                entries.append(EntryDef(y, code, kind, periods))
    pairs = [(e.year_code, e.subject_code) for e in entries]
    if len(set(pairs)) != len(pairs):
        raise ValueError(f"{name}: a subject repeats for a year")
    return CurriculumDef(
        doc["code"], doc["name"], doc["authority"], doc["note"], subjects, tuple(entries)
    )
