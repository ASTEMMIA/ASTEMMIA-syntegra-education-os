"""Jurisdiction structure files (CEDA §2, P4 "global core, local configuration").

How a country or state organises education differs: in Abia State basic education sits under
ASUBEB (by LGEA) and tertiary under the Ministry of Tertiary Education; elsewhere the bodies,
their sub-units and what each registers are different. None of that is code. A structure file
describes it, and ``governance.api.apply_structure`` builds it.

Ranks follow from nesting: the jurisdiction tenant, then each body one level below it, then
the body's units one level below that. Institutions are always placed below those (level 6).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from syntegra.governance.domain import GovernanceLevel

CODE = r"^[A-Z0-9][A-Z0-9_-]{1,63}$"
BUNDLED = Path(__file__).resolve().parent.parent / "structures"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Country(_Model):
    code: str = Field(pattern=r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2, also the tenant code
    name: str
    time_zone: str
    label: str | None = None


class Jurisdiction(_Model):
    """A sub-national tenant (state, region, province).

    Omit it where bodies sit directly under the country.
    """

    code: str = Field(pattern=CODE)
    name: str
    label: str = "State"
    time_zone: str | None = None


class Units(_Model):
    """A body's local sub-units, e.g. Abia's 17 LGEAs or a country's school districts."""

    label: str  # shown to users, e.g. "LGEA"
    code_prefix: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,15}$")
    name_suffix: str = ""  # e.g. " LGEA" so "Ohafia" becomes "Ohafia LGEA"
    names: list[str] = Field(min_length=1)

    @field_validator("names")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len({n.strip().casefold() for n in v}) != len(v):
            raise ValueError("unit names must be unique")
        return [n.strip() for n in v]

    def code_for(self, name: str) -> str:
        return f"{self.code_prefix}-" + re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")


class Body(_Model):
    """An overseeing body: a board, ministry, commission or authority."""

    code: str = Field(pattern=CODE)
    name: str
    label: str  # e.g. "Board", "Ministry"
    # Education levels it registers (setting `register.levels`). An empty list means none yet.
    registers: list[str] | None = None
    units: Units | None = None


class Structure(_Model):
    version: int = Field(ge=1, le=1)
    country: Country
    jurisdiction: Jurisdiction | None = None
    bodies: list[Body] = Field(min_length=1)

    @model_validator(mode="after")
    def _checks(self) -> Structure:
        codes = [b.code for b in self.bodies]
        if len(set(codes)) != len(codes):
            raise ValueError("body codes must be unique")
        unit_codes = [
            u.code_for(n) for b in self.bodies if b.units for u in [b.units] for n in u.names
        ]
        if len(set(unit_codes)) != len(unit_codes) or set(unit_codes) & set(codes):
            raise ValueError("unit codes must be unique and differ from body codes")
        if self.unit_level >= GovernanceLevel.INSTITUTION:
            raise ValueError("too many levels above institutions")
        return self

    @property
    def tenant_level(self) -> GovernanceLevel:
        return GovernanceLevel.REGION if self.jurisdiction else GovernanceLevel.COUNTRY

    @property
    def body_level(self) -> GovernanceLevel:
        return GovernanceLevel(self.tenant_level + 1)

    @property
    def unit_level(self) -> GovernanceLevel:
        return GovernanceLevel(self.body_level + 1)


def load_structure(path_or_name: str) -> Structure:
    """A file path, or the name of a bundled structure such as 'ng-abia'."""
    path = Path(path_or_name)
    if not path.exists():
        bundled = BUNDLED / f"{path_or_name}.json"
        if not bundled.exists():
            names = ", ".join(sorted(p.stem for p in BUNDLED.glob("*.json")))
            raise FileNotFoundError(f"no structure file '{path_or_name}' (bundled: {names})")
        path = bundled
    return Structure.model_validate(json.loads(path.read_text(encoding="utf-8")))
