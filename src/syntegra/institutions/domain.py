"""Pure rules for the school register and its bulk import. No framework or database imports."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import phonenumbers


class Level(StrEnum):
    """Education levels offered (CEDA §2, Nigeria configuration; ISCED in brackets)."""

    ECCDE = "eccde"  # early childhood (0)
    PRIMARY = "primary"  # Primary 1-6 (1)
    JSS = "jss"  # junior secondary (2)
    SSS = "sss"  # senior secondary (3)


LEVEL_LABEL = {Level.ECCDE: "ECCDE", Level.PRIMARY: "Primary", Level.JSS: "JSS", Level.SSS: "SSS"}

# Spellings seen in real school lists. "secondary" alone means both JSS and SSS.
_LEVEL_ALIASES: dict[str, tuple[Level, ...]] = {
    "eccde": (Level.ECCDE,),
    "ece": (Level.ECCDE,),
    "nursery": (Level.ECCDE,),
    "preprimary": (Level.ECCDE,),
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
}

TEMPLATE_COLUMNS = (
    "name",
    "lga",
    "levels",
    "government_code",
    "town",
    "address",
    "phone",
    "email",
    "latitude",
    "longitude",
)
REQUIRED = ("name", "lga", "levels")
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
            return [], f"level '{part.strip()}' is not recognised. Use Primary, JSS, SSS or ECCDE."
        for lv in levels:
            if lv not in found:
                found.append(lv)
    if not found:
        return [], "levels are missing. Use Primary, JSS, SSS or ECCDE."
    order = list(Level)
    return sorted(found, key=order.index), None


@dataclass
class CleanRow:
    name: str
    name_key: str
    lga_scope_id: str
    lga_name: str
    levels: list[Level]
    government_code: str | None = None
    town: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "lga": self.lga_name,
            "lga_scope_id": self.lga_scope_id,
            "levels": [lv.value for lv in self.levels],
            "government_code": self.government_code,
            "town": self.town,
            "address": self.address,
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


def _blank(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def validate_row(
    row_no: int,
    raw: Mapping[str, str],
    lgas: Mapping[str, tuple[str, str]],
    *,
    default_region: str = "NG",
) -> RowResult:
    """``lgas`` maps fold(name) -> (scope id, display name); 'X LGEA' and 'X' both resolve."""
    data = {k: (raw.get(k) or "").strip() for k in TEMPLATE_COLUMNS}
    errors: list[str] = []
    for col in REQUIRED:
        if not data[col]:
            errors.append(f"{col} is required")
    name = re.sub(r"\s+", " ", data["name"])
    if name and not 3 <= len(name) <= 200:
        errors.append("name must be 3 to 200 characters")
    lga = None
    if data["lga"]:
        key = fold(data["lga"]).replace("l g e a", "lgea").replace("l g a", "lga")
        bare = re.sub(
            r"\s*(lgea|lga|local government( education authority| area)?)$", "", key
        ).strip()
        lga = lgas.get(key) or lgas.get(bare)
        if lga is None:
            errors.append(f"LGA '{data['lga']}' is not one of this board's LGEAs")
    levels: list[Level] = []
    if data["levels"]:
        levels, err = parse_levels(data["levels"])
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
    code = _blank(data["government_code"])
    if code and not re.fullmatch(r"[A-Za-z0-9/\-. ]{2,40}", code):
        errors.append("government code has unexpected characters")
    clean = None
    if not errors and lga is not None:
        clean = CleanRow(
            name=name,
            name_key=fold(name),
            lga_scope_id=lga[0],
            lga_name=lga[1],
            levels=levels,
            government_code=code.upper() if code else None,
            town=_blank(data["town"]),
            address=_blank(data["address"]),
            phone=phone,
            email=email.lower() if email else None,
            latitude=lat,
            longitude=lng,
        )
    return RowResult(row_no, dict(raw), clean, errors)


def mark_duplicates(
    results: list[RowResult],
    *,
    existing_codes: set[str],
    existing_names: set[tuple[str, str]],
) -> None:
    """A school already registered, or listed twice in the file, is an error on that row.

    Same school = same government code, or same name (ignoring case/punctuation) in the same LGA.
    """
    seen_codes: dict[str, int] = {}
    seen_names: dict[tuple[str, str], int] = {}
    for r in results:
        c = r.clean
        if c is None:
            continue
        name_key = (c.lga_scope_id, c.name_key)
        if c.government_code and c.government_code in existing_codes:
            r.errors.append(
                f"a school with government code {c.government_code} is already registered"
            )
        elif name_key in existing_names:
            r.errors.append(f"'{c.name}' is already registered in {c.lga_name}")
        elif c.government_code and c.government_code in seen_codes:
            r.errors.append(
                f"government code {c.government_code} also appears on row "
                f"{seen_codes[c.government_code]}"
            )
        elif name_key in seen_names:
            r.errors.append(
                f"'{c.name}' also appears on row {seen_names[name_key]} for {c.lga_name}"
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
    for r in valid:
        assert r.clean is not None
        by_lga[r.clean.lga_name] = by_lga.get(r.clean.lga_name, 0) + 1
        for lv in r.clean.levels:
            by_level[LEVEL_LABEL[lv]] = by_level.get(LEVEL_LABEL[lv], 0) + 1
    return {
        "rows": len(rs),
        "valid": len(valid),
        "errors": len(rs) - len(valid),
        "by_lga": dict(sorted(by_lga.items())),
        "by_level": by_level,
    }
