import uuid
from datetime import date, datetime

import pytest

from syntegra.core.errors import ValidationFailed
from syntegra.mdm.domain import (
    ContactKind,
    IdentifierScheme,
    MatchOutcome,
    MatchThresholds,
    PersonFacts,
    QualityInput,
    Sex,
    SourcedRecord,
    assess_quality,
    normalize_contact,
    normalize_identifier,
    normalize_name,
    phonetic_keys,
    score_match,
    survive,
)
from syntegra.platform.crypto import BlindIndex, DecryptionError, FieldCipher, LocalKeyProvider

T = MatchThresholds()
PHONE = "+2348031234567"
BASE = PersonFacts("Adébáyọ̀", "Ọláòyè", None, date(2014, 3, 7), Sex.MALE, {}, frozenset({PHONE}))


def test_names_fold_diacritics_case_and_punctuation():
    assert normalize_name("Adébáyọ̀") == "adebayo"
    assert normalize_name("  OLÁ-ÒYÈ ") == "ola oye"
    assert normalize_name("N'Diaye") == "n diaye"
    assert phonetic_keys("Chukwuemeka") == phonetic_keys("Chukwuemeke")


@pytest.mark.parametrize(
    "other,outcome",
    [
        (PersonFacts("Adebayo", "Olaoye", None, date(2014, 3, 7), Sex.MALE), MatchOutcome.REVIEW),
        (
            PersonFacts(
                "Adebayo", "Olaoye", None, date(2014, 3, 7), Sex.MALE, {}, frozenset({PHONE})
            ),
            MatchOutcome.REVIEW,
        ),
        (
            PersonFacts("Olaoye", "Adebayo", None, date(2014, 3, 7), Sex.MALE),
            MatchOutcome.REVIEW,
        ),  # swapped names
        (
            PersonFacts("Adebayo", "Olaoye", None, date(2014, 7, 3), Sex.MALE),
            MatchOutcome.REVIEW,
        ),  # dd/mm swapped
        (
            PersonFacts("Adebayo", "Olayoye", None, date(2014, 3, 7), Sex.MALE),
            MatchOutcome.REVIEW,
        ),  # spelling
        (
            PersonFacts(
                "Kehinde", "Olaoye", None, date(2014, 3, 7), Sex.MALE, {}, frozenset({PHONE})
            ),
            MatchOutcome.REVIEW,
        ),  # twin
        (
            PersonFacts("Adebayo", "Olaoye", None, date(2012, 11, 20), Sex.MALE),
            MatchOutcome.NO_MATCH,
        ),  # namesake
        (
            PersonFacts(
                "Funmilayo", "Olaoye", None, date(2016, 5, 1), Sex.FEMALE, {}, frozenset({PHONE})
            ),
            MatchOutcome.NO_MATCH,
        ),  # sibling
    ],
    ids=[
        "no-accents",
        "with-phone",
        "names-swapped",
        "dob-swapped",
        "spelling",
        "twin",
        "namesake",
        "sibling",
    ],
)
def test_match_calibration(other, outcome):
    assert T.outcome(score_match(BASE, other).score) is outcome


def test_identifiers_decide_both_ways():
    nin = b"x" * 32
    a = PersonFacts("Ngozi", "Okafor", identifiers={"NIN": nin})
    assert (
        T.outcome(
            score_match(
                a, PersonFacts("Ngozie", "Okafor", None, date(2010, 1, 1), Sex.FEMALE, {"NIN": nin})
            ).score
        )
        is MatchOutcome.BLOCK
    )
    assert score_match(a, PersonFacts("Ngozi", "Okafor", identifiers={"NIN": b"y" * 32})).score == 0


def test_every_score_is_explained():
    m = score_match(BASE, PersonFacts("Adebayo", "Olaoye", None, date(2014, 3, 7), Sex.MALE))
    assert sum(r["points"] for r in m.explain()) == m.score
    assert {r["code"] for r in m.explain()} == {
        "dob_exact",
        "family_name_exact",
        "given_name_exact",
    }


def test_thresholds_must_be_ordered():
    with pytest.raises(ValidationFailed):
        MatchThresholds(auto_link=50, review=60)


def test_identifier_and_contact_normalisation():
    assert normalize_identifier(IdentifierScheme.NIN, "123 4567 8901") == "12345678901"
    with pytest.raises(ValidationFailed):
        normalize_identifier(IdentifierScheme.NIN, "1234")
    assert normalize_contact(ContactKind.PHONE, "0803 123 4567") == PHONE
    assert normalize_contact(ContactKind.EMAIL, "Ada@Example.NG") == "ada@example.ng"
    with pytest.raises(ValidationFailed):
        normalize_contact(ContactKind.PHONE, "12345")


