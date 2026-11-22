"""Pure rules for the institution register and its bulk import. No framework or database imports.

One register covers kindergarten to university (ADR-010, ADR-012):

* **parent:** where an institution sits under its overseeing body. A school sits under one of the
  body's units (an LGEA). A tertiary institution sits directly under its body (the Ministry).
* **location:** the local government area it is in, always recorded and checked against the
  jurisdiction's areas. It is separate from the parent.
* **type and levels:** what kind of institution it is and what it offers.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import phonenumbers


class Level(StrEnum):
    """Levels offered (CEDA §2 sector table, Nigeria configuration; ISCED in brackets)."""

    ECCDE = "eccde"  # early childhood / kindergarten (0)
    PRIMARY = "primary"  # Primary 1-6 (1)
    JSS = "jss"  # junior secondary (2)
    SSS = "sss"  # senior secondary (3)
    TVET = "tvet"  # technical and vocational: NTC/NBC, NBTE programmes (3-5)
    NCE = "nce"  # Nigeria Certificate in Education, colleges of education (5)
    ND = "nd"  # National Diploma, polytechnics (5)
    HND = "hnd"  # Higher National Diploma, polytechnics (6)
    DEGREE = "degree"  # first degree (6)
    POSTGRADUATE = "postgraduate"  # PGD, master's, doctorate (7-8)
    ADULT = "adult"  # adult and non-formal: basic and post-literacy, continuing education (ADR-020)


LEVEL_LABEL = {
    Level.ECCDE: "ECCDE",
    Level.PRIMARY: "Primary",
    Level.JSS: "JSS",
    Level.SSS: "SSS",
    Level.TVET: "TVET",
    Level.NCE: "NCE",
    Level.ND: "ND",
    Level.HND: "HND",
    Level.DEGREE: "Degree",
    Level.POSTGRADUATE: "Postgraduate",
    Level.ADULT: "Adult and non-formal",
}
SCHOOL_LEVELS = frozenset({Level.ECCDE, Level.PRIMARY, Level.JSS, Level.SSS})
ADULT_LEVELS = frozenset({Level.ADULT})
TERTIARY_LEVELS = frozenset(set(Level) - SCHOOL_LEVELS - ADULT_LEVELS)
LEVEL_HINT = "Use Primary, JSS, SSS, ECCDE, TVET, NCE, ND, HND, Degree, Postgraduate or Adult."

# Spellings seen in real lists. "secondary" means JSS + SSS; "nd/hnd" splits into both.
_LEVEL_ALIASES: dict[str, tuple[Level, ...]] = {
    "eccde": (Level.ECCDE,),
    "ece": (Level.ECCDE,),
    "nursery": (Level.ECCDE,),
    "preprimary": (Level.ECCDE,),
    "kindergarten": (Level.ECCDE,),
    "kg": (Level.ECCDE,),
    "primary": (Level.PRIMARY,),
    "pry": (Level.PRIMARY,),
    "prim": (Level.PRIMARY,),
    "lp": (Level.PRIMARY,),
    "jss": (Level.JSS,),
    "js": (Level.JSS,),
    "juniorsecondary": (Level.JSS,),
    "junior": (Level.JSS,),
    "sss": (Level.SSS,),
    "ss": (Level.SSS,),
    "seniorsecondary": (Level.SSS,),
    "senior": (Level.SSS,),
    "secondary": (Level.JSS, Level.SSS),
    "sec": (Level.JSS, Level.SSS),
    "tvet": (Level.TVET,),
    "technical": (Level.TVET,),
    "vocational": (Level.TVET,),
    "ntc": (Level.TVET,),
    "nbc": (Level.TVET,),
    "ntcnbc": (Level.TVET,),
    "nce": (Level.NCE,),
    "nd": (Level.ND,),
    "ond": (Level.ND,),
    "nationaldiploma": (Level.ND,),
    "hnd": (Level.HND,),
    "highernationaldiploma": (Level.HND,),
    "degree": (Level.DEGREE,),
    "firstdegree": (Level.DEGREE,),
    "undergraduate": (Level.DEGREE,),
    "bachelors": (Level.DEGREE,),
    "bsc": (Level.DEGREE,),
    "ba": (Level.DEGREE,),
    "bed": (Level.DEGREE,),
    "beng": (Level.DEGREE,),
    "mbbs": (Level.DEGREE,),
    "llb": (Level.DEGREE,),
    "postgraduate": (Level.POSTGRADUATE,),
    "pg": (Level.POSTGRADUATE,),
    "pgd": (Level.POSTGRADUATE,),
    "masters": (Level.POSTGRADUATE,),
    "msc": (Level.POSTGRADUATE,),
    "ma": (Level.POSTGRADUATE,),
    "mba": (Level.POSTGRADUATE,),
    "phd": (Level.POSTGRADUATE,),
    "doctorate": (Level.POSTGRADUATE,),
    "adult": (Level.ADULT,),
    "adultandnonformal": (Level.ADULT,),
    "adulteducation": (Level.ADULT,),
    "adultliteracy": (Level.ADULT,),
    "literacy": (Level.ADULT,),
    "basicliteracy": (Level.ADULT,),
    "postliteracy": (Level.ADULT,),
    "massliteracy": (Level.ADULT,),
    "nonformal": (Level.ADULT,),
    "nonformaleducation": (Level.ADULT,),
    "continuingeducation": (Level.ADULT,),
}


class InstitutionType(StrEnum):
    SCHOOL = "school"
    UNIVERSITY = "university"
    POLYTECHNIC = "polytechnic"
    COLLEGE_OF_EDUCATION = "college_of_education"
    TECHNICAL_COLLEGE = "technical_college"
    COLLEGE_OF_HEALTH = "college_of_health"  # health technology, nursing and midwifery
    OTHER_TERTIARY = "other_tertiary"
    ADULT_LEARNING_CENTRE = "adult_learning_centre"  # literacy and non-formal centres (ADR-020)


TYPE_LABEL = {
    InstitutionType.SCHOOL: "School",
    InstitutionType.UNIVERSITY: "University",
    InstitutionType.POLYTECHNIC: "Polytechnic",
    InstitutionType.COLLEGE_OF_EDUCATION: "College of education",
    InstitutionType.TECHNICAL_COLLEGE: "Technical college",
    InstitutionType.COLLEGE_OF_HEALTH: "College of health",
    InstitutionType.OTHER_TERTIARY: "Other tertiary",
    InstitutionType.ADULT_LEARNING_CENTRE: "Adult learning centre",
}
_TYPE_ALIASES: dict[str, InstitutionType] = {
    "school": InstitutionType.SCHOOL,
    "primaryschool": InstitutionType.SCHOOL,
    "secondaryschool": InstitutionType.SCHOOL,
    "university": InstitutionType.UNIVERSITY,
    "uni": InstitutionType.UNIVERSITY,
    "polytechnic": InstitutionType.POLYTECHNIC,
    "poly": InstitutionType.POLYTECHNIC,
    "monotechnic": InstitutionType.POLYTECHNIC,
    "collegeofeducation": InstitutionType.COLLEGE_OF_EDUCATION,
    "coe": InstitutionType.COLLEGE_OF_EDUCATION,
    "collegeofeducationtechnical": InstitutionType.COLLEGE_OF_EDUCATION,
    "technicalcollege": InstitutionType.TECHNICAL_COLLEGE,
    "govttechnicalcollege": InstitutionType.TECHNICAL_COLLEGE,
    "governmenttechnicalcollege": InstitutionType.TECHNICAL_COLLEGE,
    "collegeofhealth": InstitutionType.COLLEGE_OF_HEALTH,
    "collegeofhealthtechnology": InstitutionType.COLLEGE_OF_HEALTH,
    "collegeofhealthscienceandtechnology": InstitutionType.COLLEGE_OF_HEALTH,
    "schoolofnursing": InstitutionType.COLLEGE_OF_HEALTH,
    "schoolofmidwifery": InstitutionType.COLLEGE_OF_HEALTH,
    "collegeofnursing": InstitutionType.COLLEGE_OF_HEALTH,
    "other": InstitutionType.OTHER_TERTIARY,
    "othertertiary": InstitutionType.OTHER_TERTIARY,
    "adultlearningcentre": InstitutionType.ADULT_LEARNING_CENTRE,
    "adultlearningcenter": InstitutionType.ADULT_LEARNING_CENTRE,
    "adulteducationcentre": InstitutionType.ADULT_LEARNING_CENTRE,
    "adulteducationcenter": InstitutionType.ADULT_LEARNING_CENTRE,
    "literacycentre": InstitutionType.ADULT_LEARNING_CENTRE,
    "literacycenter": InstitutionType.ADULT_LEARNING_CENTRE,
    "adultliteracycentre": InstitutionType.ADULT_LEARNING_CENTRE,
    "massliteracycentre": InstitutionType.ADULT_LEARNING_CENTRE,
    "nonformaleducationcentre": InstitutionType.ADULT_LEARNING_CENTRE,
    "continuingeducationcentre": InstitutionType.ADULT_LEARNING_CENTRE,
}
TYPE_HINT = (
    "Use School, University, Polytechnic, College of education, Technical college, "
    "College of health, Other tertiary or Adult learning centre."
)

TEMPLATE_COLUMNS = (
    "name",
    "type",
    "lga",
    "levels",
    "government_code",
    "address_line1",
    "address_line2",
    "town",
    "postcode",
    "phone",
    "email",
    "latitude",
    "longitude",
    "ownership",  # ADR-021: blank means government-owned
    "proprietor",
)
REQUIRED = ("name", "lga", "levels")
# Header spellings found in real spreadsheets, mapped to template columns.
COLUMN_ALIASES = {
    "address": "address_line1",
    "street": "address_line1",
    "street address": "address_line1",
    "address 1": "address_line1",
    "address line 1": "address_line1",
    "address 2": "address_line2",
    "address line 2": "address_line2",
    "city": "town",
    "town/city": "town",
    "post code": "postcode",
    "postal code": "postcode",
    "postal_code": "postcode",
    "zip": "postcode",
    "zip code": "postcode",
    "lga name": "lga",
    "local government area": "lga",
    "owner": "ownership",
    "ownership type": "ownership",
    "proprietor name": "proprietor",
    "proprietor/owner": "proprietor",
    "owner name": "proprietor",
}

# Ownership (ADR-021). Government-owned covers state and federal; "private" is anything else
# a proprietor owns. Words found in real lists are understood.
_OWNERSHIP_ALIASES = {
    "public": "public",
    "government": "public",
    "govt": "public",
    "gov": "public",
    "state": "public",
    "stategovernment": "public",
    "stateowned": "public",
    "federal": "public",
    "federalgovernment": "public",
    "federalowned": "public",
    "governmentowned": "public",
    "private": "private",
    "privatelyowned": "private",
    "privateowned": "private",
    "proprietary": "private",
}
OWNERSHIP_LABEL = {"public": "Government", "private": "Private"}


def parse_ownership(value: str | None) -> tuple[str | None, str | None]:
    """Blank means government-owned, so lists without the column keep their meaning."""
    key = re.sub(r"[^a-z]", "", (value or "").casefold())
    if not key:
        return "public", None
    found = _OWNERSHIP_ALIASES.get(key)
    if found is None:
        return None, f"ownership '{value}' is not recognised. Use Government or Private."
    return found, None


ADDRESS_FIELDS = ("address_line1", "address_line2", "town", "postcode")
ADDRESS_LABEL = {
    "address_line1": "address line 1",
    "address_line2": "address line 2",
    "town": "town",
    "postcode": "postcode",
}


@dataclass(frozen=True)
class AddressRules:
    """Country-specific address format and which parts a jurisdiction requires (P4).

    Set from the structure file; Nigeria: 6-digit NIPOST postcodes.
    """

    postcode_pattern: str | None = None
    postcode_label: str = "postcode"
    postcode_example: str | None = None
    required: frozenset[str] = frozenset()
    state: str | None = None  # e.g. "Abia State", added to the composed address
    country: str | None = None  # e.g. "Nigeria"


def normalize_postcode(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def compose_address(
    *,
    line1: str | None,
    line2: str | None,
    town: str | None,
    location: str | None,
    postcode: str | None,
    state: str | None,
    country: str | None,
) -> str:
    """One-line postal address: '12 Market Road, Ohafia, Ohafia LGA, Abia State 440001, Nigeria'."""
    parts = [p for p in (line1, line2, town) if p]
    if location and location != town:
        parts.append(
            f"{location} LGA" if not location.upper().endswith(("LGA", "LGEA")) else location
        )
    elif location and not parts:
        parts.append(location)
    tail = " ".join(p for p in (state, postcode) if p)
    if tail:
        parts.append(tail)
    if country:
        parts.append(country)
    return ", ".join(parts)


MAX_ROWS = 10_000


def fold(value: str | None) -> str:
    """Case-, accent- and punctuation-insensitive key.

    'Govt. Pry Sch, Ụmụahia' -> 'govt pry sch umuahia'.
    """
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", value.casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_levels(value: str) -> tuple[list[Level], str | None]:
    parts = [p for p in re.split(r"[;,/&+|]|\band\b", value.casefold()) if p.strip()]
    found: list[Level] = []
    for part in parts:
        key = re.sub(r"[^a-z]", "", part)
        levels = _LEVEL_ALIASES.get(key)
        if levels is None:
            return [], f"level '{part.strip()}' is not recognised. {LEVEL_HINT}"
        for lv in levels:
            if lv not in found:
                found.append(lv)
    if not found:
        return [], f"levels are missing. {LEVEL_HINT}"
    order = list(Level)
    return sorted(found, key=order.index), None


def parse_type(value: str | None, levels: list[Level]) -> tuple[InstitutionType | None, str | None]:
    """Type is optional for schools (default School) and required for tertiary institutions."""
    key = re.sub(r"[^a-z]", "", (value or "").casefold())
    if not key:
        if levels and set(levels) <= SCHOOL_LEVELS:
            return InstitutionType.SCHOOL, None
        if levels and set(levels) <= ADULT_LEVELS:
            return InstitutionType.ADULT_LEARNING_CENTRE, None
        return None, f"type is required for tertiary institutions. {TYPE_HINT}"
    kind = _TYPE_ALIASES.get(key)
    if kind is None:
        return None, f"type '{value}' is not recognised. {TYPE_HINT}"
    # Adult and non-formal learning is its own sector (CEDA §2, §23.8; ADR-020).
    if kind is InstitutionType.ADULT_LEARNING_CENTRE:
        if set(levels) != {Level.ADULT}:
            return None, "an adult learning centre offers the Adult level only"
        return kind, None
    if Level.ADULT in levels:
        return None, "the Adult level is for adult learning centres; set the type to one"
    if kind is InstitutionType.SCHOOL and set(levels) & TERTIARY_LEVELS:
        return None, "a school cannot offer tertiary levels; set the type, e.g. Polytechnic"
    if kind is not InstitutionType.SCHOOL and levels and not set(levels) & TERTIARY_LEVELS:
        return None, f"a {TYPE_LABEL[kind].lower()} must offer at least one tertiary level"
    return kind, None


def unit_key(value: str) -> tuple[str, str]:
    """Keys under which a typed unit or area name is looked up: as written, and without a
    trailing 'LGA'/'LGEA'/'Local Government (Area)' (with or without full stops)."""
    key = fold(value).replace("l g e a", "lgea").replace("l g a", "lga")
    bare = re.sub(r"\s*(lgea|lga|local government( education authority| area)?)$", "", key).strip()
    return key, bare


@dataclass
class CleanRow:
    name: str
    name_key: str
    parent_scope_id: str
    location: str
    institution_type: InstitutionType
    levels: list[Level]
    government_code: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    town: str | None = None
    postcode: str | None = None
    phone: str | None = None
    email: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    ownership: str = "public"
    proprietor_name: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.institution_type.value,
            "ownership": self.ownership,
            "proprietor": self.proprietor_name,
            "lga": self.location,
            "parent_scope_id": self.parent_scope_id,
            "levels": [lv.value for lv in self.levels],
            "government_code": self.government_code,
            "address_line1": self.address_line1,
            "address_line2": self.address_line2,
            "town": self.town,
            "postcode": self.postcode,
            "phone": self.phone,
            "email": self.email,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


@dataclass
class RowResult:
    row_no: int
    raw: dict[str, str]
    clean: CleanRow | None
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Placement:
    """Where rows can go for the body the caller works in.

    ``units`` maps unit_key -> (scope id, bare name). When it is empty, institutions sit
    directly under ``body_scope_id``. ``areas`` maps unit_key -> canonical area name; when it
    is empty, any location text is accepted.
    """

    body_scope_id: str
    units: Mapping[str, tuple[str, str]]
    areas: Mapping[str, str]


def _blank(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def _place(raw_lga: str, placement: Placement) -> tuple[tuple[str, str] | None, str | None]:
    """(parent scope id, location name) or an error."""
    key, bare = unit_key(raw_lga)
    if placement.units:
        unit = placement.units.get(key) or placement.units.get(bare)
        if unit is None:
            return None, f"LGA '{raw_lga}' is not one of this body's units"
        return unit, None
    if placement.areas:
        area = placement.areas.get(key) or placement.areas.get(bare)
        if area is None:
            return None, f"LGA '{raw_lga}' is not a local government area of this state"
        return (placement.body_scope_id, area), None
    return (placement.body_scope_id, raw_lga.strip()), None


def validate_row(
    row_no: int,
    raw: Mapping[str, str],
    placement: Placement,
    *,
    default_region: str = "NG",
    allowed: frozenset[Level] | None = None,
    address: AddressRules | None = None,
    self_registering: frozenset[Level] = frozenset(),
) -> RowResult:
    """``allowed`` restricts the levels the body registers (setting ``register.levels``);
    ``address`` sets the postcode format and required address parts (setting ``address.rules``);
    ``self_registering`` are the levels whose PRIVATE institutions must apply online to this
    body instead of being listed by it (setting ``private.registration``; ADR-014, ADR-021)."""
    rules = address or AddressRules()
    data = {k: (raw.get(k) or "").strip() for k in TEMPLATE_COLUMNS}
    errors: list[str] = []
    for col in REQUIRED:
        if not data[col]:
            errors.append(f"{col} is required")
    name = re.sub(r"\s+", " ", data["name"])
    if name and not 3 <= len(name) <= 200:
        errors.append("name must be 3 to 200 characters")
    placed = None
    if data["lga"]:
        placed, err = _place(data["lga"], placement)
        if err:
            errors.append(err)
    levels: list[Level] = []
    kind: InstitutionType | None = None
    if data["levels"]:
        levels, err = parse_levels(data["levels"])
        if err:
            errors.append(err)
        elif allowed is not None and not set(levels) <= allowed:
            other = ", ".join(LEVEL_LABEL[lv] for lv in levels if lv not in allowed)
            errors.append(f"this body does not register {other} institutions")
        else:
            kind, err = parse_type(data["type"], levels)
            if err:
                errors.append(err)
    phone = None
    if data["phone"]:
        try:
            parsed = phonenumbers.parse(data["phone"], default_region)
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException, ValueError:
            errors.append(f"phone '{data['phone']}' is not a valid number")
    email = _blank(data["email"])
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors.append(f"email '{email}' is not valid")
    lat = lng = None
    if data["latitude"] or data["longitude"]:
        try:
            lat, lng = float(data["latitude"]), float(data["longitude"])
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError
        except ValueError:
            errors.append("latitude and longitude must both be numbers in range")
            lat = lng = None
    for part in ADDRESS_FIELDS:
        if part in rules.required and not data[part]:
            errors.append(f"{ADDRESS_LABEL[part]} is required")
    for part, limit in (("address_line1", 255), ("address_line2", 255), ("town", 100)):
        if len(data[part]) > limit:
            errors.append(f"{ADDRESS_LABEL[part]} must be at most {limit} characters")
    postcode = normalize_postcode(data["postcode"]) if data["postcode"] else None
    if postcode and rules.postcode_pattern:
        # "440 001" is a correct Nigerian code typed with a space: try it without spaces too.
        # A format whose codes contain a space (UK "SW1A 1AA") matches as typed.
        pattern = rules.postcode_pattern
        match = next(
            (c for c in (postcode, postcode.replace(" ", "")) if re.fullmatch(pattern, c)), None
        )
        if match is None:
            example = f", e.g. {rules.postcode_example}" if rules.postcode_example else ""
            typed = data["postcode"]
            errors.append(
                f"{rules.postcode_label} '{typed}' is not in the expected format{example}"
            )
        postcode = match
    code = _blank(data["government_code"])
    if code and not re.fullmatch(r"[A-Za-z0-9/\-. ]{2,40}", code):
        errors.append("government code has unexpected characters")
    ownership, err = parse_ownership(data["ownership"])
    if err:
        errors.append(err)
    proprietor = _blank(re.sub(r"\s+", " ", data["proprietor"]))
    if proprietor and len(proprietor) > 200:
        errors.append("proprietor must be at most 200 characters")
    if proprietor and ownership == "public":
        errors.append("proprietor is only for private institutions; set ownership to Private")
    if ownership == "private" and levels and set(levels) & self_registering:
        offered = ", ".join(LEVEL_LABEL[lv] for lv in levels if lv in self_registering)
        errors.append(
            f"private {offered} institutions apply online themselves (state directive); "
            "leave them out of this list"
        )
    clean = None
    if not errors and placed is not None and kind is not None:
        clean = CleanRow(
            name=name,
            name_key=fold(name),
            parent_scope_id=placed[0],
            location=placed[1],
            institution_type=kind,
            levels=levels,
            government_code=code.upper() if code else None,
            address_line1=_blank(data["address_line1"]),
            address_line2=_blank(data["address_line2"]),
            town=_blank(data["town"]),
            postcode=postcode,
            phone=phone,
            email=email.lower() if email else None,
            latitude=lat,
            longitude=lng,
            ownership=ownership or "public",
            proprietor_name=proprietor,
        )
    return RowResult(row_no, dict(raw), clean, errors)


def mark_duplicates(
    results: list[RowResult],
    *,
    existing_codes: set[str],
    existing_names: set[tuple[str, str, str]],
) -> None:
    """An institution already registered, or listed twice in the file, is an error on that row.

    Same institution = same government code, or the same name (ignoring case/punctuation)
    under the same parent in the same location.
    """
    seen_codes: dict[str, int] = {}
    seen_names: dict[tuple[str, str, str], int] = {}
    for r in results:
        c = r.clean
        if c is None:
            continue
        name_key = (c.parent_scope_id, fold(c.location), c.name_key)
        if c.government_code and c.government_code in existing_codes:
            r.errors.append(
                f"an institution with government code {c.government_code} is already registered"
            )
        elif name_key in existing_names:
            r.errors.append(f"'{c.name}' is already registered in {c.location}")
        elif c.government_code and c.government_code in seen_codes:
            r.errors.append(
                f"government code {c.government_code} also appears on row "
                f"{seen_codes[c.government_code]}"
            )
        elif name_key in seen_names:
            r.errors.append(
                f"'{c.name}' also appears on row {seen_names[name_key]} for {c.location}"
            )
        if r.errors:
            r.clean = None
            continue
        if c.government_code:
            seen_codes[c.government_code] = r.row_no
        seen_names[name_key] = r.row_no


def summarise(results: Iterable[RowResult]) -> dict[str, object]:
    rs = list(results)
    valid = [r for r in rs if r.clean is not None]
    by_lga: dict[str, int] = {}
    by_level: dict[str, int] = {}
    by_type: dict[str, int] = {}
    by_ownership: dict[str, int] = {}
    for r in valid:
        assert r.clean is not None
        by_lga[r.clean.location] = by_lga.get(r.clean.location, 0) + 1
        o = OWNERSHIP_LABEL[r.clean.ownership]
        by_ownership[o] = by_ownership.get(o, 0) + 1
        t = TYPE_LABEL[r.clean.institution_type]
        by_type[t] = by_type.get(t, 0) + 1
        for lv in r.clean.levels:
            by_level[LEVEL_LABEL[lv]] = by_level.get(LEVEL_LABEL[lv], 0) + 1
    return {
        "rows": len(rs),
        "valid": len(valid),
        "errors": len(rs) - len(valid),
        "by_lga": dict(sorted(by_lga.items())),
        "by_type": by_type,
        "by_ownership": by_ownership,
        "by_level": by_level,
    }
