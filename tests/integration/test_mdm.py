"""Person 360 services against the database: duplicate checks, scope privacy, merge/un-merge."""

from contextlib import asynccontextmanager
from datetime import date

import pytest
from sqlalchemy import select

from syntegra.core.clock import today
from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from syntegra.mdm import api as mdm
from syntegra.mdm.domain import ContactKind, IdentifierScheme, Sex
from syntegra.mdm.models import ExternalIdentifier, MatchReview, Person
from syntegra.platform.audit_store import verify_day
from syntegra.platform.crypto import build_field_crypto
from syntegra.platform.models import AuditEvent

CRYPTO = build_field_crypto(
    env="test", kek_b64=None, previous_keks_b64=[], blind_index_key_b64=None
)


def ada(**kw):
    base = dict(
        given_name="Adébáyọ̀",
        family_name="Ọláòyè",
        date_of_birth=date(2014, 3, 7),
        sex_at_registration=Sex.MALE,
        nationality="NG",
        primary_language="yo",
        contacts=(mdm.ContactIn(ContactKind.PHONE, "0803 123 4567", True),),
        identifiers=(mdm.IdentifierIn(IdentifierScheme.NIN, "12345678901"),),
    )
    base.update(kw)
    return mdm.PersonIn(**base)


@asynccontextmanager
async def as_async(c):
    with use_context(c):
        yield


@asynccontextmanager
async def ctx(world, user=None, scope=None, purpose=None):
    with use_context(
        RequestContext(
            world.school_a_tenant,
            scope or world.school_a_root,
            user or world.registrar_a,
            purpose=purpose,
        )
    ):
        yield


