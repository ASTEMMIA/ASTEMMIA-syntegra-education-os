from datetime import date

import pytest
from pydantic import ValidationError

from syntegra.core.errors import ValidationFailed
from syntegra.governance.structure import load_structure
from syntegra.institutions.body_rules import BodySpec, Catalogue, check_link, load_catalogue
from syntegra.mdm.domain import IdentifierScheme, normalize_identifier


def test_bundled_catalogues_are_valid_and_abia_uses_them():
    ng, intl = load_catalogue("ng"), load_catalogue("international")
    codes = {b.code for b in ng.bodies} | {b.code for b in intl.bodies}
    assert {"WAEC", "NECO", "NABTEB", "JAMB", "NUC", "NBTE", "NCCE", "CAMBRIDGE"} <= codes
    assert load_structure("ng-abia").education_bodies == ["ng", "international"]


@pytest.mark.parametrize(
    "bad",
    [
        {"code": "x", "name": "Lower", "scope": "national", "roles": ["funding"]},
        {"code": "EXAMS", "name": "No exams", "scope": "national", "roles": ["examination"]},
        {
            "code": "EB",
            "name": "Bad country",
            "scope": "national",
            "roles": ["funding"],
            "countries": ["Nigeria"],
        },
        {"code": "EB", "name": "Bad role", "scope": "national", "roles": ["inspector"]},
        {
            "code": "EB",
            "name": "Dup exams",
            "scope": "national",
            "roles": ["examination"],
            "examinations": [
                {"code": "A", "name": "A", "levels": ["sss"]},
                {"code": "A", "name": "A2", "levels": ["sss"]},
            ],
        },
    ],
)
def test_bad_body_entries_are_refused(bad):
    with pytest.raises(ValidationError):
        BodySpec.model_validate(bad)


def test_duplicate_codes_in_a_catalogue_are_refused():
    b = {"code": "EB", "name": "Body", "scope": "national", "roles": ["funding"]}
    with pytest.raises(ValidationError, match="unique"):
        Catalogue.model_validate({"version": 1, "name": "t", "bodies": [b, b]})


def _check(body, **kw):
    args = dict(
        link_type="exam_centre",
        institution_type="school",
        institution_levels={"sss"},
        exam_code="WASSCE-SC",
        reference="123",
        status="active",
        valid_from=None,
        valid_to=None,
    )
    return check_link(body, **(args | kw))


def test_rules():
    ng = {b.code: b for b in load_catalogue("ng").bodies}
    assert _check(ng["WAEC"]) == []
    assert _check(ng["WAEC"], link_type="nonsense")[0].startswith(
        "'nonsense' is not a kind of link"
    )
    assert "status must be one of" in _check(ng["WAEC"], status="maybe")[0]
    assert (
        _check(
            ng["NUC"],
            link_type="accreditation",
            exam_code=None,
            reference=None,
            institution_type="university",
            institution_levels={"degree"},
        )
        == []
    )
    assert "only an exam centre names an examination" in _check(
        ng["NUC"],
        link_type="accreditation",
        exam_code="X",
        institution_type="university",
        institution_levels={"degree"},
    )
    nabteb = _check(
        ng["NABTEB"],
        exam_code="NBC-NTC",
        institution_type="technical_college",
        institution_levels={"tvet"},
    )
    assert nabteb == []
    assert (
        _check(
            ng["UBEC"],
            link_type="beneficiary",
            exam_code=None,
            reference=None,
            institution_levels={"primary"},
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
        == []
    )


def test_new_candidate_number_schemes():
    assert normalize_identifier(IdentifierScheme.NECO, "1012 3456ab") == "10123456AB"
    assert normalize_identifier(IdentifierScheme.NABTEB, "nab-2026-0001") == "NAB20260001"
    with pytest.raises(ValidationFailed):
        normalize_identifier(IdentifierScheme.NECO, "12")


def test_adult_and_non_formal_education():
    from syntegra.institutions.domain import InstitutionType, Level, parse_levels, parse_type

    levels, err = parse_levels("Adult literacy; post-literacy")
    assert levels == [Level.ADULT] and err is None
    assert parse_type("", levels) == (InstitutionType.ADULT_LEARNING_CENTRE, None)
    assert parse_type("Mass Literacy Centre", levels)[0] is InstitutionType.ADULT_LEARNING_CENTRE
    assert "Adult level only" in parse_type("Literacy centre", [Level.ADULT, Level.PRIMARY])[1]
    assert "for adult learning centres" in parse_type("School", [Level.PRIMARY, Level.ADULT])[1]
    ng = {b.code: b for b in load_catalogue("ng").bodies}
    assert (
        _check(
            ng["NMEC"],
            link_type="accreditation",
            exam_code=None,
            reference=None,
            institution_type="adult_learning_centre",
            institution_levels={"adult"},
        )
        == []
    )
    assert (
        "does not cover"
        in _check(
            ng["NMEC"],
            link_type="accreditation",
            exam_code=None,
            reference=None,
            institution_levels={"primary"},
        )[0]
    )