def _q(**kw):
    base = dict(
        given_name="Ada",
        family_name="Obi",
        date_of_birth=date(2014, 1, 1),
        sex="F",
        nationality="NG",
        primary_language="ig",
        name_script="Latn",
        contact_count=1,
        identifier_count=1,
        verified_identifier_count=1,
        open_match_reviews=0,
        last_confirmed_at=datetime(2026, 11, 1),
    )
    base.update(kw)
    return QualityInput(**base)


def test_quality_index_scores_and_says_what_next():
    now, today = datetime(2026, 11, 21), date(2026, 11, 21)
    assert assess_quality(_q(), today=today, now=now).score == 100
    r = assess_quality(
        _q(
            identifier_count=0,
            verified_identifier_count=0,
            open_match_reviews=1,
            date_of_birth=date(2027, 1, 1),
        ),
        today=today,
        now=now,
    )
    codes = {i.code for i in r.issues}
    assert {"missing_identifier", "possible_duplicate", "dob_in_future"} <= codes
    assert r.score < 70 and all(i.message for i in r.issues)


def test_survivorship_prefers_trust_then_recency_then_fills_gaps():
    old, new = datetime(2025, 1, 1), datetime(2026, 1, 1)
    survivor = SourcedRecord(
        {"given_name": "Ada", "family_name": "Obi", "date_of_birth": None, "nationality": "NG"},
        "import",
        new,
    )
    merged = SourcedRecord(
        {
            "given_name": "Adaeze",
            "family_name": "Obi",
            "date_of_birth": "2014-01-01",
            "nationality": "GH",
        },
        "nimc",
        old,
    )
    d = {x.field: x for x in survive(survivor, merged)}
    assert d["given_name"].value == "Adaeze" and "trusted" in d["given_name"].reason
    assert (
        d["date_of_birth"].value == "2014-01-01"
        and d["date_of_birth"].reason == "filled a missing value"
    )
    d2 = {
        x.field: x
        for x in survive(
            SourcedRecord({"given_name": "Ada"}, "registry", old),
            SourcedRecord({"given_name": "Adaeze"}, "registry", new),
        )
    }
    assert d2["given_name"].value == "Adaeze" and "recent" in d2["given_name"].reason


def test_field_cipher_binds_ciphertext_to_its_row():
    cipher = FieldCipher(LocalKeyProvider(b"k" * 32))
    t, r = uuid.uuid4(), uuid.uuid4()
    blob = cipher.encrypt(
        "12345678901", tenant_id=t, table="mdm.external_identifier", column="value_enc", row_id=r
    )
    assert b"12345678901" not in blob
    assert (
        cipher.decrypt(
            blob, tenant_id=t, table="mdm.external_identifier", column="value_enc", row_id=r
        )
        == "12345678901"
    )
    with pytest.raises(DecryptionError):  # copied onto another row
        cipher.decrypt(
            blob,
            tenant_id=t,
            table="mdm.external_identifier",
            column="value_enc",
            row_id=uuid.uuid4(),
        )
    with pytest.raises(DecryptionError):  # tampered
        cipher.decrypt(
            blob[:-1] + bytes([blob[-1] ^ 1]),
            tenant_id=t,
            table="mdm.external_identifier",
            column="value_enc",
            row_id=r,
        )


def test_kek_rotation_keeps_old_ciphertext_readable():
    old, new = b"o" * 32, b"n" * 32
    t, r = uuid.uuid4(), uuid.uuid4()
    blob = FieldCipher(LocalKeyProvider(old)).encrypt(
        "x", tenant_id=t, table="a", column="b", row_id=r
    )
    assert (
        FieldCipher(LocalKeyProvider(new, [old])).decrypt(
            blob, tenant_id=t, table="a", column="b", row_id=r
        )
        == "x"
    )
    with pytest.raises(DecryptionError):
        FieldCipher(LocalKeyProvider(new)).decrypt(
            blob, tenant_id=t, table="a", column="b", row_id=r
        )


def test_blind_index_is_keyed_and_domain_separated():
    a, b = BlindIndex(b"a" * 32), BlindIndex(b"b" * 32)
    assert a.digest("id:NIN", "1") == a.digest("id:NIN", "1")
    assert a.digest("id:NIN", "1") != b.digest("id:NIN", "1")
    assert a.digest("id:NIN", "1") != a.digest("id:BVN", "1")
