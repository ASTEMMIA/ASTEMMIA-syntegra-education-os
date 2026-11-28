"""Pure MDM rules (CEDA §4, §6.2, §16.3). No framework or database imports.

Matching is explainable by design (P13): every score is the sum of named reasons a registry
officer can read, never an opaque number.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

import jellyfish
import phonenumbers
from email_validator import EmailNotValidError, validate_email

from syntegra.core.errors import ValidationFailed

# --------------------------------------------------------------------------- reference sets


class Sex(StrEnum):
    FEMALE = "F"
    MALE = "M"
    INTERSEX = "X"
    NOT_RECORDED = "U"


class IdentifierScheme(StrEnum):
    NIN = "NIN"  # Nigeria National Identification Number (NIMC)
    UPN = "UPN"  # UK Unique Pupil Number
    ULN = "ULN"  # UK Unique Learner Number
    WAEC = "WAEC"  # WAEC candidate number
    JAMB = "JAMB"  # JAMB registration number
    PASSPORT = "PASSPORT"
    BVN = "BVN"  # staff payroll


# Schemes whose value identifies one human in its issuing system: a clash is strong evidence.
STRONG_SCHEMES = frozenset(
    {
        IdentifierScheme.NIN,
        IdentifierScheme.UPN,
        IdentifierScheme.ULN,
        IdentifierScheme.PASSPORT,
        IdentifierScheme.BVN,
    }
)

_SCHEME_PATTERN: dict[IdentifierScheme, re.Pattern[str]] = {
    IdentifierScheme.NIN: re.compile(r"^\d{11}$"),
    IdentifierScheme.BVN: re.compile(r"^\d{11}$"),
    IdentifierScheme.UPN: re.compile(r"^[A-Z]\d{11}[A-Z0-9]$"),
    IdentifierScheme.ULN: re.compile(r"^\d{10}$"),
    IdentifierScheme.WAEC: re.compile(r"^\d{10}[A-Z0-9]{0,4}$"),
    IdentifierScheme.JAMB: re.compile(r"^\d{8,10}[A-Z]{2}$"),
    IdentifierScheme.PASSPORT: re.compile(r"^[A-Z0-9]{6,12}$"),
}


class ContactKind(StrEnum):
    PHONE = "phone"
    EMAIL = "email"


class NameScript(StrEnum):
    LATIN = "Latn"  # ISO 15924
    ARABIC = "Arab"  # incl. Ajami (Hausa in Arabic script)


# --------------------------------------------------------------------------- normalisation

_NON_LETTER = re.compile(r"[^a-z\s]")
_SPACES = re.compile(r"\s+")


def normalize_name(value: str | None) -> str:
    """Fold case and strip diacritics so 'Adébáyọ̀' and 'ADEBAYO' compare equal.

    Hyphens and apostrophes become spaces; anything that is not a Latin letter is dropped.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.replace("-", " ").replace("'", " ").replace("\u2019", " ")
    return _SPACES.sub(" ", _NON_LETTER.sub("", stripped)).strip()


def name_tokens(value: str | None) -> list[str]:
    return [t for t in normalize_name(value).split(" ") if t]


def phonetic_keys(value: str | None) -> set[str]:
    """Two phonetic codes per token (Metaphone + NYSIIS) for recall across spellings (ADR-008)."""
    keys: set[str] = set()
    for tok in name_tokens(value):
        if len(tok) < 2:
            continue
        keys.add("M:" + jellyfish.metaphone(tok))
        keys.add("N:" + jellyfish.nysiis(tok))
    return keys


def blocking_key(value: str | None) -> str | None:
    """Indexed key for candidate retrieval: Metaphone of the first token."""
    toks = name_tokens(value)
    return jellyfish.metaphone(toks[0])[:16] if toks else None


def normalize_identifier(scheme: IdentifierScheme, value: str) -> str:
    v = re.sub(r"[\s\-./]", "", value).upper()
    pattern = _SCHEME_PATTERN.get(scheme)
    if pattern is not None and not pattern.match(v):
        raise ValidationFailed(
            f"{scheme.value} number is not in the expected format",
            next_action="Check the number against the original document.",
        )
    return v


