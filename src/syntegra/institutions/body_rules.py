"""Education bodies outside the state's own structure (ADR-019). Framework-free.

Examination boards (WAEC, NECO, NABTEB…), admissions (JAMB), regulators (NUC, NBTE, NCCE…),
funders (UBEC, TETFund) and others come from a **catalogue file** per country, as data, like the
jurisdiction structure files (ADR-011). An institution is linked to a body by what the body does:

| Body role                  | Link an institution may have                     |
|----------------------------|--------------------------------------------------|
| examination                | exam_centre (a centre number for one examination) |
| admissions                 | admissions_code (e.g. its JAMB institution code)  |
| regulator                  | accreditation, approval                           |
| funding                    | beneficiary                                       |
| curriculum, professional_… | none: they relate to curricula and staff          |

A link must also fit the body: the institution's type or one of its levels must be among those
the body covers, and an exam centre's examination must be one the body runs for a level the
institution offers.
"""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from syntegra.institutions.domain import InstitutionType, Level

CATALOGUE_DIR = Path(__file__).resolve().parent.parent / "structures" / "bodies"
CODE = r"^[A-Z0-9][A-Z0-9-]{1,23}$"


class Role(StrEnum):
    EXAMINATION = "examination"
    ADMISSIONS = "admissions"
    REGULATOR = "regulator"
    FUNDING = "funding"
    CURRICULUM = "curriculum"
    PROFESSIONAL_REGISTRATION = "professional_registration"


class LinkType(StrEnum):
    EXAM_CENTRE = "exam_centre"
    ADMISSIONS_CODE = "admissions_code"
    ACCREDITATION = "accreditation"
    APPROVAL = "approval"
    BENEFICIARY = "beneficiary"


LINKS_BY_ROLE: dict[Role, frozenset[LinkType]] = {
    Role.EXAMINATION: frozenset({LinkType.EXAM_CENTRE}),
    Role.ADMISSIONS: frozenset({LinkType.ADMISSIONS_CODE}),
    Role.REGULATOR: frozenset({LinkType.ACCREDITATION, LinkType.APPROVAL}),
    Role.FUNDING: frozenset({LinkType.BENEFICIARY}),
    Role.CURRICULUM: frozenset(),
    Role.PROFESSIONAL_REGISTRATION: frozenset(),
}
# Links that are meaningless without the body's own number for the institution.
NEEDS_REFERENCE = frozenset({LinkType.EXAM_CENTRE, LinkType.ADMISSIONS_CODE})
STATUSES = ("active", "provisional", "suspended", "withdrawn")

LINK_LABEL = {
    LinkType.EXAM_CENTRE: "exam centre",
    LinkType.ADMISSIONS_CODE: "admissions code",
    LinkType.ACCREDITATION: "accreditation",
    LinkType.APPROVAL: "approval",
    LinkType.BENEFICIARY: "beneficiary",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Examination(_Model):
    code: str = Field(pattern=CODE)
    name: str
    levels: list[Level] = Field(min_length=1)


class Covers(_Model):
    levels: list[Level] = []
    types: list[InstitutionType] = []


class BodySpec(_Model):
    code: str = Field(pattern=CODE)
    name: str = Field(min_length=3, max_length=200)
    scope: Literal["international", "regional", "national", "state"]
    countries: list[str] = []  # ISO 3166-1 alpha-2; empty means any country
    roles: list[Role] = Field(min_length=1)
    covers: Covers = Covers()
    examinations: list[Examination] = []
    note: str | None = None

    @field_validator("countries")
    @classmethod
    def _iso(cls, v: list[str]) -> list[str]:
        for c in v:
            if len(c) != 2 or not c.isalpha() or not c.isupper():
                raise ValueError(f"{c!r} is not an ISO 3166-1 alpha-2 country code")
        return v

    @model_validator(mode="after")
    def _exams(self) -> BodySpec:
        codes = [e.code for e in self.examinations]
        if len(codes) != len(set(codes)):
            raise ValueError(f"{self.code}: examination codes must be unique")
        if Role.EXAMINATION in self.roles and not self.examinations:
            raise ValueError(f"{self.code}: an examination body must list its examinations")
        return self

    @property
    def link_types(self) -> frozenset[LinkType]:
        out: frozenset[LinkType] = frozenset()
        for r in self.roles:
            out |= LINKS_BY_ROLE[r]
        return out


class Catalogue(_Model):
    version: int
    name: str
    description: str | None = None
    bodies: list[BodySpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> Catalogue:
        codes = [b.code for b in self.bodies]
        if len(codes) != len(set(codes)):
            raise ValueError("body codes must be unique within a catalogue")
        return self


def load_catalogue(name_or_path: str) -> Catalogue:
    """A bundled catalogue by name (``ng``, ``international``) or a path to a JSON file."""
    path = Path(name_or_path)
    if not path.suffix:
        path = CATALOGUE_DIR / f"{name_or_path}.json"
    return Catalogue.model_validate(json.loads(path.read_text(encoding="utf-8")))


def check_link(
    body: BodySpec,
    *,
    link_type: str,
    institution_type: str,
    institution_levels: set[str],
    exam_code: str | None,
    reference: str | None,
    status: str,
    valid_from: date | None,
    valid_to: date | None,
) -> list[str]:
    """Every reason this link cannot be recorded; empty when it can."""
    errors: list[str] = []
    try:
        kind = LinkType(link_type)
    except ValueError:
        return [f"'{link_type}' is not a kind of link ({', '.join(t.value for t in LinkType)})"]
    if kind not in body.link_types:
        allowed = ", ".join(sorted(LINK_LABEL[t] for t in body.link_types)) or "none"
        errors.append(
            f"{body.code} does not record {LINK_LABEL[kind]} links for institutions "
            f"(it records: {allowed})"
        )
        return errors
    covered_types = {t.value for t in body.covers.types}
    covered_levels = {lv.value for lv in body.covers.levels}
    if institution_type not in covered_types and not (institution_levels & covered_levels):
        errors.append(f"{body.code} does not cover this kind of institution")
    if kind in NEEDS_REFERENCE and not (reference or "").strip():
        errors.append(f"a {LINK_LABEL[kind]} needs {body.code}'s number for the institution")
    if kind is LinkType.EXAM_CENTRE:
        exams = {e.code: e for e in body.examinations}
        if not exam_code:
            errors.append(f"say which {body.code} examination ({', '.join(exams)})")
        elif exam_code not in exams:
            errors.append(f"{body.code} has no examination '{exam_code}' ({', '.join(exams)})")
        else:
            exam_levels = {lv.value for lv in exams[exam_code].levels}
            if not (institution_levels & exam_levels):
                errors.append(
                    f"{exam_code} is for {', '.join(sorted(exam_levels)).upper()}, "
                    "which this institution does not offer"
                )
    elif exam_code:
        errors.append("only an exam centre names an examination")
    if status not in STATUSES:
        errors.append(f"status must be one of {', '.join(STATUSES)}")
    if valid_from and valid_to and valid_to < valid_from:
        errors.append("valid_to is before valid_from")
    return errors
