import pytest

from syntegra.institutions.domain import (
    Level,
    fold,
    mark_duplicates,
    parse_levels,
    summarise,
    validate_row,
)

LGAS = {
    "umuahia north": ("lga-1", "Umuahia North"),
    "umuahia north lgea": ("lga-1", "Umuahia North"),
    "aba south": ("lga-2", "Aba South"),
}


def row(**kw):
    base = {"name": "Community Primary School Ndume", "lga": "Umuahia North", "levels": "Primary"}
    base.update(kw)
    return base


@pytest.mark.parametrize(
    "text,levels",
    [
        ("Pry", [Level.PRIMARY]),
        ("JSS & SSS", [Level.JSS, Level.SSS]),
        ("Secondary", [Level.JSS, Level.SSS]),
        ("sss, jss", [Level.JSS, Level.SSS]),
        ("Nursery/Primary", [Level.ECCDE, Level.PRIMARY]),
    ],
)
def test_levels_accept_real_world_spellings(text, levels):
    assert parse_levels(text) == (levels, None)


def test_unknown_level_says_what_to_use():
    _, err = parse_levels("College")
    assert "Primary, JSS, SSS or ECCDE" in err


def test_lga_matches_with_or_without_lgea_suffix_and_accents():
    assert validate_row(2, row(lga="UMUAHIA NORTH LGEA"), LGAS).clean.lga_scope_id == "lga-1"
    assert validate_row(2, row(lga="Umuahia North L.G.A."), LGAS).clean is not None
    bad = validate_row(2, row(lga="Atlantis"), LGAS)
    assert bad.clean is None and "not one of this board's LGEAs" in bad.errors[0]


def test_required_fields_phone_email_and_coordinates():
    r = validate_row(2, {"name": "", "lga": "", "levels": ""}, LGAS)
    assert {"name is required", "lga is required", "levels is required"} <= set(r.errors)
    r = validate_row(
        2,
        row(phone="0803 123 4567", email="Head@School.ng", latitude="5.52", longitude="7.49"),
        LGAS,
    )
    assert r.clean.phone == "+2348031234567" and r.clean.email == "head@school.ng"
    r = validate_row(2, row(phone="123", latitude="abc", longitude="7"), LGAS)
    assert len(r.errors) == 2


def test_duplicates_in_file_and_already_registered():
    rs = [
        validate_row(2, row(), LGAS),
        validate_row(
            3, row(name="COMMUNITY PRIMARY SCHOOL, NDUME"), LGAS
        ),  # same school, other spelling
        validate_row(
            4, row(name="Community Primary School Ndume", lga="Aba South"), LGAS
        ),  # other LGA: fine
        validate_row(5, row(name="Town School", government_code="ab/123"), LGAS),
        validate_row(6, row(name="Central School", government_code="AB/123"), LGAS),
        validate_row(7, row(name="Old School"), LGAS),
    ]
    mark_duplicates(rs, existing_codes=set(), existing_names={("lga-1", fold("Old School"))})
    assert [r.clean is not None for r in rs] == [True, False, True, True, False, False]
    assert (
        "row 2" in rs[1].errors[0]
        and "row 5" in rs[4].errors[0]
        and "already registered" in rs[5].errors[0]
    )
    s = summarise(rs)
    assert (
        s["valid"] == 3 and s["errors"] == 3 and s["by_lga"] == {"Aba South": 1, "Umuahia North": 2}
    )