def normalize_contact(kind: ContactKind, value: str, default_region: str = "NG") -> str:
    if kind is ContactKind.PHONE:
        try:
            parsed = phonenumbers.parse(value, default_region)
        except phonenumbers.NumberParseException as exc:
            raise ValidationFailed(
                "phone number could not be read",
                next_action="Use the full number, e.g. +234 803 123 4567.",
            ) from exc
        if not phonenumbers.is_valid_number(parsed):
            raise ValidationFailed(
                "phone number is not valid", next_action="Check the number and country code."
            )
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValidationFailed(
            "email address is not valid", next_action="Check the spelling of the address."
        ) from exc


# --------------------------------------------------------------------------- matching


@dataclass(frozen=True, slots=True)
class PersonFacts:
    """What the matcher compares. Identifier and contact values arrive already hashed/normalised."""

    given_name: str
    family_name: str
    other_names: str | None = None
    date_of_birth: date | None = None
    sex: Sex | None = None
    identifiers: Mapping[str, bytes] = field(default_factory=dict)  # scheme -> blind index
    contacts: frozenset[str] = frozenset()  # normalised phone/email values


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    points: int
    detail: str


@dataclass(frozen=True, slots=True)
class MatchScore:
    score: int
    reasons: tuple[Reason, ...]

    def explain(self) -> list[dict[str, Any]]:
        return [{"code": r.code, "points": r.points, "detail": r.detail} for r in self.reasons]


class MatchOutcome(StrEnum):
    NO_MATCH = "no_match"
    REVIEW = "review"  # create, and open a match_review task for a registry officer
    BLOCK = "block"  # do not create: offer the existing record (§4)


@dataclass(frozen=True, slots=True)
class MatchThresholds:
    auto_link: int = 85
    review: int = 60

    def __post_init__(self) -> None:
        if not 0 < self.review < self.auto_link <= 100:
            raise ValidationFailed("match thresholds must satisfy 0 < review < auto_link <= 100")

    def outcome(self, score: int) -> MatchOutcome:
        if score >= self.auto_link:
            return MatchOutcome.BLOCK
        if score >= self.review:
            return MatchOutcome.REVIEW
        return MatchOutcome.NO_MATCH


JW_STRONG = 0.92


def _name_points(
    a: str, b: str, exact: int, phonetic: int, similar: int, label: str
) -> Reason | None:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return None
    if na == nb:
        return Reason(f"{label}_exact", exact, f"{label.replace('_', ' ')} is the same")
    if phonetic_keys(na) & phonetic_keys(nb) and na[0] == nb[0]:
        return Reason(f"{label}_phonetic", phonetic, f"{label.replace('_', ' ')} sounds the same")
    if jellyfish.jaro_winkler_similarity(na, nb) >= JW_STRONG:
        return Reason(
            f"{label}_similar", similar, f"{label.replace('_', ' ')} is spelled very similarly"
        )
    return None


def score_match(a: PersonFacts, b: PersonFacts) -> MatchScore:
    reasons: list[Reason] = []

    # Identifiers: the strongest evidence either way.
    for scheme, digest in a.identifiers.items():
        other = b.identifiers.get(scheme)
        if other is None:
            continue
        if other == digest:
            reasons.append(Reason("identifier_match", 55, f"same {scheme} number"))
        elif scheme in STRONG_SCHEMES:
            reasons.append(Reason("identifier_conflict", -45, f"different {scheme} numbers"))

    # Names, allowing given/family to have been swapped (common on paper records).
    family = _name_points(a.family_name, b.family_name, 20, 16, 14, "family_name")
    given = _name_points(a.given_name, b.given_name, 18, 14, 12, "given_name")
    swapped_f = _name_points(a.family_name, b.given_name, 20, 16, 14, "family_name")
    swapped_g = _name_points(a.given_name, b.family_name, 18, 14, 12, "given_name")
    straight = (family.points if family else 0) + (given.points if given else 0)
    swapped = int(
        ((swapped_f.points if swapped_f else 0) + (swapped_g.points if swapped_g else 0)) * 0.9
    )
    if swapped > straight:
        reasons.append(Reason("names_swapped", swapped, "given and family names appear swapped"))
    else:
        reasons.extend(r for r in (family, given) if r)

    if (
        a.other_names
        and b.other_names
        and set(name_tokens(a.other_names)) & set(name_tokens(b.other_names))
    ):
        reasons.append(Reason("other_names_overlap", 3, "a middle name is shared"))

    # Date of birth.
    da, db = a.date_of_birth, b.date_of_birth
    if da and db:
        if da == db:
            reasons.append(Reason("dob_exact", 30, "same date of birth"))
        elif da.year == db.year and da.month == db.day and da.day == db.month:
            reasons.append(
                Reason("dob_day_month_swapped", 22, "date of birth with day and month swapped")
            )
        elif (da.month, da.day) == (db.month, db.day) and abs(da.year - db.year) == 1:
            reasons.append(Reason("dob_year_off_by_one", 10, "date of birth one year apart"))
        else:
            reasons.append(Reason("dob_different", -30, "different date of birth"))

    known = {Sex.FEMALE, Sex.MALE}
    if a.sex in known and b.sex in known and a.sex != b.sex:
        reasons.append(Reason("sex_different", -15, "different sex at registration"))

    if a.contacts & b.contacts:
        reasons.append(Reason("contact_match", 15, "same phone number or email address"))

    total = max(0, min(100, sum(r.points for r in reasons)))
    return MatchScore(total, tuple(sorted(reasons, key=lambda r: -abs(r.points))))


