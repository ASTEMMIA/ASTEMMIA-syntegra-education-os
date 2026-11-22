"""Pure records rules: classification, file plans, file numbering, paper retention (ADR-027).

* **Digital records are permanent.** Once captured, a document and every version of it stays in
  the state's education repository for good; nothing here deletes one. The State Government and
  the State Archives oversee the whole repository.
* **Paper originals** follow the series' paper rule after the file closes: destroy, transfer to the
  State Archives, or retain. Destruction needs verified digitisation first.
* **Model file plans** for primary, secondary and tertiary institutions are data
  (``file_plans/*.json``); an institution adopts one and may add its own series.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

PLANS_DIR = Path(__file__).with_name("file_plans")


class Classification(StrEnum):
    """CEDA §8.1 tiers as they apply to records, lowest to highest."""

    PUBLIC = "public"  # prospectus, published policy, curricula
    INTERNAL = "internal"  # timetables, minutes, circulars
    CONFIDENTIAL = "confidential"  # learner records, results, staff files
    RESTRICTED = "restricted"  # medical, SEND, discipline, HR casework
    SPECIAL = "special"  # safeguarding


RANK = {c: i for i, c in enumerate(Classification)}

# The permission that reads each tier. Public needs only a role at the scope.
VIEW_PERMISSION: dict[Classification, str | None] = {
    Classification.PUBLIC: None,
    Classification.INTERNAL: "document:view_internal",
    Classification.CONFIDENTIAL: "document:view_confidential",
    Classification.RESTRICTED: "document:view_restricted",
    Classification.SPECIAL: "document:view_special",
}
# Restricted and Special records are opened only for a stated purpose, which the access log keeps.
NEEDS_PURPOSE = frozenset({Classification.RESTRICTED, Classification.SPECIAL})


class Numbering(StrEnum):
    SINGLE = "single"  # one running file: GOV/6, then volumes
    ANNUAL = "annual"  # a new file each year: FIN/1/2026/1
    SELF = "self"  # one file per person: STF/1/0042
    ALPHABETICAL = "alphabetical"  # by subject's initial: GOV/4/A/3


class PaperRule(StrEnum):
    DESTROY = "destroy"
    TRANSFER = "transfer_archives"
    RETAIN = "retain"


PLAN_NAMES = ("primary", "secondary", "tertiary")
DOC_TYPES = (
    "admission_form",
    "report_card",
    "result_sheet",
    "certificate",
    "transcript",
    "letter",
    "circular",
    "minutes",
    "policy",
    "report",
    "form",
    "receipt",
    "voucher",
    "contract",
    "plan",
    "photograph",
    "other",
)
SOURCES = ("scanned", "born_digital", "email", "upload")
MIME_TYPES = {
    "application/pdf": b"%PDF",
    "image/tiff": None,  # II*\0 or MM\0*
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG",
}
LINK_KINDS = ("learner", "staff", "person", "institution", "application")
SERIES_CODE = re.compile(r"^[A-Z]{2,5}(/[0-9A-Z]{1,4}){1,2}$")


def check_content(mime: str, head: bytes) -> str | None:
    """A problem with the file, or None. Capture stations send PDF/A at 300 dpi."""
    if mime not in MIME_TYPES:
        return f"file type must be one of {', '.join(MIME_TYPES)}"
    magic = MIME_TYPES[mime]
    if mime == "image/tiff":
        ok = head[:4] in (b"II*\x00", b"MM\x00*")
    else:
        ok = magic is not None and head.startswith(magic)
    return None if ok else f"the file is not a valid {mime}"


def can_view(granted: set[Classification], level: Classification) -> bool:
    return level is Classification.PUBLIC or level in granted


@dataclass(frozen=True, slots=True)
class SeriesDef:
    function_code: str
    function_name: str
    code: str
    title: str
    activity: str
    numbering: Numbering
    classification: Classification
    paper_retain_years: int | None
    paper_rule: PaperRule
    vital: bool


def check_series(d: SeriesDef) -> list[str]:
    out = []
    if not SERIES_CODE.match(d.code):
        out.append(f"{d.code}: a series code looks like ADM/1 (letters, slash, number)")
    if not d.title.strip():
        out.append(f"{d.code}: a title is required")
    if d.paper_rule is not PaperRule.RETAIN and d.paper_retain_years is None:
        out.append(f"{d.code}: say how many years the paper is kept after the file closes")
    if d.paper_retain_years is not None and not 0 <= d.paper_retain_years <= 100:
        out.append(f"{d.code}: paper retention must be 0-100 years")
    return out


def _series(fn: dict[str, Any], s: dict[str, Any]) -> SeriesDef:
    return SeriesDef(
        function_code=fn["code"],
        function_name=fn["name"],
        code=s["code"],
        title=s["title"],
        activity=s.get("activity", ""),
        numbering=Numbering(s["numbering"]),
        classification=Classification(s["classification"]),
        paper_retain_years=s["paper"]["retain_years"],
        paper_rule=PaperRule(s["paper"]["then"]),
        vital=bool(s.get("vital", False)),
    )


@cache
def model_plan(name: str) -> tuple[str, tuple[SeriesDef, ...]]:
    """(title, series) of a model file plan. Raises ValueError if unknown or malformed."""
    if name not in PLAN_NAMES:
        raise ValueError(f"model file plan must be one of {', '.join(PLAN_NAMES)}")
    doc = json.loads((PLANS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    series = tuple(_series(fn, s) for fn in doc["functions"] for s in fn["series"])
    problems = [p for d in series for p in check_series(d)]
    codes = [d.code for d in series]
    if len(set(codes)) != len(codes):
        problems.append("series codes repeat")
    if problems:
        raise ValueError("; ".join(problems))
    return doc["title"], series


def plan_for_levels(levels: set[str]) -> str:
    """The model plan that suits an institution's levels (it may choose another)."""
    if levels & {"tvet", "nce", "nd", "hnd", "degree", "postgraduate"}:
        return "tertiary"
    if levels & {"jss", "sss"}:
        return "secondary"
    return "primary"


def file_number(
    series_code: str,
    numbering: Numbering,
    *,
    seq: int,
    year: int | None = None,
    letter: str | None = None,
) -> str:
    """The reference written on the file cover. ``seq`` counts within the series (and the year
    for annual files, the letter for alphabetical ones)."""
    if numbering is Numbering.SINGLE:
        return series_code if seq == 1 else f"{series_code}/V{seq}"
    if numbering is Numbering.ANNUAL:
        if year is None:
            raise ValueError("annual files need a year")
        return f"{series_code}/{year}/{seq}"
    if numbering is Numbering.SELF:
        return f"{series_code}/{seq:04d}"
    if not letter or not letter.isalpha():
        raise ValueError("alphabetical files need the subject's initial")
    return f"{series_code}/{letter.upper()}/{seq}"


def initial(name: str) -> str:
    for ch in name.strip().upper():
        if "A" <= ch <= "Z":
            return ch
    raise ValueError("the subject name needs a letter")


def paper_due(closed_on: date, years: int | None) -> date | None:
    """When the paper original may be dispositioned, or None if it is kept for good."""
    if years is None:
        return None
    try:
        return closed_on.replace(year=closed_on.year + years)
    except ValueError:  # 29 February
        return closed_on.replace(year=closed_on.year + years, day=28)


def search_terms(*parts: str | None) -> str:
    """Normalised text the search runs over: lower case, single spaces, no punctuation."""
    text = " ".join(p for p in parts if p)
    return re.sub(r"\s+", " ", re.sub(r"[^\w/]+", " ", text.lower())).strip()