async def test_create_encrypts_identifier_and_scores_quality(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        r = await mdm.create_person(s, CRYPTO, ada())
        v = await mdm.get_person(s, r.person_id)
        assert v.identifiers[0].masked == "•••••••8901" and v.contacts[0].value == "+2348031234567"
        assert v.quality["score"] >= 90
        row = (await s.execute(select(ExternalIdentifier))).scalar_one()
        assert b"12345678901" not in row.value_enc
    async with sm() as s, s.begin(), ctx(world, purpose="exam-registration"):
        assert (
            await mdm.reveal_identifier(s, CRYPTO, r.person_id, v.identifiers[0].id)
            == "12345678901"
        )
    async with sm() as s:
        reveal = (
            await s.execute(select(AuditEvent).where(AuditEvent.action == "reveal"))
        ).scalar_one()
        assert reveal.purpose == "exam-registration" and reveal.actor_id == world.registrar_a
        assert (await verify_day(s, world.school_a_tenant, today()))[1] == []


async def test_reveal_requires_a_purpose(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        r = await mdm.create_person(s, CRYPTO, ada())
        v = await mdm.get_person(s, r.person_id)
        with pytest.raises(ValidationFailed, match="why"):
            await mdm.reveal_identifier(s, CRYPTO, r.person_id, v.identifiers[0].id)


async def test_same_nin_is_blocked_outright(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        await mdm.create_person(s, CRYPTO, ada())
        with pytest.raises(mdm.DuplicateBlocked) as exc:
            await mdm.create_person(s, CRYPTO, ada(given_name="Bayo", contacts=()))
        assert exc.value.extra["candidates"][0]["visible"] is True


async def test_review_band_creates_and_opens_a_review(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        first = await mdm.create_person(s, CRYPTO, ada())
        second = await mdm.create_person(
            s, CRYPTO, ada(given_name="Adebayo", family_name="Olaoye", identifiers=(), contacts=())
        )
        assert len(second.review_ids) == 1
        review = (await s.execute(select(MatchReview))).scalar_one()
        assert review.candidate_id == first.person_id and review.status == "open"
        assert {r["code"] for r in review.reasons} >= {"dob_exact", "family_name_exact"}
        assert (await mdm.get_person(s, second.person_id)).quality["uniqueness"] == 0


async def test_steward_can_override_a_block_with_a_reason(sm, world):
    from syntegra.governance import api as governance

    async with sm() as s, s.begin(), ctx(world, user=world.head_a):
        # With default thresholds only a shared national ID blocks (and cannot be overridden).
        # A school that blocks earlier lets its steward override with a recorded reason.
        await governance.set_config(
            s, scope_node_id=world.school_a_root, key="mdm.match.auto_link", value=80
        )
    async with sm() as s, s.begin(), ctx(world):
        first = await mdm.create_person(s, CRYPTO, ada(identifiers=()))
        twin = ada(identifiers=(), given_name="Adébáyọ̀")  # identical paper record
        with pytest.raises(mdm.DuplicateBlocked):
            await mdm.create_person(s, CRYPTO, twin)
        with pytest.raises(ValidationFailed, match="reason"):
            await mdm.create_person(s, CRYPTO, twin, not_duplicate_of=[first.person_id])
        r = await mdm.create_person(
            s,
            CRYPTO,
            twin,
            not_duplicate_of=[first.person_id],
            override_reason="Twins: birth certificates checked",
        )
        review = (
            await s.execute(select(MatchReview).where(MatchReview.person_id == r.person_id))
        ).scalar_one()
        assert review.status == "different_people" and review.decision_note.startswith("Twins")


async def test_candidates_outside_scope_are_hidden_but_still_caught(sm, world):
    async with sm() as s, s.begin(), ctx(world, scope=world.school_a_campus_1):
        await mdm.create_person(
            s, CRYPTO, ada(scope_node_id=world.school_a_campus_1, identifiers=())
        )
    async with (
        sm() as s,
        s.begin(),
        ctx(world, user=world.registrar_campus_2, scope=world.school_a_campus_2),
    ):
        found = await mdm.find_candidates(s, CRYPTO, ada(identifiers=()))
        assert found and found[0].visible is False
        assert mdm._candidate_json(found[0]).keys() == {"visible", "score", "outcome", "message"}
        r = await mdm.create_person(
            s,
            CRYPTO,
            ada(
                identifiers=(),
                contacts=(),
                family_name="Olaoye",
                given_name="Adebayo",
                scope_node_id=world.school_a_campus_2,
            ),
        )
        assert r.review_ids
    async with sm() as s, ctx(world):  # the steward at the school root sees the review
        reviews = await mdm.list_reviews(s)
        assert len(reviews) == 1 and reviews[0].scope_node_id == world.school_a_root


async def test_other_tenants_never_see_or_match(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        r = await mdm.create_person(s, CRYPTO, ada())
    async with (
        sm() as s,
        s.begin(),
        as_async(RequestContext(world.school_b_tenant, world.school_b_root, world.head_b)),
    ):
        assert await mdm.find_candidates(s, CRYPTO, ada()) == []
        with pytest.raises(NotFound):
            await mdm.get_person(s, r.person_id)


async def test_update_needs_current_version_and_keeps_previous_name(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        r = await mdm.create_person(s, CRYPTO, ada())
        v = await mdm.get_person(s, r.person_id)
        with pytest.raises(PreconditionFailed):
            await mdm.update_person(
                s, r.person_id, expected_version=v.row_version + 5, changes={"family_name": "Okon"}
            )
        v2 = await mdm.update_person(
            s, r.person_id, expected_version=v.row_version, changes={"family_name": "Okon"}
        )
        assert v2.row_version == v.row_version + 1
        assert v2.other_recorded_names[0]["family_name"] == "Ọláòyè"
        # The old name still matches later paperwork.
        found = await mdm.find_candidates(s, CRYPTO, ada(identifiers=(), contacts=()))
        assert found and found[0].person_id == r.person_id


async def test_merge_moves_data_keeps_alias_and_reverses_cleanly(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        a = await mdm.create_person(s, CRYPTO, ada(identifiers=(), source_system="import"))
        b = await mdm.create_person(
            s,
            CRYPTO,
            ada(
                given_name="Adebayo",
                family_name="Olaoye",
                contacts=(mdm.ContactIn(ContactKind.EMAIL, "olaoye@example.ng"),),
                source_system="nimc",
            ),
        )
        (review_id,) = b.review_ids
        merge_id = await mdm.decide_review(
            s, review_id, same_person=True, note="Same birth certificate"
        )
        survivor = await mdm.get_person(s, a.person_id)
        assert survivor.given_name == "Adebayo"  # NIMC-sourced value outranks the import
        assert (
            survivor.identifiers
            and len(survivor.contacts) == 2
            and survivor.merged_from == [b.person_id]
        )
        assert (await mdm.get_person(s, b.person_id)).id == a.person_id  # merged id is an alias
        with pytest.raises(ValidationFailed):
            await mdm.merge_persons(s, survivor_id=a.person_id, merged_id=a.person_id, reason="x")

        kept = await mdm.unmerge(s, merge_id, reason="Wrong decision")
        assert kept == []
        restored_a, restored_b = (
            await mdm.get_person(s, a.person_id),
            await mdm.get_person(s, b.person_id),
        )
        assert (
            restored_a.given_name == "Adébáyọ̀"
            and not restored_a.identifiers
            and len(restored_a.contacts) == 1
        )
        assert (
            restored_b.status == "active"
            and restored_b.identifiers
            and restored_b.contacts[0].kind == "email"
        )
        with pytest.raises(Conflict):
            await mdm.unmerge(s, merge_id, reason="again")


async def test_merge_refuses_conflicting_national_ids(sm, world):
    async with sm() as s, s.begin(), ctx(world):
        a = await mdm.create_person(s, CRYPTO, ada())
        b = await mdm.create_person(
            s,
            CRYPTO,
            ada(
                given_name="Kehinde",
                contacts=(),
                identifiers=(mdm.IdentifierIn(IdentifierScheme.NIN, "10987654321"),),
            ),
        )
        with pytest.raises(Conflict, match="different NIN"):
            await mdm.merge_persons(
                s, survivor_id=a.person_id, merged_id=b.person_id, reason="test"
            )


async def test_thresholds_come_from_configuration(sm, world):
    from syntegra.governance import api as governance

    async with sm() as s, s.begin(), ctx(world, user=world.head_a):
        await governance.set_config(
            s, scope_node_id=world.school_a_root, key="mdm.match.review", value=90
        )
        await governance.set_config(
            s, scope_node_id=world.school_a_root, key="mdm.match.auto_link", value=95
        )
        assert (await mdm.thresholds(s, world.school_a_root)).review == 90
        await mdm.create_person(s, CRYPTO, ada(identifiers=()))
        r = await mdm.create_person(
            s, CRYPTO, ada(given_name="Adebayo", family_name="Olaoye", identifiers=(), contacts=())
        )
        assert r.review_ids == []  # 68 is below this school's stricter review band
        assert len((await s.execute(select(Person))).scalars().all()) == 2