# --------------------------------------------------------------------------- data quality


@dataclass(frozen=True, slots=True)
class QualityIssue:
    dimension: str
    code: str
    message: str  # what to do next (P10)


@dataclass(frozen=True, slots=True)
class QualityReport:
    score: int
    completeness: int
    validity: int
    uniqueness: int
    timeliness: int
    issues: tuple[QualityIssue, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "completeness": self.completeness,
            "validity": self.validity,
            "uniqueness": self.uniqueness,
            "timeliness": self.timeliness,
            "issues": [
                {"dimension": i.dimension, "code": i.code, "message": i.message}
                for i in self.issues
            ],
        }


@dataclass(frozen=True, slots=True)
class QualityInput:
    given_name: str | None
    family_name: str | None
    date_of_birth: date | None
    sex: str | None
    nationality: str | None
    primary_language: str | None
    name_script: str | None
    contact_count: int
    identifier_count: int
    verified_identifier_count: int
    open_match_reviews: int
    last_confirmed_at: datetime | None


MAX_AGE_YEARS = 120
STALE_DAYS = 365
_LANG = re.compile(r"^[a-z]{2,3}$")  # ISO 639-1/3


def assess_quality(q: QualityInput, *, today: date, now: datetime) -> QualityReport:
    """Score completeness (50), validity (30), uniqueness (10) and timeliness (10) (§16.3)."""
    issues: list[QualityIssue] = []

    required = [
        ("given_name", q.given_name, "Add the given name."),
        ("family_name", q.family_name, "Add the family name."),
        (
            "date_of_birth",
            q.date_of_birth,
            "Add the date of birth from a birth certificate or NIN slip.",
        ),
        (
            "sex",
            q.sex if q.sex and q.sex != Sex.NOT_RECORDED else None,
            "Record sex at registration.",
        ),
        ("nationality", q.nationality, "Record nationality."),
        ("primary_language", q.primary_language, "Record the home language."),
        ("contact", q.contact_count or None, "Add a phone number or email address."),
        ("identifier", q.identifier_count or None, "Add a national identifier such as the NIN."),
    ]
    missing = [(f, m) for f, v, m in required if not v]
    for f, m in missing:
        issues.append(QualityIssue("completeness", f"missing_{f}", m))
    completeness = round(50 * (len(required) - len(missing)) / len(required))

    validity = 30
    if q.date_of_birth is not None:
        if q.date_of_birth > today:
            validity -= 15
            issues.append(
                QualityIssue(
                    "validity", "dob_in_future", "Correct the date of birth: it is in the future."
                )
            )
        elif (today.year - q.date_of_birth.year) > MAX_AGE_YEARS:
            validity -= 15
            issues.append(
                QualityIssue(
                    "validity", "dob_implausible", "Check the date of birth: the age is over 120."
                )
            )
    if q.nationality and q.nationality not in phonenumbers.SUPPORTED_REGIONS:
        validity -= 5
        issues.append(
            QualityIssue(
                "validity", "nationality_unknown", "Use a two-letter ISO country code, e.g. NG."
            )
        )
    if q.primary_language and not _LANG.match(q.primary_language):
        validity -= 5
        issues.append(
            QualityIssue(
                "validity", "language_code", "Use an ISO 639 language code, e.g. yo, ig, ha, en."
            )
        )
    if (
        q.name_script == NameScript.LATIN
        and q.given_name
        and q.family_name
        and not (normalize_name(q.given_name) and normalize_name(q.family_name))
    ):
        validity -= 5
        issues.append(
            QualityIssue(
                "validity", "name_script", "The name is not in Latin script: set the name script."
            )
        )
    validity = max(0, validity)

    uniqueness = 10 if q.open_match_reviews == 0 else 0
    if q.open_match_reviews:
        issues.append(
            QualityIssue(
                "uniqueness",
                "possible_duplicate",
                "Resolve the open duplicate review for this person.",
            )
        )

    timeliness = 10
    if q.identifier_count and not q.verified_identifier_count:
        timeliness -= 5
        issues.append(
            QualityIssue(
                "timeliness",
                "identifier_unverified",
                "Verify the identifier with the issuing authority.",
            )
        )
    if q.last_confirmed_at is None or (now - q.last_confirmed_at).days > STALE_DAYS:
        timeliness -= 5
        issues.append(
            QualityIssue(
                "timeliness",
                "not_confirmed_recently",
                "Confirm these details with the person or guardian.",
            )
        )

    score = completeness + validity + uniqueness + timeliness
    return QualityReport(score, completeness, validity, uniqueness, timeliness, tuple(issues))


