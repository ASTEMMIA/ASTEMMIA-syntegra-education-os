import pytest

from syntegra.institutions.domain import (
    InstitutionType,
    Level,
    Placement,
    fold,
    mark_duplicates,
    parse_levels,
    parse_type,
    summarise,
    validate_row,
)

# ASUBEB-like body: institutions go under its LGEAs.
SCHOOLS = Placement(
    "asubeb",
    {"umuahia north": ("lga-1", "Umuahia North"), "aba south": ("lga-2", "Aba South")},
    {"umuahia north": "Umuahia North", "aba south": "Aba South"},
)
# Ministry-like body: no units, institutions go directly under it; location checked against areas.
TERTIARY = Placement("ministry", {}, {"umuahia north": "Umuahia North", "aba south": "Aba South"})


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
        ("Nursery/Primary", [Level.ECCDE, Level.PRIMARY]),
        ("Kindergarten", [Level.ECCDE]),
        ("ND/HND", [Level.ND, Level.HND]),
        ("OND, HND", [Level.ND, Level.HND]),
        ("NCE", [Level.NCE]),
        ("B.Sc; M.Sc; PhD", [Level.DEGREE, Level.POSTGRADUATE]),
        ("NTC/NBC", [Level.TVET]),
        ("Degree & PGD", [Level.DEGREE, Level.POSTGRADUATE]),
    ],
)
def test_levels_accept_real_world_spellings(text, levels):
    assert parse_levels(text) == (levels, None)


def test_unknown_level_says_what_to_use():
    _, err = parse_levels("Diploma of Awesomeness")
    assert "Primary, JSS, SSS, ECCDE, TVET, NCE, ND, HND, Degree, Postgraduate or Adult" in err


@pytest.mark.parametrize(
    "text,kind",
    [
        ("Poly", InstitutionType.POLYTECHNIC),
        ("University", InstitutionType.UNIVERSITY),
        ("COE", InstitutionType.COLLEGE_OF_EDUCATION),
        ("College of Health Technology", InstitutionType.COLLEGE_OF_HEALTH),
        ("School of Nursing", InstitutionType.COLLEGE_OF_HEALTH),
        ("Govt. Technical College", InstitutionType.TECHNICAL_COLLEGE),
    ],
)
def test_institution_types(text, kind):
    assert parse_type(text, [Level.ND]) == (kind, None)


def test_type_rules():
    assert parse_type("", [Level.PRIMARY]) == (InstitutionType.SCHOOL, None)  # schools may omit it
    assert "required for tertiary" in parse_type("", [Level.ND])[1]
    assert "cannot offer tertiary" in parse_type("School", [Level.PRIMARY, Level.DEGREE])[1]
    assert "must offer at least one tertiary level" in parse_type("University", [Level.SSS])[1]


def test_school_goes_under_its_lgea():
    c = validate_row(2, row(lga="UMUAHIA NORTH LGEA"), SCHOOLS).clean
    assert (
        c.parent_scope_id == "lga-1"
        and c.location == "Umuahia North"
        and c.institution_type is InstitutionType.SCHOOL
    )
    assert validate_row(2, row(lga="Umuahia North L.G.A."), SCHOOLS).clean is not None
    bad = validate_row(2, row(lga="Atlantis"), SCHOOLS)
    assert bad.clean is None and "not one of this body's units" in bad.errors[0]


def test_tertiary_goes_directly_under_its_body_with_location_checked():
    c = validate_row(
        2,
        row(
            name="Example Polytechnic", type="Polytechnic", lga="aba south l.g.a.", levels="ND/HND"
        ),
        TERTIARY,
    ).clean
    assert c.parent_scope_id == "ministry" and c.location == "Aba South"
    assert c.institution_type is InstitutionType.POLYTECHNIC and c.levels == [Level.ND, Level.HND]
    bad = validate_row(
        2, row(name="Example Polytechnic", type="Poly", lga="Lagos Island", levels="ND"), TERTIARY
    )
    assert "not a local government area of this state" in bad.errors[0]


def test_body_registers_only_its_levels():
    r = validate_row(2, row(levels="JSS & SSS"), SCHOOLS, allowed=frozenset({Level.PRIMARY}))
    assert r.clean is None and r.errors == ["this body does not register JSS, SSS institutions"]
    r = validate_row(
        2,
        row(type="University", levels="Degree"),
        SCHOOLS,
        allowed=frozenset({Level.ECCDE, Level.PRIMARY, Level.JSS, Level.SSS}),
    )
    assert r.errors == ["this body does not register Degree institutions"]


def test_required_fields_phone_email_and_coordinates():
    r = validate_row(2, {"name": "", "lga": "", "levels": ""}, SCHOOLS)
    assert {"name is required", "lga is required", "levels is required"} <= set(r.errors)
    r = validate_row(
        2,
        row(phone="0803 123 4567", email="Head@School.ng", latitude="5.52", longitude="7.49"),
        SCHOOLS,
    )
    assert r.clean.phone == "+2348031234567" and r.clean.email == "head@school.ng"
    r = validate_row(2, row(phone="123", latitude="abc", longitude="7"), SCHOOLS)
    assert len(r.errors) == 2


def test_duplicates_in_file_and_already_registered():
    rs = [
        validate_row(2, row(), SCHOOLS),
        validate_row(
            3, row(name="COMMUNITY PRIMARY SCHOOL, NDUME"), SCHOOLS
        ),  # same school, other spelling
        validate_row(
            4, row(name="Community Primary School Ndume", lga="Aba South"), SCHOOLS
        ),  # other LGA: fine
        validate_row(5, row(name="Town School", government_code="ab/123"), SCHOOLS),
        validate_row(6, row(name="Central School", government_code="AB/123"), SCHOOLS),
        validate_row(7, row(name="Old School"), SCHOOLS),
    ]
    mark_duplicates(
        rs, existing_codes=set(), existing_names={("lga-1", "umuahia north", fold("Old School"))}
    )
    assert [r.clean is not None for r in rs] == [True, False, True, True, False, False]
    assert (
        "row 2" in rs[1].errors[0]
        and "row 5" in rs[4].errors[0]
        and "already registered" in rs[5].errors[0]
    )
    s = summarise(rs)
    assert (
        s["valid"] == 3
        and s["by_lga"] == {"Aba South": 1, "Umuahia North": 2}
        and s["by_type"] == {"School": 3}
    )
