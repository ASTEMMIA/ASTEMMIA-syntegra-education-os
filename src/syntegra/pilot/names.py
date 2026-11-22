"""Made-up names for demonstration learners, guardians and teachers (ADR-031).

Common Nigerian names, mostly Igbo as in Abia State, combined at random: no combination is
meant to be a real person. Every record made from them is marked as demonstration data.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

GIRLS = (
    "Adaeze", "Chiamaka", "Ngozi", "Chioma", "Amarachi", "Ifeoma", "Nneka", "Obiageli", "Ugochi",
    "Chinyere", "Ogechi", "Ijeoma", "Nkechi", "Uchenna", "Chidinma", "Kosisochukwu", "Somtochukwu",
    "Munachi", "Chinenye", "Adanna", "Oluchi", "Ebere", "Onyinyechi", "Chisom", "Ozioma", "Nmesoma",
    "Akunna", "Ifunanya", "Chizoba", "Nkiruka", "Uloma", "Udoka", "Kamsiyochukwu", "Ginika",
    "Precious", "Blessing", "Favour", "Joy", "Grace", "Mercy", "Peace", "Gift", "Esther", "Ruth",
    "Funmilayo", "Aisha", "Hauwa", "Efe", "Imaobong", "Ekaette",
)  # fmt: skip
BOYS = (
    "Chinedu", "Emeka", "Obinna", "Ikenna", "Chukwuemeka", "Kelechi", "Uchenna", "Nnamdi",
    "Chidiebere", "Ifeanyi", "Somto", "Tobenna", "Chibuike", "Ebuka", "Kenechukwu", "Onyekachi",
    "Ugochukwu", "Chimezie", "Okechukwu", "Nonso", "Kamsi", "Chiemeka", "Izuchukwu", "Ekene",
    "Arinze", "Chukwudi", "Obiora", "Uzoma", "Nwachukwu", "Ikechukwu", "Chigozie", "Munachimso",
    "Daniel", "David", "Samuel", "Emmanuel", "Joshua", "Victor", "Michael", "Godswill",
    "Tunde", "Segun", "Musa", "Ibrahim", "Efosa", "Etim", "Ubong", "Tamuno", "Ebi", "Zubairu",
)  # fmt: skip
FAMILY = (
    "Okafor", "Okeke", "Eze", "Nwosu", "Okoro", "Nwankwo", "Obi", "Onyeka", "Uche", "Okonkwo",
    "Nwachukwu", "Chukwu", "Ibe", "Okoye", "Udeh", "Agu", "Nnadi", "Ogbonna", "Kalu", "Uzor",
    "Onwuka", "Ndukwe", "Iroegbu", "Nwaogu", "Amadi", "Onuoha", "Orji", "Ukaegbu", "Nwokocha",
    "Egwu", "Mbah", "Anyanwu", "Iwuchukwu", "Ihejirika", "Ogbu", "Akuma", "Igwe", "Ekeh",
    "Nwaneri", "Obioha", "Uwakwe", "Okorie", "Ezeh", "Njoku", "Emenike", "Oti", "Azubuike",
    "Nwafor", "Okwu", "Onyekwere", "Ejiofor", "Mgbemena", "Ugwu", "Odoemelam", "Ikwuagwu",
    "Nwabueze", "Okwara", "Uduma", "Ogwo", "Agwu", "Ojukwu", "Chikere", "Nwogu", "Ekwueme",
    "Adebayo", "Bello", "Ibrahim", "Etim", "Bassey", "Okon",
)  # fmt: skip
PHONE_PREFIX = "0700"  # business numbers, not mobiles: no demonstration number reaches a phone


@dataclass(frozen=True, slots=True)
class Child:
    given: str
    other: str
    family: str
    sex: str
    born: date
    guardian: str
    guardian_relationship: str
    guardian_phone: str


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


class Names:
    """A seeded source of distinct made-up people: the same seed gives the same people."""

    def __init__(self, seed: int = 2026) -> None:
        self._r = random.Random(seed)  # noqa: S311 - reproducible made-up names, not secrets
        self._seen: set[tuple[str, str, date]] = set()
        self._phones = 0

    def phone(self) -> str:
        self._phones += 1
        if self._phones > 999_999:
            raise RuntimeError("out of demonstration phone numbers")
        return f"{PHONE_PREFIX}{self._phones:07d}"

    def adult(self) -> tuple[str, str, str, date]:
        sex = self._r.choice("FM")
        given = self._r.choice(GIRLS if sex == "F" else BOYS)
        born = date(self._r.randint(1968, 1998), self._r.randint(1, 12), self._r.randint(1, 28))
        return given, self._r.choice(FAMILY), sex, born

    def child(self, age_on_start: int, session_start: date, sex_only: str | None = None) -> Child:
        """A child of this age at the start of the session, with a parent; ``sex_only`` for a
        girls' or boys' school."""
        for _ in range(1000):
            sex = sex_only or self._r.choice("FM")
            pool = GIRLS if sex == "F" else BOYS
            given, other = self._r.sample(pool, 2)
            family = self._r.choice(FAMILY)
            # Exactly this age on the first day of the session.
            latest = _years_before(session_start, age_on_start)
            earliest = _years_before(session_start, age_on_start + 1) + timedelta(days=1)
            born = latest - timedelta(days=self._r.randint(0, (latest - earliest).days))
            if (given, family, born) in self._seen:
                continue
            self._seen.add((given, family, born))
            mother = self._r.random() < 0.7
            parent = self._r.choice(GIRLS if mother else BOYS)
            return Child(
                given, other, family, sex, born,
                f"{parent} {family}", "mother" if mother else "father", self.phone(),
            )  # fmt: skip
        raise RuntimeError("could not make a distinct demonstration child")