# --------------------------------------------------------------------------- survivorship

SURVIVING_FIELDS: tuple[str, ...] = (
    "given_name",
    "family_name",
    "other_names",
    "preferred_name",
    "name_script",
    "date_of_birth",
    "sex_at_registration",
    "nationality",
    "primary_language",
)

DEFAULT_SOURCE_TRUST: dict[str, int] = {
    "nimc": 100,  # verified against the national identity database
    "waec": 90,
    "neco": 90,
    "jamb": 90,
    "registry": 80,  # entered by a registry officer from documents
    "manual": 60,  # entered without a stated source
    "import": 50,  # migrated from a legacy MIS
    "self": 40,  # self-declared by a parent or learner
}


@dataclass(frozen=True, slots=True)
class SourcedRecord:
    values: Mapping[str, Any]
    source: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FieldDecision:
    field: str
    value: Any
    from_record: str  # "survivor" | "merged"
    reason: str


def survive(
    survivor: SourcedRecord,
    merged: SourcedRecord,
    trust: Mapping[str, int] | None = None,
) -> list[FieldDecision]:
    """Per field: a known value beats an empty one, then higher source trust, then recency (§4)."""
    ranks = {**DEFAULT_SOURCE_TRUST, **(trust or {})}

    def rank(r: SourcedRecord) -> int:
        return ranks.get((r.source or "manual").lower(), ranks["manual"])

    out: list[FieldDecision] = []
    for f in SURVIVING_FIELDS:
        sv, mv = survivor.values.get(f), merged.values.get(f)
        if mv in (None, "") or sv == mv:
            out.append(FieldDecision(f, sv, "survivor", "kept"))
        elif sv in (None, ""):
            out.append(FieldDecision(f, mv, "merged", "filled a missing value"))
        elif rank(merged) > rank(survivor):
            out.append(
                FieldDecision(f, mv, "merged", f"more trusted source ({merged.source or 'manual'})")
            )
        elif rank(merged) == rank(survivor) and merged.updated_at > survivor.updated_at:
            out.append(FieldDecision(f, mv, "merged", "same trust, more recent"))
        else:
            out.append(FieldDecision(f, sv, "survivor", "more trusted or more recent"))
    return out


def ensure_mergeable(
    survivor_id: Any, merged_id: Any, survivor_status: str, merged_status: str
) -> None:
    if survivor_id == merged_id:
        raise ValidationFailed("a person cannot be merged with themself")
    if "merged" in (survivor_status, merged_status):
        raise ValidationFailed(
            "one of these records has already been merged",
            next_action="Merge into the surviving record instead.",
        )


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if not (v in seen or seen.add(v))]  # type: ignore[func-returns-value]


def as_facts(
    *,
    given: str,
    family: str,
    other: str | None,
    dob: date | None,
    sex: str | None,
    identifiers: Mapping[str, bytes],
    contacts: Sequence[str],
) -> PersonFacts:
    return PersonFacts(
        given, family, other, dob, Sex(sex) if sex else None, dict(identifiers), frozenset(contacts)
    )
