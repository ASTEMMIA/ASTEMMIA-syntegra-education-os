"""Ownership in the register (ADR-021): private tertiary institutions listed by the Ministry;
private schools still apply online to ASUBEB (state directive)."""

import pytest

from syntegra.core.context import RequestContext, use_context
from syntegra.core.errors import ValidationFailed
from syntegra.institutions import api as inst
from syntegra.institutions import bodies as eb
from syntegra.institutions.body_rules import load_catalogue
from syntegra.institutions.domain import parse_ownership
from tests.integration.test_tertiary_register import _abia

CSV = """name,type,lga,levels,address_line1,town,postcode,ownership,proprietor
TEST Abia State University,University,Isuikwuato,Degree; Postgraduate,1 Rd,Uturu,440001,State,
TEST Gregorian University,University,Umuahia North,Degree,2 Rd,Aba,440001,Private,Gregorian Fdn
TEST Federal Poly,Polytechnic,Ohafia,ND; HND,3 Rd,Ohafia,440001,Federal,
TEST Mystery College,College of education,Bende,NCE,4 Rd,Bende,440001,Mission,
TEST Owned By Nobody,University,Bende,Degree,5 Rd,Bende,440001,Government,Some Company
"""


def at(tenant, scope):
    return use_context(RequestContext(tenant.id, scope, None, system=True))


def test_ownership_words():
    assert parse_ownership("") == ("public", None)
    for word in ("Government", "govt", "State", "Federal Government", "public"):
        assert parse_ownership(word) == ("public", None), word
    assert parse_ownership("Privately owned") == ("private", None)
    assert "Use Government or Private" in parse_ownership("Mission")[1]


async def test_the_ministry_lists_private_tertiary_institutions(sm, world):
    tenant, bodies = await _abia(sm, world)
    ministry = bodies["MIN-TERTIARY-EDUCATION"]
    async with sm() as s, s.begin():
        with at(tenant, ministry):
            job_id = await inst.preview_import(s, CSV, source_name="tertiary.csv")
            job, rows = await inst.get_job(s, job_id)
            assert job.summary["valid"] == 3
            assert job.summary["by_ownership"] == {"Government": 2, "Private": 1}
            msgs = {r.row_no: r.messages[0] for r in rows if r.status == "error"}
            assert "ownership 'Mission' is not recognised" in msgs[5]
            assert "proprietor is only for private institutions" in msgs[6]
            await inst.commit_import(s, job_id, managing_body="Ministry")
            private = await inst.list_institutions(s, ownership="private")
            assert [(v.name, v.ownership, v.proprietor_name) for v in private] == [
                ("TEST Gregorian University", "private", "Gregorian Fdn")
            ]
            assert len(await inst.list_institutions(s, ownership="public")) == 2


async def test_private_schools_still_apply_online_to_asubeb(sm, world):
    tenant, bodies = await _abia(sm, world)
    text = (
        "name,lga,levels,address_line1,town,postcode,ownership\n"
        "TEST Bright Academy,Aba North,Primary,1 Rd,Aba,450001,Private\n"
        "TEST Community School,Aba North,Primary,2 Rd,Aba,450001,\n"
    )
    async with sm() as s, s.begin():
        with at(tenant, bodies["ASUBEB"]):
            job, rows = await inst.get_job(s, await inst.preview_import(s, text, source_name=None))
            assert job.summary["valid"] == 1
            assert "apply online themselves" in rows[0].messages[0]
            with pytest.raises(ValidationFailed, match="apply online themselves"):
                await inst.register_institution(
                    s,
                    {
                        "name": "TEST Other Academy",
                        "lga": "Aba North",
                        "levels": "Primary",
                        "address_line1": "3 Rd",
                        "town": "Aba",
                        "postcode": "450001",
                        "ownership": "Private",
                    },
                )


async def test_government_only_funders(sm, world):
    tenant, bodies = await _abia(sm, world)
    ministry = bodies["MIN-TERTIARY-EDUCATION"]
    async with sm() as s, s.begin():
        await eb.seed_catalogue(s, load_catalogue("ng"))
        with at(tenant, ministry):
            await inst.commit_import(
                s, await inst.preview_import(s, CSV, source_name=None), managing_body="Ministry"
            )
            views = {v.name: v.id for v in await inst.list_institutions(s)}
            await eb.link_institution(
                s, views["TEST Abia State University"], body_code="TETFUND", link_type="beneficiary"
            )
            with pytest.raises(
                ValidationFailed, match="TETFUND covers government-owned institutions only"
            ):
                await eb.link_institution(
                    s,
                    views["TEST Gregorian University"],
                    body_code="TETFUND",
                    link_type="beneficiary",
                )
            # Regulators cover both: NUC accredits private universities too.
            await eb.link_institution(
                s, views["TEST Gregorian University"], body_code="NUC", link_type="accreditation"
            )


async def test_api_shows_and_filters_ownership(client, sm, world):
    tenant, bodies = await _abia(sm, world)
    async with sm() as s, s.begin():
        with at(tenant, bodies["MIN-TERTIARY-EDUCATION"]):
            await inst.commit_import(
                s, await inst.preview_import(s, CSV, source_name=None), managing_body="Ministry"
            )
    from tests.integration.test_private_registration import _officer

    await _officer(sm, tenant, bodies["ASUBEB"])
    login = await client.post(
        "/v1/auth/dev-login", json={"tenant_code": "NG-AB", "login": "officer@abia.example"}
    )
    h = {
        "Authorization": f"Bearer {login.json()['access_token']}",
        "X-Scope-Node-Id": str(bodies["MIN-TERTIARY-EDUCATION"]),
    }
    items = (await client.get("/v1/institutions?ownership=private", headers=h)).json()["items"]
    assert [(i["name"], i["ownership"], i["proprietor"]) for i in items] == [
        ("TEST Gregorian University", "private", "Gregorian Fdn")
    ]
