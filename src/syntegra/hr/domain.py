"""Staff 360 rules (ADR-024). Framework-free."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


class Category(StrEnum):
    TEACHING = "teaching"
    NON_TEACHING = "non_teaching"


class StaffStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ON_LEAVE = "on_leave"
    EXITED = "exited"


class AppointmentType(StrEnum):
    FIRST_APPOINTMENT = "first_appointment"
    POSTING = "posting"  # deployed to a (new) place of work
    TRANSFER = "transfer"  # moved between places of work
    PROMOTION = "promotion"  # new grade level, usually same place
    ACTING = "acting"  # acting in a higher post
    SECONDMENT = "secondment"
    REDEPLOYMENT = "redeployment"
    CONTRACT = "contract"


class ExitReason(StrEnum):
    RESIGNATION = "resignation"
    RETIREMENT = "retirement"
    DEATH = "death"
    DISMISSAL = "dismissal"
    TERMINATION = "termination"
    END_OF_CONTRACT = "end_of_contract"
    TRANSFER_OUT = "transfer_out"  # to an employer outside the platform


class QualificationKind(StrEnum):
    PHD = "phd"
    MASTERS = "masters"
    PGDE = "pgde"  # Postgraduate Diploma in Education
    DEGREE = "degree"  # B.Ed, B.Sc(Ed), B.A., B.Sc…
    HND = "hnd"
    NCE = "nce"  # Nigeria Certificate in Education: the minimum teaching qualification
    ND = "nd"
    DIPLOMA = "diploma"
    CERTIFICATE = "certificate"  # e.g. SSCE, trade test
    OTHER = "other"


QUALIFICATION_LABEL = {
    QualificationKind.PHD: "PhD",
    QualificationKind.MASTERS: "Master's",
    QualificationKind.PGDE: "PGDE",
    QualificationKind.DEGREE: "Degree",
    QualificationKind.HND: "HND",
    QualificationKind.NCE: "NCE",
    QualificationKind.ND: "ND",
    QualificationKind.DIPLOMA: "Diploma",
    QualificationKind.CERTIFICATE: "Certificate",
    QualificationKind.OTHER: "Other",
}
# Highest first, for "highest qualification" on the nominal roll.
QUALIFICATION_RANK = list(QualificationKind)

GRADE_LEVELS = range(1, 18)  # Nigerian public service grade levels 01-17
STEPS = range(1, 16)
SALARY_STRUCTURES = ("CONPSS", "CONTISS", "CONAHESS", "CONUASS", "CONTEDISS", "PRIVATE", "OTHER")


def check_grade(grade_level: int | None, step: int | None) -> list[str]:
    errors = []
    if grade_level is not None and grade_level not in GRADE_LEVELS:
        errors.append("grade level must be 1 to 17")
    if step is not None and step not in STEPS:
        errors.append("step must be 1 to 15")
    if step is not None and grade_level is None:
        errors.append("a step needs a grade level")
    return errors


@dataclass(frozen=True, slots=True)
class RetirementRule:
    """Retire at ``age`` years of age or after ``service`` years of service, whichever is first."""

    age: int
    service: int


# Defaults, configurable per jurisdiction (setting hr.retirement). Teachers: the Harmonised
# Retirement Age for Teachers in Nigeria Act 2022 (65 / 40). Other public servants: 60 / 35.
DEFAULT_RETIREMENT = {
    Category.TEACHING: RetirementRule(65, 40),
    Category.NON_TEACHING: RetirementRule(60, 35),
}


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February in a non-leap year
        return d.replace(year=d.year + years, day=28)


def retirement_date(
    rule: RetirementRule, *, date_of_birth: date | None, first_appointment: date | None
) -> date | None:
    """The earlier of reaching retirement age and completing the years of service."""
    candidates = []
    if date_of_birth is not None:
        candidates.append(add_years(date_of_birth, rule.age))
    if first_appointment is not None:
        candidates.append(add_years(first_appointment, rule.service) - timedelta(days=1))
    return min(candidates) if candidates else None


def years_of_service(first_appointment: date | None, on: date) -> int | None:
    if first_appointment is None or first_appointment > on:
        return None
    years = on.year - first_appointment.year
    if (on.month, on.day) < (first_appointment.month, first_appointment.day):
        years -= 1
    return years


_QUAL_ALIASES = {
    "phd": QualificationKind.PHD,
    "doctorate": QualificationKind.PHD,
    "masters": QualificationKind.MASTERS,
    "msc": QualificationKind.MASTERS,
    "med": QualificationKind.MASTERS,
    "ma": QualificationKind.MASTERS,
    "mba": QualificationKind.MASTERS,
    "pgde": QualificationKind.PGDE,
    "pgdipe": QualificationKind.PGDE,
    "pgd": QualificationKind.PGDE,
    "degree": QualificationKind.DEGREE,
    "bed": QualificationKind.DEGREE,
    "bsced": QualificationKind.DEGREE,
    "baed": QualificationKind.DEGREE,
    "bsc": QualificationKind.DEGREE,
    "ba": QualificationKind.DEGREE,
    "btech": QualificationKind.DEGREE,
    "hnd": QualificationKind.HND,
    "nce": QualificationKind.NCE,
    "nd": QualificationKind.ND,
    "ond": QualificationKind.ND,
    "diploma": QualificationKind.DIPLOMA,
    "certificate": QualificationKind.CERTIFICATE,
    "ssce": QualificationKind.CERTIFICATE,
    "tc2": QualificationKind.CERTIFICATE,
    "gradeii": QualificationKind.CERTIFICATE,
    "other": QualificationKind.OTHER,
}
# Qualifications that train a teacher (the NCE is the minimum to teach in Nigeria).
TEACHING_KINDS = frozenset({QualificationKind.NCE, QualificationKind.PGDE})
_EDUCATION_DEGREE = re.compile(r"\b(b\.?\s?ed|b\.?sc\.?\s?\(?ed|b\.?a\.?\s?\(?ed|m\.?\s?ed)", re.I)


def parse_qualification(value: str) -> QualificationKind | None:
    key = re.sub(r"[^a-z]", "", value.casefold())
    if key in _QUAL_ALIASES:
        return _QUAL_ALIASES[key]
    for alias, kind in _QUAL_ALIASES.items():
        if len(alias) >= 3 and key.startswith(alias):
            return kind
    return None


def is_teaching_qualification(kind: QualificationKind, title: str | None) -> bool:
    """NCE and PGDE always; degrees in education (B.Ed, B.Sc(Ed), M.Ed…) too."""
    if kind in TEACHING_KINDS:
        return True
    return bool(title and _EDUCATION_DEGREE.search(title))
